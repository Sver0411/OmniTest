"""Public adapter API v1. Runtime implementations remain independently versioned."""

from omnitest.adapters.base import Adapter, AdapterInfo, ExecutionContext, Registry
from omnitest.core.ir import Assertion, Model, Step, TestIR

ADAPTER_API_VERSION = 1


def validate_adapter(factory):
    """Check declarations and the async lifecycle without acquiring domain resources."""
    Registry(plugins=False).register(factory)


__all__ = [
    "ADAPTER_API_VERSION",
    "Adapter",
    "AdapterInfo",
    "Assertion",
    "ExecutionContext",
    "Model",
    "Step",
    "TestIR",
    "validate_adapter",
]
