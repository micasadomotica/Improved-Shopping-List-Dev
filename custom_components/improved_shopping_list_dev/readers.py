"""Persist readers and process physical scans without an open frontend."""

from asyncio import Lock, gather, sleep, timeout
from copy import deepcopy
from datetime import UTC, datetime
import logging
import time
from uuid import uuid4

from homeassistant.core import HomeAssistant, callback
from homeassistant.components.todo import DATA_COMPONENT
from homeassistant.helpers.event import async_track_state_change_event, async_track_state_report_event
from homeassistant.helpers.storage import Store

from .catalog import READER_ACTIONS, CatalogError, code_key, normalized_name
from .const import DOMAIN
from .todo_names import item_name
from .product_lookup import ProductLookup, is_product_barcode
from .quantity_values import purchase_defaults

_LOGGER = logging.getLogger(__name__)
INVALID_STATES = {"", "unknown", "unavailable", "none"}
MODE_DOMAINS = ("select", "input_select")
# Outcome of a scan, exposed per reader so devices can give visual feedback.
RESULTS = ("ok", "known", "unknown", "created", "repeat", "error")
# Options a mode entity (for example an ESPHome select on the scanner) may use.
# The first label is the one the integration writes back to the device.
MODE_LABELS = {
    "none": ("Sin acción",),
    "add_list": ("Añadir a la lista", "Añadir a la lista de la compra", "Lista"),
    "stock_remove": ("Quitar del stock", "Quitar stock", "Quitar una unidad al stock"),
    "stock_add": ("Añadir al stock", "Añadir stock", "Añadir una unidad al stock"),
    "add_catalog": ("Añadir al catálogo", "Catálogo"),
}


def mode_action(value) -> str | None:
    """Map a mode option (action id or Spanish label) to a reader action."""
    if not isinstance(value, str):
        return None
    key = normalized_name(value)
    for action, labels in MODE_LABELS.items():
        if key == action or any(key == normalized_name(label) for label in labels):
            return action
    return None


def mode_options(state) -> list:
    options = state.attributes.get("options") if state is not None else None
    return options if isinstance(options, list) else []


def normalize_reader(data: dict, hass: HomeAssistant, settings) -> dict:
    fields = {"id", "revision", "name", "kind", "source", "entity_id", "attribute", "device_id", "list_entity", "cooldown", "enabled", "default_action", "mode_entity"}
    if not isinstance(data, dict) or set(data) - fields:
        raise CatalogError("El lector contiene campos no admitidos.")
    result = {}
    for field, limit in (("name", 100), ("entity_id", 255), ("attribute", 100), ("device_id", 100), ("list_entity", 255), ("mode_entity", 255)):
        value = data.get(field, "")
        if not isinstance(value, str) or len(value.strip()) > limit:
            raise CatalogError("Los campos del lector no son válidos.")
        result[field] = value.strip()
    if not result["name"]:
        raise CatalogError("El nombre del lector es obligatorio.")
    result["kind"] = data.get("kind", "barcode")
    result["source"] = data.get("source", "entity")
    if result["kind"] not in ("barcode", "nfc") or result["source"] not in ("entity", "tag"):
        raise CatalogError("Selecciona un tipo y una fuente de lectura válidos.")
    if result["source"] == "entity":
        if hass.states.get(result["entity_id"]) is None:
            raise CatalogError("Selecciona una entidad existente en Home Assistant.")
        result["device_id"] = ""
    else:
        if result["kind"] != "nfc" or not result["device_id"]:
            raise CatalogError("Los eventos NFC necesitan el device_id del lector para distinguirlo de otros dispositivos.")
        result["entity_id"] = result["attribute"] = ""
    if result["list_entity"] and result["list_entity"] not in settings.snapshot["entities"]:
        raise CatalogError("Añade la lista de destino en Ajustes o utiliza la principal.")
    result["cooldown"] = data.get("cooldown", 1 if result["kind"] == "barcode" else 0)
    result["enabled"] = data.get("enabled", True)
    result["default_action"] = data.get("default_action", "none")
    if not isinstance(result["default_action"], str) or result["default_action"] not in READER_ACTIONS:
        raise CatalogError("Selecciona una acción por defecto válida para el lector.")
    if type(result["cooldown"]) is not int or not 0 <= result["cooldown"] <= 3600 or not isinstance(result["enabled"], bool):
        raise CatalogError("La protección antirrepetición debe estar entre 0 y 3600 segundos (0 la desactiva).")
    if result["mode_entity"]:
        state = hass.states.get(result["mode_entity"])
        if result["mode_entity"].split(".", 1)[0] not in MODE_DOMAINS or state is None:
            raise CatalogError("La entidad de modo debe ser un selector (select o input_select) existente.")
        options = mode_options(state)
        if state.state not in ("unknown", "unavailable") and (
            not options or any(mode_action(option) is None for option in options)
        ):
            raise CatalogError("Todas las opciones de la entidad de modo deben ser acciones del lector: "
                               + ", ".join(labels[0] for labels in MODE_LABELS.values()) + ".")
        if options and not any(mode_action(option) == result["default_action"] for option in options):
            raise CatalogError("La entidad de modo no incluye la acción por defecto elegida.")
    return result


class ReaderManager:
    def __init__(self, hass, catalog, settings):
        self.hass, self.catalog, self.settings = hass, catalog, settings
        self._store = Store(hass, 1, f"{DOMAIN}.readers")
        self._data = {"readers": {}, "pending": {}, "dismissed": [], "cooldowns": {}}
        self._lock = Lock()
        self._operation_lock = Lock()
        self._listeners = set()
        self._last = {}
        self._scans = 0
        self._captures = {}
        self._tasks = set()
        self._unsubscribers = []
        self._active = False
        self.lookup = ProductLookup(hass)
        self._lookup_pending = set()
        self.activity = None
        self.stock = None
        self.quantities = None

    async def async_load(self):
        await self.lookup.async_load()
        data = await self._store.async_load()
        if data is not None:
            if not isinstance(data, dict) or any(not isinstance(data.get(key), dict) for key in ("readers", "pending", "cooldowns")) or not isinstance(data.get("dismissed"), list):
                raise CatalogError("No se han podido leer los lectores guardados.", "storage_error")
            self._data = data
            for reader in self._data["readers"].values():
                reader.setdefault("default_action", "none")
                reader.setdefault("mode_entity", "")

    @property
    def snapshot(self):
        return {
            "readers": [{**deepcopy(reader), "last": deepcopy(self._last.get(reader["id"]))} for reader in sorted(self._data["readers"].values(), key=lambda item: item["name"].casefold())],
            "pending": [{**deepcopy(item), "identification": self._identification(item)} for item in self._data["pending"].values() if self.catalog.find_by_code(item["kind"], item["code"]) is None],
        }

    def _identification(self, item):
        if item["kind"] != "barcode" or not is_product_barcode(item["code"]):
            return {"status": "unsupported"}
        return self.lookup.cached(item["code"]) or {"status": "pending"}

    @callback
    def _schedule_lookup(self, item):
        if item["id"] in self._lookup_pending or item["kind"] != "barcode" or not is_product_barcode(item["code"]) or self.lookup.is_fresh(item["code"]):
            return
        self._lookup_pending.add(item["id"])
        if self._spawn(self._identify(item), background=True) is None:
            self._lookup_pending.discard(item["id"])

    async def _identify(self, item):
        try:
            if item["id"] in self._data["pending"] and self.catalog.find_by_code(item["kind"], item["code"]) is None:
                await self.lookup.async_lookup(item["code"])
                if self._active:
                    self._notify()
        except Exception:
            _LOGGER.exception("Could not identify a pending product")
        finally:
            self._lookup_pending.discard(item["id"])

    @callback
    def subscribe(self, listener):
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    @callback
    def _notify(self):
        for listener in tuple(self._listeners):
            listener()

    @callback
    def capture(self, reader_id, listener):
        """Temporarily route fresh physical scans to one product editor only."""
        reader = self._data["readers"].get(reader_id)
        if not self._active or not reader or not reader["enabled"]:
            raise CatalogError("Selecciona un lector activo.")
        if reader_id in self._captures:
            raise CatalogError("Este lector ya está capturando códigos en otra ficha.", "conflict")
        capture = {"listener":listener}
        self._captures[reader_id] = capture

        @callback
        def stop():
            if self._captures.get(reader_id) is capture:
                self._captures.pop(reader_id)
                capture["timer"].cancel()

        @callback
        def expire():
            if self._captures.get(reader_id) is capture:
                stop()
                listener({"status":"expired"})

        capture["stop"] = stop
        capture["timer"] = self.hass.loop.call_later(60, expire)
        return stop

    @callback
    def _cancel_capture(self, reader_id):
        if capture := self._captures.get(reader_id):
            capture["stop"]()
            capture["listener"]({"status":"cancelled"})

    async def _persist(self, data):
        await self._store.async_save(data)
        self._data = data

    @callback
    def _status(self, reader_id, code, message, product=None, *, result):
        """Record the outcome of a scan. result: ok, known, unknown, created, repeat or error."""
        self._scans += 1
        self._last[reader_id] = {"code": code, "message": message, "product": product, "time": datetime.now(UTC).isoformat(),
                                 "result": result, "scan": self._scans}
        if self.activity:
            reader = self._data["readers"].get(reader_id, {})
            self.activity.record("reader.scan", message, source="reader", reader=reader.get("name", reader_id),
                                 product=product or "", code=code, list_entity=reader.get("list_entity") or self.settings.snapshot["primary_entity"])
        self._notify()

    async def async_save_reader(self, data):
        reader = normalize_reader(data, self.hass, self.settings)
        async with self._lock:
            reader_id = data.get("id")
            if reader_id is not None and not isinstance(reader_id, str):
                raise CatalogError("Identificador de lector no válido.")
            current = self._data["readers"].get(reader_id)
            if reader_id and current is None:
                raise CatalogError("El lector ya no existe.", "not_found")
            if current and data.get("revision") != current["revision"]:
                raise CatalogError("Otra sesión ha cambiado el lector. Vuelve a abrirlo.", "conflict")
            if current and "default_action" not in data:
                reader["default_action"] = current.get("default_action", "none")
            if current and "mode_entity" not in data:
                reader["mode_entity"] = current.get("mode_entity", "")
            for other in self._data["readers"].values():
                if other["id"] == reader_id:
                    continue
                if reader["mode_entity"] and reader["mode_entity"] == other.get("mode_entity"):
                    raise CatalogError("Esa entidad de modo ya la utiliza otro lector.")
                if reader["source"] == other["source"] and (reader["entity_id"] if reader["source"] == "entity" else reader["device_id"]) == (other["entity_id"] if other["source"] == "entity" else other["device_id"]):
                    raise CatalogError("Ya hay un lector configurado con esa fuente.")
            reader.update({"id": reader_id or str(uuid4()), "revision": current["revision"] + 1 if current else 1})
            updated = deepcopy(self._data)
            updated["readers"][reader["id"]] = reader
            await self._persist(updated)
        self._listen_sources()
        self._cancel_capture(reader["id"])
        self._notify()
        await self._push_mode(reader)
        return deepcopy(reader)

    @callback
    def _mode(self, reader):
        """The reader action now selected on its mode entity, or its stored default."""
        if reader.get("mode_entity"):
            state = self.hass.states.get(reader["mode_entity"])
            if state is not None and (action := mode_action(state.state)):
                return action
        return reader.get("default_action", "none")

    async def _push_mode(self, reader):
        """Show a default action changed in the panel on the device's mode selector."""
        entity_id = reader.get("mode_entity")
        state = self.hass.states.get(entity_id) if entity_id else None
        if state is None or state.state in ("unknown", "unavailable") or mode_action(state.state) == reader["default_action"]:
            return
        option = next((option for option in mode_options(state) if mode_action(option) == reader["default_action"]), None)
        if option is None:
            return
        try:
            await self.hass.services.async_call(entity_id.split(".", 1)[0], "select_option",
                                                {"entity_id": entity_id, "option": option}, blocking=True)
        except Exception:  # The reader is saved; the device catches up when it reports its mode.
            _LOGGER.warning("Could not update the mode entity %s", entity_id, exc_info=True)

    @callback
    def _mode_event(self, event):
        state = event.data.get("new_state")
        if state is not None and (action := mode_action(state.state)):
            self._spawn(self._apply_mode(state.entity_id, action))

    async def _apply_mode(self, entity_id, action):
        """Adopt a mode chosen on the device (button) as the reader's default action."""
        changed = False
        try:
            async with self._lock:
                updated = deepcopy(self._data)
                for reader in updated["readers"].values():
                    if reader.get("mode_entity") == entity_id and reader["default_action"] != action:
                        reader.update({"default_action": action, "revision": reader["revision"] + 1})
                        changed = True
                if changed:
                    await self._persist(updated)
        except OSError:
            _LOGGER.exception("Could not store the mode selected on %s", entity_id)
            return
        if changed:
            self._notify()

    async def async_delete_reader(self, reader_id, revision):
        async with self._lock:
            current = self._data["readers"].get(reader_id)
            if not current or current["revision"] != revision:
                raise CatalogError("El lector ha cambiado o ya no existe. Actualiza la vista.", "conflict")
            updated = deepcopy(self._data)
            del updated["readers"][reader_id]
            await self._persist(updated)
        self._last.pop(reader_id, None)
        self._cancel_capture(reader_id)
        self._listen_sources()
        self._notify()

    async def async_dismiss(self, pending_id):
        async with self._lock:
            item = self._data["pending"].get(pending_id)
            if item is None:
                return
            updated = deepcopy(self._data)
            key = f'{item["kind"]}:{code_key(item["kind"], item["code"])}'
            if key not in updated["dismissed"]:
                updated["dismissed"].append(key)
            del updated["pending"][pending_id]
            await self._persist(updated)
        self._notify()

    @callback
    def start(self):
        self._active = True
        self._catalog_unsubscribe = self.catalog.subscribe(self._catalog_changed)
        self._listen_sources()
        # The device keeps its mode while Home Assistant restarts: adopt it.
        for entity_id in {reader["mode_entity"] for reader in self._data["readers"].values() if reader.get("mode_entity")}:
            state = self.hass.states.get(entity_id)
            if state is not None and (action := mode_action(state.state)):
                self._spawn(self._apply_mode(entity_id, action))
        for item in self._data["pending"].values():
            if self.catalog.find_by_code(item["kind"], item["code"]) is None:
                self._schedule_lookup(item)

    async def async_stop(self):
        self._active = False
        for reader_id in tuple(self._captures):
            self._cancel_capture(reader_id)
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers = []
        if getattr(self, "_catalog_unsubscribe", None):
            self._catalog_unsubscribe()
            self._catalog_unsubscribe = None
        for task in tuple(self._tasks):
            task.cancel()
        await gather(*self._tasks, return_exceptions=True)
        self._lookup_pending.clear()

    @callback
    def _spawn(self, coroutine, *, background=False):
        if not self._active or len(self._tasks) >= 100:
            coroutine.close()
            return
        task = (self.hass.async_create_background_task(coroutine, "Improved Shopping List Dev product lookup")
                if background else self.hass.async_create_task(coroutine))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    @callback
    def _catalog_changed(self):
        self._notify()
        self._spawn(self._prune_pending())

    async def _prune_pending(self):
        try:
            async with self._lock:
                keep = {key: item for key, item in self._data["pending"].items() if self.catalog.find_by_code(item["kind"], item["code"]) is None}
                if len(keep) != len(self._data["pending"]):
                    updated = deepcopy(self._data)
                    updated["pending"] = keep
                    await self._persist(updated)
        except OSError:
            _LOGGER.exception("Could not remove resolved scan suggestions")

    @callback
    def _listen_sources(self):
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers = []
        if not self._active:
            return
        entities = sorted({reader["entity_id"] for reader in self._data["readers"].values() if reader["enabled"] and reader["source"] == "entity"})
        if entities:
            self._unsubscribers.append(async_track_state_change_event(self.hass, entities, self._entity_event))
            self._unsubscribers.append(async_track_state_report_event(self.hass, entities, self._entity_report_event))
        if any(reader["enabled"] and reader["source"] == "tag" for reader in self._data["readers"].values()):
            self._unsubscribers.append(self.hass.bus.async_listen("tag_scanned", self._tag_event))
        modes = sorted({reader["mode_entity"] for reader in self._data["readers"].values() if reader.get("mode_entity")})
        if modes:
            self._unsubscribers.append(async_track_state_change_event(self.hass, modes, self._mode_event))

    @callback
    def _entity_event(self, event):
        old, state = event.data.get("old_state"), event.data.get("new_state")
        self._entity_reading(old, state)

    @callback
    def _entity_report_event(self, event):
        # HA reports identical values separately from state changes. Only a new
        # report timestamp represents a new scan; never replay a cached state.
        reported = event.data.get("last_reported")
        previous = event.data.get("old_last_reported")
        if reported is None or previous is None or reported <= previous:
            return
        state = event.data.get("new_state")
        self._entity_reading(state, state)

    @callback
    def _entity_reading(self, old, state):
        # Never execute a stale value restored when an entity first appears.
        if old is None or state is None or state.state in ("unknown", "unavailable") or state.attributes.get("restored"):
            return
        for reader in self._data["readers"].values():
            if reader["source"] != "entity" or not reader["enabled"] or reader["entity_id"] != state.entity_id:
                continue
            attribute = reader["attribute"]
            code = state.attributes.get(attribute) if attribute else state.state
            previous = old.attributes.get(attribute) if attribute else old.state
            # Attribute-only updates are not new scans; event entities change their timestamp.
            if code == previous and state.state == old.state and state.attributes != old.attributes:
                continue
            self._spawn(self.async_process(reader["id"], code))

    @callback
    def _tag_event(self, event):
        for reader in self._data["readers"].values():
            if reader["enabled"] and reader["source"] == "tag" and reader["device_id"] == event.data.get("device_id"):
                self._spawn(self.async_process(reader["id"], event.data.get("tag_id")))

    async def async_process(self, reader_id, code):
        if not isinstance(code, str) or not code.strip() or len(code) > 1024 or any(ord(char) < 32 and char not in "\r\n\t" for char in code):
            return
        code = code.strip()
        pending_id = None
        if any(ord(char) < 32 for char in code):
            return
        if code.casefold() in INVALID_STATES:
            return
        try:
            async with self._lock:
                reader = deepcopy(self._data["readers"].get(reader_id))
                if not self._active or not reader or not reader["enabled"]:
                    return
                if capture := self._captures.get(reader_id):
                    capture["listener"]({"status":"scanned", "reader_id":reader_id, "kind":reader["kind"], "code":code})
                    # Feedback for the device only: no activity record for a capture.
                    self._scans += 1
                    self._last[reader_id] = {"code": code, "message": "Código capturado en la ficha del producto", "product": None,
                                             "time": datetime.now(UTC).isoformat(), "result": "ok", "scan": self._scans}
                    self._notify()
                    return  # No list action, cooldown write, lookup or unknown-code suggestion.
                product = self.catalog.find_by_code(reader["kind"], code)
                mode = self._mode(reader)
                now = time.time()
                code_id = f'{reader["kind"]}:{code_key(reader["kind"], code)}'
                keys = [f"{reader_id}:code:{code_id}"]
                if product:
                    keys.append(f'{reader_id}:product:{product["id"]}')
                if any(now - self._data["cooldowns"].get(key, 0) < reader["cooldown"] for key in keys):
                    self._status(reader_id, code, "Lectura repetida ignorada", product["name"] if product else None, result="repeat")
                    return
                updated = deepcopy(self._data)
                updated["cooldowns"] = {key: timestamp for key, timestamp in updated["cooldowns"].items() if now - timestamp < 3600}
                updated["cooldowns"].update({key: now for key in keys})
                if not product and mode == "add_catalog" and code_id in updated["dismissed"]:
                    # Scanning in catalogue mode is an explicit request to register it again.
                    updated["dismissed"].remove(code_id)
                if not product and code_id not in updated["dismissed"]:
                    existing = next((item for item in updated["pending"].values() if item["kind"] == reader["kind"] and code_key(item["kind"], item["code"]) == code_key(reader["kind"], code)), None)
                    pending_id = existing["id"] if existing else str(uuid4())
                    updated["pending"][pending_id] = {"id": pending_id, "kind": reader["kind"], "code": code, "reader_id": reader_id, "reader_name": reader["name"], "time": datetime.now(UTC).isoformat()}
                await self._persist(updated)  # Persist the guard before any provider mutation.
            if mode == "add_catalog":
                if product is not None:
                    self._status(reader_id, code, "Ya está en el catálogo", product["name"], result="known")
                else:
                    await self._register(reader, code)
                return
            if product is None:
                self._status(reader_id, code, "Código desconocido en Sugerencias" if code_id not in self._data["dismissed"] else "Código desconocido descartado", result="unknown")
                if pending_id and (pending := self._data["pending"].get(pending_id)):
                    self._schedule_lookup(pending)
                return
            action = product.get("reader_actions", {}).get(reader_id, mode)
            if action == "none":
                self._status(reader_id, code, "Sin acción configurada para este producto y lector", product["name"], result="known")
            elif action in ("stock_remove", "stock_add"):
                if self.stock is None:
                    raise CatalogError("El stock no está disponible.")
                def still_current():
                    matched = self.catalog.find_by_code(reader["kind"], code)
                    return self._active and self._data["readers"].get(reader_id) == reader and matched is not None and matched["id"] == product["id"] and matched["revision"] == product["revision"]
                result = await self.stock.async_change_product(product, 1 if action == "stock_add" else -1,
                    reader=reader["name"], guard=still_current)
                self._status(reader_id, code, f'Stock: {result["quantity"]} unidades', product["name"], result="ok")
            elif action == "add_list":
                async with self._operation_lock:
                    await self._add_to_list(reader, product, code)
        except Exception:  # Provider and storage failures must never become unhandled tasks.
            _LOGGER.exception("Could not process reader %s", reader_id)
            self._status(reader_id, code, "No se pudo completar la lectura. Comprueba el lector, la lista o el registro.", result="error")

    async def _register(self, reader, code):
        """Catalogue mode: create the product from public data, or leave it pending."""
        unknown = "Etiqueta NFC" if reader["kind"] == "nfc" else "Código"
        hint = await self.lookup.async_lookup(code) if reader["kind"] == "barcode" and is_product_barcode(code) else {}
        name = hint.get("name", "").strip() if hint.get("status") == "found" and isinstance(hint.get("name"), str) else ""
        if not name:
            self._notify()
            self._status(reader["id"], code, f"{unknown} sin datos: complétalo en Catálogo → Productos escaneados", result="unknown")
            return
        async with self._operation_lock:
            if not self._active or self._data["readers"].get(reader["id"], {}).get("enabled") is not True:
                return
            if (product := self.catalog.find_by_code(reader["kind"], code)) is not None:
                self._status(reader["id"], code, "Ya está en el catálogo", product["name"], result="known")
                return
            if any(normalized_name(item["name"]) == normalized_name(name) for item in self.catalog.products):
                self._status(reader["id"], code, f"Ya existe «{name}»: asígnale el código desde Productos escaneados", name, result="unknown")
                return
            try:
                product = await self.catalog.async_save_product({"name": name, "barcodes": [code]})
            except CatalogError as err:
                self._status(reader["id"], code, f"No se ha podido añadir al catálogo: {err}", name, result="error")
                return
        self._status(reader["id"], code, "Añadido al catálogo", product["name"], result="created")

    def _still_current(self, reader, product, code, target):
        current = self._data["readers"].get(reader["id"])
        matched = self.catalog.find_by_code(reader["kind"], code)
        if not self._active or current != reader or not matched or matched["revision"] != product["revision"]:
            raise CatalogError("La configuración cambió durante la lectura.")
        if target not in self.settings.snapshot["entities"]:
            raise CatalogError("La lista ya no está añadida a la integración.")

    async def _add_to_list(self, reader, product, code):
        target = reader["list_entity"] or self.settings.snapshot["primary_entity"]
        self._still_current(reader, product, code, target)
        state = self.hass.states.get(target)
        if state is None or state.state in ("unknown", "unavailable"):
            raise CatalogError("La lista de destino no está disponible.")
        result = await self.hass.services.async_call("todo", "get_items", {"entity_id": target}, blocking=True, return_response=True)
        items = result.get(target, {}).get("items") if isinstance(result, dict) else None
        if not isinstance(items, list):
            raise CatalogError("No se ha podido comprobar el contenido de la lista.")
        matches = [item for item in items if normalized_name(item_name(self.settings, target, item)) == normalized_name(product["name"])]
        pending = next((item for item in matches if item.get("status") != "completed"), None)
        if pending:
            if not pending.get("uid") or self.quantities is None:
                raise CatalogError("No se puede aumentar la cantidad del producto en la lista.")
            result = await self.quantities.async_adjust(target, str(pending["uid"]), 1,
                reader=reader["name"], guard=lambda: self._still_current(reader, product, code, target),
                scan_quantity=product.get("scan_quantity", purchase_defaults(product)["unit_quantity"]))
            value = result["entries"][target][str(pending["uid"])]
            self._status(reader["id"], code, f"Cantidad en la lista: {value['quantity']} {value['unit']}", product["name"], result="ok")
            return
        self._still_current(reader, product, code, target)
        state = self.hass.states.get(target)
        if state is None or state.state in ("unknown", "unavailable"):
            raise CatalogError("La lista de destino no está disponible.")
        features = int(state.attributes.get("supported_features", 0))
        completed = next((item for item in matches if item.get("uid") and item.get("status") == "completed"), None)
        context = self.activity.reader_context(reader["name"]) if self.activity else None
        if completed and features & 4:
            await self.hass.services.async_call("todo", "update_item", {"entity_id": target, "item": completed["uid"], "status": "needs_action"}, blocking=True, context=context)
        elif features & 1:
            await self.hass.services.async_call("todo", "add_item", {"entity_id": target, "item": product["name"]}, blocking=True, context=context)
        else:
            raise CatalogError("La lista de destino no permite añadir o recuperar el producto.")
        # Confirm the provider's UID before storing a first-scan amount. Never resend
        # add_item on a delayed provider response (Bring may refresh asynchronously).
        if self.quantities is not None:
            pending = await self._confirm_pending_item(reader, product, code, target)
            await self.quantities.async_adjust(target, str(pending["uid"]), 1,
                reader=reader["name"], guard=lambda: self._still_current(reader, product, code, target),
                scan_quantity=product.get("scan_quantity", purchase_defaults(product)["unit_quantity"]), reset=True)
        self._status(reader["id"], code, f"Añadido a {state.attributes.get('friendly_name', target)}", product["name"], result="ok")

    async def _confirm_pending_item(self, reader, product, code, target):
        try:
            async with timeout(30):
                for delay in (0, 1, 4, 6):
                    if delay:
                        await sleep(delay)
                    self._still_current(reader, product, code, target)
                    state = self.hass.states.get(target)
                    if state is None or state.state in ("unknown", "unavailable"):
                        break
                    if delay:
                        component = self.hass.data.get(DATA_COMPONENT)
                        entity = component.get_entity(target) if component else None
                        if entity is not None:
                            await entity.async_update_ha_state(force_refresh=True)
                    result = await self.hass.services.async_call("todo", "get_items", {"entity_id": target}, blocking=True, return_response=True)
                    items = result.get(target, {}).get("items") if isinstance(result, dict) else None
                    if isinstance(items, list):
                        pending = next((item for item in items if item.get("uid") and item.get("status") == "needs_action"
                            and normalized_name(item_name(self.settings, target, item)) == normalized_name(product["name"])), None)
                        if pending:
                            return pending
        except TimeoutError:
            pass
        raise CatalogError("El proveedor no ha confirmado el artículo. Comprueba la lista y su cantidad antes de volver a escanear.")
