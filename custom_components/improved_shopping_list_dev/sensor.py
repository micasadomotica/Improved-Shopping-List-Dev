"""Counts, saved-price estimates and receipt history exposed as native sensors."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .entity import ShoppingEntity
from .readers import RESULTS
from .store_links import STORES

SENSORS = (
    SensorEntityDescription(key="shopping_list_products", icon="mdi:cart", state_class=SensorStateClass.MEASUREMENT),
    SensorEntityDescription(key="next_purchase_amount", icon="mdi:cash", device_class=SensorDeviceClass.MONETARY, native_unit_of_measurement="EUR", suggested_display_precision=2),
    SensorEntityDescription(key="catalog_products", icon="mdi:view-grid", state_class=SensorStateClass.MEASUREMENT),
    SensorEntityDescription(key="catalog_categories", icon="mdi:label", state_class=SensorStateClass.MEASUREMENT),
    SensorEntityDescription(key="low_stock_products", icon="mdi:package-variant", state_class=SensorStateClass.MEASUREMENT),
    SensorEntityDescription(key="last_purchase_date", icon="mdi:calendar-clock", device_class=SensorDeviceClass.TIMESTAMP),
    SensorEntityDescription(key="last_purchase_amount", icon="mdi:receipt-text", device_class=SensorDeviceClass.MONETARY, suggested_display_precision=2),
    SensorEntityDescription(key="last_purchase_store", icon="mdi:store"),
    SensorEntityDescription(key="active_readers", icon="mdi:barcode-scan", state_class=SensorStateClass.MEASUREMENT),
    SensorEntityDescription(key="default_store", icon="mdi:heart"),
    SensorEntityDescription(key="active_stores", icon="mdi:store-check", state_class=SensorStateClass.MEASUREMENT),
)


async def async_setup_entry(hass, entry, async_add_entities):
    data = hass.data[DOMAIN]["entity_data"]
    async_add_entities(ShoppingSensor(entry, data, description) for description in SENSORS)
    readers = hass.data[DOMAIN]["readers"]
    added = set()

    @callback
    def add_reader_results():
        new = [reader for reader in readers.snapshot["readers"] if reader["id"] not in added]
        added.update(reader["id"] for reader in new)
        if new:
            async_add_entities(ReaderResultSensor(entry, data, readers, reader) for reader in new)

    add_reader_results()
    entry.async_on_unload(readers.subscribe(add_reader_results))


class ReaderResultSensor(ShoppingEntity, SensorEntity):
    """Outcome of each scan, so a device can show it (for example with an RGB LED)."""

    _attr_icon = "mdi:barcode-scan"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(RESULTS)

    def __init__(self, entry, data, readers, reader):
        super().__init__(entry, data, f"reader_result_{reader['id']}")
        self._attr_translation_key = None
        self._attr_name = f"Resultado {reader['name']}"
        self._readers = readers
        self._reader_id = reader["id"]

    def _reader(self):
        return next((reader for reader in self._readers.snapshot["readers"] if reader["id"] == self._reader_id), None)

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self._readers.subscribe(self._reader_changed))

    @callback
    def _reader_changed(self):
        if self._reader() is None:
            registry = er.async_get(self.hass)
            if self.registry_entry is not None:
                registry.async_remove(self.entity_id)
            return
        self.async_write_ha_state()

    @property
    def available(self):
        return super().available and self._reader() is not None

    @property
    def native_value(self):
        last = (self._reader() or {}).get("last") or {}
        return last.get("result")

    @property
    def extra_state_attributes(self):
        reader = self._reader() or {}
        last = reader.get("last") or {}
        return {
            "reader": reader.get("name"),
            "kind": reader.get("kind"),
            "mode": reader.get("default_action"),
            "code": last.get("code"),
            "product": last.get("product"),
            "message": last.get("message"),
            "time": last.get("time"),
            # Changes on every scan, even when the result repeats (for ESPHome).
            "scan": f'{last["scan"]}:{last["result"]}' if last else None,
        }


class ShoppingSensor(ShoppingEntity, SensorEntity):
    def __init__(self, entry, data, description):
        super().__init__(entry, data, description.key)
        self.entity_description = description

    @property
    def available(self):
        if self.entity_description.key == "shopping_list_products":
            return super().available and self.data.pending is not None
        if self.entity_description.key == "next_purchase_amount":
            return super().available and self.data.estimate is not None
        return super().available

    @property
    def native_value(self):
        data, key = self.data, self.entity_description.key
        purchase = data.last_purchase
        match key:
            case "shopping_list_products": return len(data.pending) if data.pending is not None else None
            case "next_purchase_amount": return data.estimate["cents"] / 100 if data.estimate else None
            case "catalog_products": return len(data.managers["catalog"].products)
            case "catalog_categories": return len(data.managers["catalog"].snapshot["categories"])
            case "low_stock_products": return len(data.low_stock)
            case "last_purchase_date": return purchase["date"] if purchase else None
            case "last_purchase_amount": return purchase["total"] if purchase else None
            case "last_purchase_store": return purchase.get("store_name") or STORES.get(purchase["store"], purchase["store"]) if purchase else None
            case "active_readers": return sum(bool(reader.get("enabled")) for reader in data.managers["readers"].snapshot["readers"])
            case "default_store": return STORES.get(data.settings["favorite_store"])
            case "active_stores": return sum(bool(config.get("enabled")) for config in data.settings["stores"].values())
        return None

    @property
    def native_unit_of_measurement(self):
        if self.entity_description.key == "last_purchase_amount":
            return self.data.last_purchase.get("currency", "EUR") if self.data.last_purchase else "EUR"
        return self.entity_description.native_unit_of_measurement

    @property
    def extra_state_attributes(self):
        key = self.entity_description.key
        if key == "shopping_list_products":
            return {"list_entity": self.data.settings["primary_entity"]}
        if key == "next_purchase_amount":
            return {key: value for key, value in (self.data.estimate or {}).items() if key != "cents"}
        if key == "low_stock_products":
            return {"products": [item["name"] for item in self.data.low_stock]}
        if key.startswith("last_purchase_"):
            return {"receipt_id": self.data.last_purchase["id"]} if self.data.last_purchase else {}
        if key == "default_store":
            return {"store": self.data.settings["favorite_store"] or None}
        if key == "active_stores":
            return {"stores": [store for store, config in self.data.settings["stores"].items() if config.get("enabled")],
                "store_names": sorted(STORES[store] for store, config in self.data.settings["stores"].items() if config.get("enabled") and store in STORES)}
        return None
