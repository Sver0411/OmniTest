from dataclasses import replace

import pytest

from omnitest.adapters.base import Registry
from omnitest.sdk import ADAPTER_API_VERSION, AdapterInfo, validate_adapter


def test_sdk_contract_version_and_installed_package_inventory():
    from omnitest_json_document import JsonDocumentAdapter

    validate_adapter(JsonDocumentAdapter)
    inventory = Registry().inventory()
    row = next(item for item in inventory["items"] if item["id"] == "json-document")
    assert row["package"]["name"] == "omnitest-json-document"
    assert row["package"]["version"] == "0.2.0"
    assert inventory["api_version"] == row["api_version"] == ADAPTER_API_VERSION == 1


def test_incompatible_plugin_api_rejected_before_initialization():
    class Future:
        info = AdapterInfo("future", "1.0", ["read"], [], ["file"], api_version=2)

        def __init__(self):
            raise AssertionError("Should not acquire resources during validation")

    with pytest.raises(ValueError, match="Unsupported adapter API"):
        validate_adapter(Future)


@pytest.mark.parametrize(
    "patch", [{"id": 12}, {"id": "../bad"}, {"version": 3}, {"capabilities": []}]
)
def test_malformed_plugin_metadata_rejected(patch):
    class Invalid:
        info = replace(AdapterInfo("example", "1.0", ["read"], [], ["file"]), **patch)

    with pytest.raises(ValueError):
        validate_adapter(Invalid)
