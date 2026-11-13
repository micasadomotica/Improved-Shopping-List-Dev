"""Dev must leave production configuration, storage and panel intact."""

from copy import deepcopy

from homeassistant.components import panel_custom
from homeassistant.components.frontend import DATA_PANELS
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.improved_shopping_list_dev.const import DOMAIN, PANEL_PATH


async def test_dev_setup_and_unload_preserve_production(hass, hass_storage):
    original_data = {"catalog": {"sentinel": "original catalog"},
                     "stock": {"sentinel": "original stock"}}
    for suffix, data in original_data.items():
        hass_storage[f"improved_shopping_list.{suffix}"] = {
            "version": 1, "minor_version": 1,
            "key": f"improved_shopping_list.{suffix}", "data": data,
        }
    original_storage = deepcopy(hass_storage)
    production = MockConfigEntry(
        domain="improved_shopping_list", unique_id="improved_shopping_list",
        data={"todo_entity": "todo.home"},
        options={"todo_entities": ["todo.home"], "favorite_store": "lidl"},
    )
    production.add_to_hass(hass)
    original_options = deepcopy(dict(production.options))
    hass.data["improved_shopping_list"] = sentinel = {"active": True}
    hass.states.async_set("todo.home", "7")
    hass.states.async_set("todo.testing", "0")
    await panel_custom.async_register_panel(
        hass, frontend_url_path="improved-shopping-list",
        webcomponent_name="improved-shopping-list-panel",
        sidebar_title="Original", module_url="/original-test.js",
    )
    original_panel = hass.data[DATA_PANELS]["improved-shopping-list"]

    flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"todo_entity": "todo.testing"},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    dev = result["result"]
    assert dev.options["todo_entities"] == ["todo.testing"]
    assert PANEL_PATH in hass.data[DATA_PANELS]
    assert hass.data[DOMAIN]["catalog"].products == []
    # Force a persisted write, not just construction of a different store key.
    await hass.data[DOMAIN]["catalog"]._store.async_save({"products": []})
    assert "improved_shopping_list_dev.catalog" in hass_storage
    assert await hass.config_entries.async_unload(dev.entry_id)
    assert PANEL_PATH not in hass.data[DATA_PANELS]
    assert hass.data[DATA_PANELS]["improved-shopping-list"] is original_panel
    assert hass.data["improved_shopping_list"] is sentinel
    assert dict(production.options) == original_options
    assert hass.states.get("todo.home").state == "7"
    for key in ("improved_shopping_list.catalog", "improved_shopping_list.stock"):
        assert hass_storage[key] == original_storage[key]
