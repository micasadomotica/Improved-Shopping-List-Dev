"""The opt-in copy must never share or overwrite production state."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.storage import Store
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.improved_shopping_list_dev.catalog import Catalog
from custom_components.improved_shopping_list_dev.const import DOMAIN
from custom_components.improved_shopping_list_dev.dev_copy import (
    COPY_COMPLETE, COPY_ORIGINAL, SOURCE_DOMAIN, async_copy_original,
)
from custom_components.improved_shopping_list_dev.stock import StockManager


@pytest.fixture
async def original(hass, tmp_path):
    hass.config.config_dir = str(tmp_path)
    catalog = Catalog(hass)
    catalog._store = Store(hass, 1, f"{SOURCE_DOMAIN}.catalog")
    filename = "a" * 64 + ".svg"
    url = f"/{SOURCE_DOMAIN}_icons/{filename}"
    await catalog.async_save_product({"name": "Aguacate", "image_url": url, "price": 1.01})
    product = catalog.products[0]
    stock = StockManager(hass, catalog)
    stock._store = Store(hass, 1, f"{SOURCE_DOMAIN}.stock")
    await stock.async_add("Aguacate")
    item = next(iter(stock._items.values()))
    item.update(reorder_enabled=True, list_entity="todo.home")
    stock.receipts["history"]["purchase"] = {
        "id": "purchase", "store": "mercadona", "purchased_at": "2026-11-12T12:00:00+00:00",
        "total": 1.01, "lines": [{"id": "line", "product_id": product["id"], "amount": 1.01}],
    }
    entry = MockConfigEntry(domain=SOURCE_DOMAIN, data={"todo_entity": "todo.home"}, options={
        "todo_entities": ["todo.home"], "favorite_store": "lidl",
        "stores": {"lidl": {"enabled": True}}, "appearance": {"columns": 4},
        "price_updates_enabled": True, "gateway_lists": [],
        "notifications": {"sentinel": "must not copy"},
        "purchase_detection": {"enabled": True},
    })
    entry.add_to_hass(hass)
    settings = SimpleNamespace(entry=entry, snapshot={"entities": ["todo.home"], "gateway_lists": []})
    directory = tmp_path / "original_icons"
    directory.mkdir()
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M1 1h4v4z"/></svg>'
    (directory / filename).write_bytes(svg)
    data = {"active": True, "catalog": catalog, "stock": stock, "settings": settings,
            "icons": SimpleNamespace(directory=directory, _assets={url: {"url": url, "id": "mdi:food"}}),
            "price_history": SimpleNamespace(_entries=[]),
            "readers": SimpleNamespace(lookup=SimpleNamespace(_nutrition_cache={})),
            "lidl_account": {"token": "test-session-not-to-copy"}}
    hass.data[SOURCE_DOMAIN] = data
    hass.states.async_set("todo.home", "5")
    hass.states.async_set("todo.test", "0")
    return data


async def make_dev(hass, *, copy=False):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN,
                            data={"todo_entity": "todo.test", COPY_ORIGINAL: copy})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_copy_is_independent_persistent_and_only_once(hass, hass_storage, original):
    before_catalog = deepcopy(original["catalog"].snapshot)
    before_stock = deepcopy(original["stock"]._items)
    before_options = deepcopy(dict(original["settings"].entry.options))
    before_storage = deepcopy(hass_storage[f"{SOURCE_DOMAIN}.catalog"])
    entry = await make_dev(hass, copy=True)
    dev = hass.data[DOMAIN]
    assert entry.data[COPY_COMPLETE]
    assert entry.data["original_copy_summary"] == {"products": 1, "stock_items": 1, "purchases": 1}
    assert entry.options["todo_entities"] == ["todo.test"]
    assert not entry.options["price_updates_enabled"]
    assert entry.options["appearance"]["columns"] == 4
    assert entry.options["favorite_store"] == "lidl"
    assert "notifications" not in entry.options and "purchase_detection" not in entry.options
    product = dev["catalog"].products[0]
    assert product["id"] == original["catalog"].products[0]["id"]
    assert product["image_url"].startswith(f"/{DOMAIN}_icons/")
    filename = product["image_url"].rsplit("/", 1)[1]
    assert (dev["icons"].directory / filename).read_bytes() == (original["icons"].directory / filename).read_bytes()
    item = next(iter(dev["stock"]._items.values()))
    assert item["mode"] == "manual" and not item["reorder_enabled"]
    assert item["list_entity"] == "todo.test"
    assert dev["stock"].receipts == original["stock"].receipts
    assert dev["stock"].receipts is not original["stock"].receipts
    assert original["catalog"].snapshot == before_catalog
    assert original["stock"]._items == before_stock
    assert dict(original["settings"].entry.options) == before_options
    assert hass_storage[f"{SOURCE_DOMAIN}.catalog"] == before_storage
    assert hass.states.get("todo.home").state == "5"
    # A Dev edit and a later source edit must stay independent across reloads.
    await dev["catalog"].async_save_product({"name": "Solo Dev"})
    await original["catalog"].async_save_product({"name": "Solo original"})
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert {p["name"] for p in dev["catalog"].products} == {"Aguacate", "Solo Dev"}
    assert {p["name"] for p in original["catalog"].products} == {"Aguacate", "Solo original"}


async def test_copy_refuses_existing_dev_data(hass, original):
    entry = await make_dev(hass)
    await hass.data[DOMAIN]["catalog"].async_save_product({"name": "Keep me"})
    assert await hass.config_entries.async_unload(entry.entry_id)
    hass.config_entries.async_update_entry(entry, data={**entry.data, COPY_ORIGINAL: True})
    with pytest.raises(ConfigEntryNotReady, match="dev_not_empty"):
        await async_copy_original(hass, entry)
    assert [p["name"] for p in hass.data[DOMAIN]["catalog"].products] == ["Keep me"]


@pytest.mark.parametrize("kind", ["reader", "routine", "carrefour_account", "dia_account", "lidl_account", "community"])
async def test_copy_refuses_previous_dev_connections(hass, original, kind):
    entry = await make_dev(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    dev = hass.data[DOMAIN]
    if kind == "reader":
        dev["readers"]._data["readers"]["existing"] = {}
    elif kind == "routine":
        dev["meal_routines"]._routines["existing"] = {}
    elif kind == "community":
        dev["community_sync"]._data["credentials"] = {"access_token": "test"}
    else:
        dev[kind]._session = {"access_token": "test"}
    hass.config_entries.async_update_entry(entry, data={**entry.data, COPY_ORIGINAL: True})
    with pytest.raises(ConfigEntryNotReady, match="dev_not_empty"):
        await async_copy_original(hass, entry)
    assert not entry.data.get(COPY_COMPLETE)
    assert dev["catalog"].products == []


async def test_failed_write_resumes_the_staged_snapshot(hass, original, monkeypatch):
    entry = await make_dev(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    hass.config_entries.async_update_entry(entry, data={**entry.data, COPY_ORIGINAL: True})
    real_save = Store.async_save
    async def fail_stock(self, data):
        if self.key == f"{DOMAIN}.stock":
            raise OSError("disk full")
        return await real_save(self, data)
    with monkeypatch.context() as patch:
        patch.setattr(Store, "async_save", fail_stock)
        with pytest.raises(ConfigEntryNotReady, match="disk full"):
            await async_copy_original(hass, entry)
    assert not entry.data.get(COPY_COMPLETE)
    await original["catalog"].async_save_product({"name": "Created after snapshot"})
    await async_copy_original(hass, entry)
    assert entry.data[COPY_COMPLETE]
    assert [p["name"] for p in hass.data[DOMAIN]["catalog"].products] == ["Aguacate"]


async def test_copy_flow_rejects_the_original_list(hass, original):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {
        "todo_entity": "todo.home", COPY_ORIGINAL: True,
    })
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "original_list"}


async def test_copy_flow_requires_loaded_original(hass):
    hass.states.async_set("todo.test", "0")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {
        "todo_entity": "todo.test", COPY_ORIGINAL: True,
    })
    assert result["errors"] == {"base": "original_unavailable"}


async def test_copy_rejects_unsafe_local_icon_before_writing(hass, original, hass_storage):
    product = next(iter(original["catalog"]._products.values()))
    product["image_url"] = f"/{SOURCE_DOMAIN}_icons/../secrets.yaml"
    entry = await make_dev(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    hass.config_entries.async_update_entry(entry, data={**entry.data, COPY_ORIGINAL: True})
    with pytest.raises(ConfigEntryNotReady, match="invalid_icon"):
        await async_copy_original(hass, entry)
    assert hass.data[DOMAIN]["catalog"].products == []
    assert f"{DOMAIN}.copy_stage.{entry.entry_id}" not in hass_storage
