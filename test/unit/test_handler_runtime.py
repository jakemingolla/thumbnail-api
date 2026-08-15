"""Unit tests for the Lambda handler runtime cache."""

from thumbnail_api.handlers.runtime import RuntimeCache


def test_get_calls_factory_once_until_reset() -> None:
    cache: RuntimeCache[str] = RuntimeCache()
    calls: list[str] = []

    def factory() -> str:
        calls.append("load")
        return "ready"

    assert cache.get(factory) == "ready"
    assert cache.get(factory) == "ready"
    assert calls == ["load"]

    cache.reset()
    assert cache.get(factory) == "ready"
    assert calls == ["load", "load"]
