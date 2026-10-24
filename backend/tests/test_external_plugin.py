import json
from importlib.metadata import EntryPoint

import pytest

from omnitest.adapters.base import ExecutionContext, Registry
from omnitest.core.ir import Step


async def test_installed_independent_package_reads_real_file(stack, tmp_path):
    registry = Registry()
    assert "json-document" in registry.factories, "Install examples/plugins/json_document"
    assert registry.factories["json-document"].__module__ == "omnitest_json_document"
    stack[0].file_root = tmp_path
    (tmp_path / "order.json").write_text(json.dumps({"total": 42}), encoding="utf-8")
    context = ExecutionContext(stack[0], {"config": {"path": "order.json"}}, tmp_path, {})
    adapter = registry.create("json-document")
    try:
        await adapter.check(context)
        assert await adapter.execute(Step(action="read"), context) == {"document": {"total": 42}}
        context.target["config"]["path"] = "../outside.json"
        with pytest.raises(ValueError, match="escapes"):
            await adapter.check(context)
    finally:
        await adapter.close()


def test_broken_and_duplicate_plugins_do_not_replace_or_disable_other_adapters(monkeypatch):
    monkeypatch.setattr(
        "omnitest.adapters.base.entry_points",
        lambda **kwargs: [
            EntryPoint(name="broken", value="builtins:str", group="omnitest.adapters"),
            EntryPoint(
                name="valid",
                value="omnitest_json_document:JsonDocumentAdapter",
                group="omnitest.adapters",
            ),
            EntryPoint(
                name="duplicate",
                value="omnitest_json_document:JsonDocumentAdapter",
                group="omnitest.adapters",
            ),
        ],
    )
    registry = Registry()
    assert {"api", "web", "cli", "json-document"}.issubset(registry.factories)
    assert len(registry.errors) == 2
    assert "AdapterInfo" in registry.errors[0] and "Duplicate" in registry.errors[1]


def test_plugin_requires_asynchronous_contract():
    from omnitest.adapters.base import AdapterInfo

    class Invalid:
        info = AdapterInfo("bad", "1.0", ["read"], [], ["file"])

        def check(self, context):
            return None

    with pytest.raises(ValueError, match="async check"):
        Registry(False).register(Invalid)
