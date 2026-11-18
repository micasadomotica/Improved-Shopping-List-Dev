"""Administrator-only Mercadona account commands; no credentials in responses or logs."""
import probatio as vol
from homeassistant.components import websocket_api
from homeassistant.exceptions import HomeAssistantError

from .catalog import CatalogError
from .const import DOMAIN
from .mercadona_account import ERRORS

MESSAGES = {"login": "tienda online conectada", "online_disconnect": "tienda online desconectada", "online_orders": "pedidos online consultados",
    "online_diagnostic": "estructura de pedidos online consultada", "mail_connect": "ticket digital conectado",
    "mail_disconnect": "ticket digital desconectado", "disconnect": "cuenta desconectada", "options": "opciones guardadas",
    "purchases": "tickets digitales consultados", "sync_history": "historial incorporado", "preview": "ticket preparado para revisar",
    "preview_saved": "ticket preparado para revisar"}


async def _operation(hass, connection, msg, action):
    data = hass.data.get(DOMAIN, {})
    if not data.get("active"):
        connection.send_error(msg["id"], "not_loaded", "Improved Shopping List Dev no está activa.")
        return
    manager = data["mercadona_account"]
    owner = connection.user.id
    try:
        if action == "status": result = manager.status(owner)
        elif action == "login": result = await manager.async_login(owner, msg["username"], msg["password"])
        elif action == "online_disconnect": result = await manager.async_online_disconnect(owner)
        elif action == "online_orders":
            await manager.async_online_orders(owner)
            result = manager.status(owner)
        elif action == "online_diagnostic": result = await manager.async_online_diagnostic(owner)
        elif action == "mail_connect":
            result = await manager.async_mail_connect(owner, msg["provider"], msg["username"], msg["password"],
                msg.get("host", ""), msg.get("port", 993), msg.get("folder", "INBOX"))
        elif action == "mail_disconnect": result = await manager.async_mail_disconnect(owner)
        elif action == "disconnect": result = await manager.async_disconnect(owner)
        elif action == "options": result = await manager.async_options(owner, msg["options"])
        elif action == "purchases": result = await manager.async_purchases(owner)
        elif action == "sync_history": result = await manager.async_sync_history(owner)
        elif action == "preview": result = await manager.async_preview(owner, msg["purchase_id"])
        else: result = await manager.async_preview_saved(owner, msg["receipt_id"])
        if action in ("preview", "preview_saved"):
            result = await manager.receipts.async_apply_confirmation_policy(result, owner)
    except ERRORS as err:
        data["activity"].record("stores.account", "Mercadona: " + str(err), source="manual", diagnostics={"action": action, **err.diagnostics})
        connection.send_error(msg["id"], err.code, str(err))
    except CatalogError as err:
        connection.send_error(msg["id"], err.code, str(err))
    except (OSError, HomeAssistantError, TimeoutError):
        connection.send_error(msg["id"], "storage_error", "No se ha podido guardar la sesión o preparar el ticket de Mercadona.")
    else:
        if action != "status":
            data["activity"].record("stores.account", "Mercadona: " + MESSAGES[action], source="manual")
        connection.send_result(msg["id"], result)


def _command(action, schema=None):
    @websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/mercadona_account/{action}", **(schema or {})})
    @websocket_api.require_admin
    @websocket_api.async_response
    async def handler(hass, connection, msg):
        await _operation(hass, connection, msg, action)
    handler.__name__ = f"ws_mercadona_{action}"
    return handler


TEXT = vol.All(str, vol.Length(min=1, max=255))
COMMANDS = (
    _command("status"),
    _command("login", {vol.Required("username"): TEXT, vol.Required("password"): TEXT}),
    _command("online_disconnect"),
    _command("online_orders"),
    _command("online_diagnostic"),
    _command("mail_connect", {vol.Required("provider"): vol.In(("gmail", "other")), vol.Required("username"): TEXT,
        vol.Required("password"): vol.All(str, vol.Length(max=255)), vol.Optional("host", default=""): vol.All(str, vol.Length(max=253)),
        vol.Optional("port", default=993): vol.All(int, vol.Range(min=1, max=65535)), vol.Optional("folder", default="INBOX"): vol.All(str, vol.Length(min=1, max=200))}),
    _command("mail_disconnect"),
    _command("disconnect"),
    _command("options", {vol.Required("options"): dict}),
    _command("purchases"),
    _command("sync_history"),
    _command("preview", {vol.Required("purchase_id"): vol.All(str, vol.Length(min=1, max=128))}),
    _command("preview_saved", {vol.Required("receipt_id"): vol.All(str, vol.Length(min=1, max=256))}),
)


def register(hass):
    for command in COMMANDS:
        websocket_api.async_register_command(hass, command)
