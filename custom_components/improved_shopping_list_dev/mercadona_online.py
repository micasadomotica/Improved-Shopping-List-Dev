"""Mercadona online shop (tienda.mercadona.es): unofficial, read-only customer API.

Endpoints observed by community projects, not published by Mercadona:
POST /api/auth/tokens/ with {username,password} or {refresh_token}; GET
/api/customers/{id}/orders/. The password is used once and never stored; only
the renewable refresh token and the customer id persist. Never touches the
cart, addresses, payment cards or checkout.
"""
import json
import re
import uuid

import aiohttp
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .catalog import CatalogError

ROOT = "https://tienda.mercadona.es"
API = ROOT + "/api"
MAX_RESPONSE = 2 * 1024 * 1024
MAX_PAGES = 20
CUSTOMER = re.compile(r"[A-Za-z0-9-]{1,64}")
TOKEN_KEYS = ("access_token", "token", "access")


class MercadonaOnlineError(CatalogError):
    def __init__(self, message, stage, **details):
        super().__init__(message, "mercadona_online_error")
        self.diagnostics = {"stage": stage, **details}


def token(value):
    return value if isinstance(value, str) and 1 <= len(value) <= 20000 and value.isascii() and value.isprintable() and " " not in value else ""


def auth_fields(data):
    """Access token, refresh token and customer id from a tokens response."""
    if not isinstance(data, dict):
        return "", "", ""
    access = next((token(data.get(key)) for key in TOKEN_KEYS if token(data.get(key))), "")
    refresh = token(data.get("refresh_token")) or token(data.get("refresh"))
    customer = data.get("customer_id")
    if customer is None and isinstance(data.get("customer"), dict):
        customer = data["customer"].get("id")
    customer = str(customer) if type(customer) in (str, int) and CUSTOMER.fullmatch(str(customer)) else ""
    return access, refresh, customer


def shape(value, depth=0):
    """Field names and value types only: never values, so no personal data leaves HA."""
    if isinstance(value, dict):
        if depth >= 5:
            return "object"
        return {str(key)[:64]: shape(item, depth + 1) for key, item in list(value.items())[:80]}
    if isinstance(value, list):
        return {"list_length": len(value), "item": shape(value[0], depth + 1) if value else None}
    if value is None:
        return "null"
    return type(value).__name__


class MercadonaOnlineAPI:
    """Bounded first-party requests with the HA client session and no cookie jar."""

    def __init__(self, hass):
        self.hass = hass
        self.device = str(uuid.uuid4())

    async def request(self, method, path, *, body=None, access="", query=None):
        stage = re.sub(r"/customers/[^/]+", "/customers/{id}", path.split("?", 1)[0])
        stage = re.sub(r"/orders/[^/]+", "/orders/{id}", stage)
        if not path.startswith("/") or "//" in path or ".." in path:
            raise MercadonaOnlineError("La ruta de Mercadona no es válida.", "endpoint")
        headers = {"Accept": "application/json", "Origin": ROOT, "Referer": ROOT + "/", "x-customer-device-id": self.device}
        if access:
            headers["Authorization"] = "Bearer " + access
        try:
            async with async_create_clientsession(self.hass, auto_cleanup=False, cookie_jar=aiohttp.DummyCookieJar()) as session:
                async with session.request(method, API + path, json=body, headers=headers, params={"lang": "es", **(query or {})},
                        timeout=aiohttp.ClientTimeout(total=25), allow_redirects=False) as response:
                    status = response.status
                    content = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        content.extend(chunk)
                        if len(content) > MAX_RESPONSE:
                            raise MercadonaOnlineError("La respuesta de Mercadona supera el tamaño permitido.", stage)
                    kind = response.headers.get("Content-Type", "").lower()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise MercadonaOnlineError("No se ha podido conectar con Mercadona. Vuelve a intentarlo más tarde.", stage, response_class="network_error") from err
        if status != 200 and status != 201:
            classification = ("session_expired" if status == 401 else "invalid_credentials" if status in (400, 403) and "tokens" in path
                else "access_denied" if status == 403 else "rate_limited" if status == 429 else "not_found" if status == 404 else "http_error")
            message = ("Mercadona no ha aceptado el correo o la contraseña" if classification == "invalid_credentials"
                else "La sesión de Mercadona ha caducado. Vuelve a conectar la cuenta" if status == 401
                else "Mercadona ha rechazado el acceso desde esta conexión" if status == 403
                else "Mercadona no ha podido completar la consulta")
            raise MercadonaOnlineError(f"{message} (HTTP {status}).", stage, http_status=status, response_class=classification)
        if "json" not in kind:
            raise MercadonaOnlineError("Mercadona ha devuelto una respuesta no reconocida.", stage, response_class="invalid_response")
        try:
            result = json.loads(content)
        except (ValueError, UnicodeError) as err:
            raise MercadonaOnlineError("Mercadona ha devuelto una respuesta no reconocida.", stage, response_class="invalid_response") from err
        if not isinstance(result, (dict, list)):
            raise MercadonaOnlineError("Mercadona ha devuelto una respuesta no reconocida.", stage, response_class="invalid_response")
        return result


def order_page(data):
    """Orders and the next page path of a paginated response."""
    if isinstance(data, list):
        return data, None
    rows = data.get("results")
    if not isinstance(rows, list) or len(rows) > 200:
        raise MercadonaOnlineError("Mercadona no ha devuelto un historial de pedidos reconocido.", "orders_format")
    following = data.get("next_page", data.get("next"))
    if following in (None, "", False):
        return rows, None
    if type(following) is int and 1 <= following <= 1000:
        return rows, following
    if isinstance(following, str):
        match = re.search(r"[?&]page=(\d{1,4})\b", following) or re.fullmatch(r"(\d{1,4})", following)
        if match:
            return rows, int(match[1])
    raise MercadonaOnlineError("La paginación de pedidos de Mercadona no es válida.", "orders_format")
