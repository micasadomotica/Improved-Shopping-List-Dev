"""Manual name search in store catalogs, to propose product links for review.

Only an explicit user action runs a search. Results are suggestions: nothing is
linked until the user picks one and its data is retrieved as for a pasted URL.
Stores whose search is protected against automated access are offered as a
link that opens the store's own search in the user's browser instead.
"""
import asyncio
import json
import re
import time
from urllib.parse import quote, quote_plus, urlencode

import aiohttp
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .const import DOMAIN
from .price_sources import PriceError
from .public_catalogs import request as public_request
from .store_availability import AVAILABLE_STORES
from .store_links import STORES, normalize_link
from .store_location import mercadona_location

MAX_RESULTS = 5
MAX_BYTES = 2 * 1024 * 1024
MIN_INTERVAL = 3
# Public anonymous search used by tienda.mercadona.es itself.
MERCADONA_SEARCH = "https://7uzjkl1dj0-dsn.algolia.net/1/indexes/products_prod_{warehouse}_es/query"
MERCADONA_HEADERS = {"x-algolia-application-id": "7UZJKL1DJ0", "x-algolia-api-key": "9d8f2e39e90df472b4f2e559a116fe17"}
SEARCHABLE = ("mercadona", "consum")
BROWSER_SEARCH = {
    "carrefour": "https://www.carrefour.es/?q={plus}",
    "lidl": "https://www.lidl.es/q/search?q={plus}",
    "eroski": "https://supermercado.eroski.es/es/search/results/?q={plus}",
    "lasirena": "https://www.lasirena.es/{path}?_q={plus}&map=ft",
}


class StoreSearchError(ValueError):
    """A search failure safe to show; never carries response bodies."""


def clean_query(value):
    query = " ".join(str(value or "").split())
    if not 2 <= len(query) <= 100:
        raise StoreSearchError("Escribe entre 2 y 100 caracteres para buscar.")
    return query


def _text(value, limit=200):
    return " ".join(str(value or "").split())[:limit]


def _money(value):
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    return round(amount, 2) if 0 < amount <= 100000 else None


def _number(value):
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


def _https(value):
    value = str(value or "")
    return value if value.startswith("https://") and len(value) <= 2048 else ""


def mercadona_candidates(data):
    hits = data.get("hits") if isinstance(data, dict) else None
    if not isinstance(hits, list):
        raise StoreSearchError("Mercadona ha devuelto una búsqueda no válida.")
    result = []
    for hit in hits:
        if not isinstance(hit, dict) or hit.get("published") is False:
            continue
        try:
            link = normalize_link("mercadona", str(hit.get("id", "")))
        except ValueError:
            continue
        price = hit.get("price_instructions") if isinstance(hit.get("price_instructions"), dict) else {}
        size, unit = price.get("unit_size"), _text(price.get("size_format"), 10)
        size_text = f"{_number(float(size))} {unit}" if type(size) in (int, float) and size > 0 and unit else ""
        packaging = _text(hit.get("packaging"), 40)
        reference, reference_unit = _money(price.get("reference_price")), _text(price.get("reference_format"), 10)
        share = _https(hit.get("share_url"))
        result.append({"reference_id": link["id"], "url": share if share.startswith(link["url"]) else link["url"],
            "name": _text(hit.get("display_name")), "brand": _text(hit.get("brand"), 80),
            "format": " · ".join(part for part in (packaging, size_text) if part),
            "price": _money(price.get("unit_price")), "measure": f"{_number(reference)} €/{reference_unit}" if reference and reference_unit else "",
            "image_url": _https(hit.get("thumbnail"))})
        if len(result) >= MAX_RESULTS:
            break
    return result


def consum_candidates(store, data):
    catalog = data.get("catalog") if isinstance(data, dict) else None
    products = catalog.get("products") if isinstance(catalog, dict) else None
    if not isinstance(products, list):
        raise StoreSearchError(f"{STORES[store]} ha devuelto una búsqueda no válida.")
    result = []
    for product in products:
        info = product.get("productData") if isinstance(product, dict) else None
        if not isinstance(info, dict):
            continue
        try:
            link = normalize_link(store, info.get("url", ""))
        except ValueError:
            continue
        if str(product.get("code", "")) != link["id"]:
            continue
        price_data = product.get("priceData") if isinstance(product.get("priceData"), dict) else {}
        prices = price_data.get("prices")
        price = unit_price = None
        for row in prices if isinstance(prices, list) else []:
            value = row.get("value") if isinstance(row, dict) else None
            if isinstance(value, dict):
                price, unit_price = _money(value.get("centAmount")), _money(value.get("centUnitAmount"))
                if row.get("id") == "OFFER_PRICE":
                    break
        unit = _text(price_data.get("unitPriceUnitType"), 20) if unit_price else ""
        name, description = _text(info.get("name")), _text(info.get("description"))
        result.append({"reference_id": link["id"], "url": link["url"], "name": name,
            "brand": _text((info.get("brand") or {}).get("name") if isinstance(info.get("brand"), dict) else "", 80),
            "format": description[len(name):].strip(" -·") if description.startswith(name) else "",
            "price": price, "measure": f"{_number(unit_price)} €/{unit}" if unit_price and unit else "",
            "image_url": _https(info.get("imageURL"))})
        if len(result) >= MAX_RESULTS:
            break
    return result


class StoreSearch:
    def __init__(self, hass):
        self.hass = hass
        self._locks = {store: asyncio.Lock() for store in SEARCHABLE}
        self._next = {}

    def stores(self):
        """Enabled stores with a searchable catalog; Mercadona when none is enabled."""
        settings = self.hass.data.get(DOMAIN, {}).get("settings")
        stores = settings.snapshot["stores"] if settings else {}
        enabled = [store for store in STORES if stores.get(store, {}).get("enabled") and store in AVAILABLE_STORES]
        searchable = [store for store in SEARCHABLE if store in enabled] or ["mercadona"]
        browser = [store for store in BROWSER_SEARCH if store in enabled]
        return searchable, browser

    @staticmethod
    def browser_links(stores, query):
        plus, path = quote_plus(query), quote(query.casefold())
        return [{"store": store, "store_name": STORES[store], "url": BROWSER_SEARCH[store].format(plus=plus, path=path)}
                for store in stores]

    async def _pace(self, store):
        wait = self._next.get(store, 0) - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        self._next[store] = time.monotonic() + MIN_INTERVAL

    async def _mercadona(self, query):
        warehouse = mercadona_location(self.hass).get("warehouse", "")
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,32}", warehouse):
            raise StoreSearchError("Configura el código postal de Mercadona.")
        session = async_create_clientsession(self.hass, auto_cleanup=False, cookie_jar=aiohttp.DummyCookieJar())
        try:
            async with session.post(MERCADONA_SEARCH.format(warehouse=warehouse),
                    headers={**MERCADONA_HEADERS, "Content-Type": "application/json", "Accept": "application/json"},
                    data=json.dumps({"params": urlencode({"query": query, "hitsPerPage": 10})}),
                    timeout=aiohttp.ClientTimeout(total=20), allow_redirects=False) as response:
                if response.status != 200:
                    raise StoreSearchError(f"Mercadona ha rechazado la búsqueda (HTTP {response.status}).")
                raw = await response.content.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise StoreSearchError("La búsqueda de Mercadona es demasiado grande.")
            return mercadona_candidates(json.loads(raw))
        except (ValueError, UnicodeError) as err:
            if isinstance(err, StoreSearchError):
                raise
            raise StoreSearchError("Mercadona ha devuelto una búsqueda no válida.") from err
        finally:
            session.detach()

    async def _consum(self, store, query):
        async def ready():
            return None
        try:
            data = await public_request(self.hass, store, "/api/rest/V1.0/catalog/searcher/products?" +
                urlencode({"q": query, "limit": 10, "offset": 0}), ready)
        except PriceError as err:
            raise StoreSearchError(str(err)) from err
        return consum_candidates(store, data or {})

    async def _search_store(self, store, query):
        async with self._locks[store]:
            await self._pace(store)
            try:
                candidates = await (self._mercadona(query) if store == "mercadona" else self._consum(store, query))
                return {"store": store, "store_name": STORES[store], "candidates": candidates}
            except (StoreSearchError, aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
                message = str(err) if isinstance(err, StoreSearchError) else f"No se ha podido conectar con {STORES[store]}."
                return {"store": store, "store_name": STORES[store], "candidates": [], "error": message}

    async def async_search(self, query):
        query = clean_query(query)
        searchable, browser = self.stores()
        results = await asyncio.gather(*(self._search_store(store, query) for store in searchable))
        return {"query": query, "results": list(results), "browser": self.browser_links(browser, query)}
