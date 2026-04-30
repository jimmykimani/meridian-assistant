from __future__ import annotations

from meridian_support.catalog.intents import (
    is_show_all_catalog_request,
    should_list_products_shortcut,
)


def test_is_show_all_catalog_request_phrases() -> None:
    assert is_show_all_catalog_request("show all products")
    assert is_show_all_catalog_request("show ALL products !!!!")
    assert is_show_all_catalog_request("  show me all products  ")
    assert is_show_all_catalog_request("full catalog")
    assert is_show_all_catalog_request("list every product")
    assert not is_show_all_catalog_request("show me wireless keyboards")
    assert not is_show_all_catalog_request("")


def test_should_list_products_shortcut_browse_phrases() -> None:
    assert should_list_products_shortcut("show me products")
    assert should_list_products_shortcut("what do you sell")
    assert not should_list_products_shortcut("show me wireless keyboards")
