"""One-time, opt-in snapshot of the running original, using Dev stores only."""

import base64
from copy import deepcopy
import logging
import re

from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers.storage import Store

from .const import CONF_TODO_ENTITIES, CONF_TODO_ENTITY, DOMAIN
from .icons import MAX_BYTES
from .stock import materialized_stock

SOURCE_DOMAIN = "improved_shopping_list"
COPY_ORIGINAL = "copy_original"
COPY_COMPLETE = "original_copy_complete"
_LOGGER = logging.getLogger(__name__)
_IMAGE = re.compile(r"[a-f0-9]{64}\.(?:svg|png|jpg|webp)\Z")


class CopyError(ValueError):
    """A snapshot cannot be made without risking existing data."""


def source_for_copy(hass, target):
    """Require a loaded source and a destination outside its selected lists."""
    source = hass.data.get(SOURCE_DOMAIN, {})
    if not source.get("active") or not source.get("settings") or not source["settings"].entry:
        raise CopyError("original_unavailable")
    settings = source["settings"].snapshot
    used = set(settings["entities"])
    for gateway in settings.get("gateway_lists", []):
        used.update((gateway["entity_id"], gateway["target_entity"]))
    if target in used:
        raise CopyError("original_list")
    return source


def _snapshot(source, target):
    """Copy in-memory values without yielding or flushing the original stores."""
    catalog = source["catalog"]
    payload = deepcopy({
        "catalog": {
            "products": catalog._products, "categories": catalog._categories,
            "recipes": catalog._recipes,
            "recommendation_categories": catalog._recommendation_categories,
            "dismissed_suggestions": sorted(catalog._dismissed_suggestions),
            "source_options": catalog._source_options, "reader_action_defaults_version": 1,
        },
        "stock": {"items": source["stock"]._items, "receipts": source["stock"].receipts},
        "price_history": {"entries": source["price_history"]._entries},
        "icon_assets": {"assets": source["icons"]._assets},
        "nutrition": source["readers"].lookup._nutrition_cache,
    })
    for product in payload["catalog"]["products"].values():
        if "recurrence" in product:
            product["recurrence"]["enabled"] = False
    for key, item in payload["stock"]["items"].items():
        item = materialized_stock(item)
        item.update(mode="manual", reorder_enabled=False, list_entity=target,
                    usage_entity="", usage_state="", meal_rules=[], expires_at=0, expired=False)
        payload["stock"]["items"][key] = item
    # Whitelist presentation and shop preferences; never copy accounts, readers,
    # gateway lists, notifications or entity references from the original entry.
    source_options = source["settings"].entry.options
    options = {key: deepcopy(source_options[key]) for key in (
        "appearance", "stores", "favorite_store", "price_update_time", "price_update_days",
    ) if key in source_options}
    options.update({CONF_TODO_ENTITY: target, CONF_TODO_ENTITIES: [target],
                    "price_updates_enabled": False})
    return {"stores": payload, "options": options, "target": target}


def _rewrite_assets(value, filenames):
    """Repoint only integration-owned URLs, including icon metadata dictionary keys."""
    if isinstance(value, dict):
        return {_rewrite_assets(key, filenames): _rewrite_assets(item, filenames)
                for key, item in value.items()}
    if isinstance(value, list):
        return [_rewrite_assets(item, filenames) for item in value]
    if isinstance(value, str):
        prefix = f"/{SOURCE_DOMAIN}_icons/"
        if value.startswith(prefix):
            filename = value[len(prefix):]
            if not _IMAGE.fullmatch(filename):
                raise CopyError("invalid_icon")
            filenames.add(filename)
            return f"/{DOMAIN}_icons/{filename}"
        prefix = f"/{SOURCE_DOMAIN}_static/"
        if value.startswith(prefix):
            return f"/{DOMAIN}_static/{value[len(prefix):]}"
    return value


def _read_icons(directory, filenames):
    result = {}
    root = directory.resolve()
    for filename in filenames:
        path = directory / filename
        if path.is_symlink() or path.resolve().parent != root:
            raise CopyError("invalid_icon")
        with path.open("rb") as icon:
            body = icon.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise CopyError("invalid_icon")
        result[filename] = base64.b64encode(body).decode("ascii")
    return result


def _write_icons(directory, icons):
    directory.mkdir(parents=True, exist_ok=True)
    root = directory.resolve()
    for filename, encoded in icons.items():
        if not _IMAGE.fullmatch(filename):
            raise CopyError("invalid_icon")
        path = directory / filename
        if path.is_symlink() or path.resolve().parent != root:
            raise CopyError("invalid_icon")
        body = base64.b64decode(encoded, validate=True)
        if len(body) > MAX_BYTES:
            raise CopyError("invalid_icon")
        path.write_bytes(body)


def _is_empty(data):
    catalog, stock = data["catalog"], data["stock"]
    return not any((catalog._products, catalog._categories, catalog._recipes,
                    catalog._recommendation_categories, any(catalog._source_options.values()),
                    stock._items, *stock.receipts.values(), data["icons"]._assets,
                    data["price_history"]._entries, data["readers"]._data["readers"],
                    data["meal_routines"]._routines,
                    *(data[key]._session for key in ("carrefour_account", "dia_account", "lidl_account")),
                    data["community_sync"]._data["credentials"]))


async def async_copy_original(hass, entry):
    """Initialize an empty Dev once, resuming the same snapshot after a failed write."""
    if not entry.data.get(COPY_ORIGINAL) or entry.data.get(COPY_COMPLETE):
        return
    target = entry.data[CONF_TODO_ENTITY]
    destination = hass.data[DOMAIN]
    stage = Store(hass, 1, f"{DOMAIN}.copy_stage.{entry.entry_id}", private=True)
    try:
        source = source_for_copy(hass, target)
        saved = await stage.async_load()
        if saved is None:
            if not _is_empty(destination):
                raise CopyError("dev_not_empty")
            filenames = set()
            saved = _rewrite_assets(_snapshot(source, target), filenames)
            saved["icons"] = await hass.async_add_executor_job(
                _read_icons, source["icons"].directory, filenames,
            )
            # Save the entire snapshot before the first destination write. Retry
            # always uses this snapshot, even if the source has changed meanwhile.
            await stage.async_save(saved)
        if saved["target"] != target:
            raise CopyError("destination_changed")
        await hass.async_add_executor_job(_write_icons, destination["icons"].directory, saved["icons"])
        for suffix, payload in saved["stores"].items():
            if suffix not in {"catalog", "stock", "price_history", "icon_assets", "nutrition"}:
                raise CopyError("invalid_snapshot")
            await Store(hass, 1, f"{DOMAIN}.{suffix}").async_save(deepcopy(payload))
        for key in ("catalog", "stock", "price_history", "icons"):
            await destination[key].async_load()
        await destination["readers"].lookup.async_load_nutrition()
        summary = {"products": len(destination["catalog"].products),
                   "stock_items": len(destination["stock"]._items),
                   "purchases": len(destination["stock"].receipts["history"])}
        hass.config_entries.async_update_entry(
            entry, options=deepcopy(saved["options"]),
            data={**entry.data, COPY_COMPLETE: True, "original_copy_summary": summary},
        )
    except (OSError, ValueError, HomeAssistantError) as err:
        raise ConfigEntryNotReady(f"No se ha podido copiar el original a Dev: {err}") from err
    try:
        await stage.async_remove()
    except (OSError, HomeAssistantError):
        _LOGGER.warning("Could not remove the completed Dev copy staging store")
