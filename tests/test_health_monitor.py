from urllib.request import Request

import pytest

from scripts import health_monitor


class FakeResponse:
    """Minimal successful HTTP response for probe tests."""

    status: int = 200

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return b'{"status":"ok"}'


def test_probe_checks_remote_api_and_device_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Remote watchdog probes process reachability and real device reads."""
    requested_urls: list[str] = []

    def fake_urlopen(request: Request, timeout: float) -> FakeResponse:
        requested_urls.append(request.full_url)
        assert timeout == 12
        return FakeResponse()

    monkeypatch.setattr(health_monitor, "urlopen", fake_urlopen)

    results = health_monitor.probe(
        "https://home-automation.example.ts.net", timeout_seconds=12
    )

    assert results["process"]["ok"] is True
    assert results["device_info"]["ok"] is True
    assert results["device_status"]["ok"] is True
    assert requested_urls == [
        "https://home-automation.example.ts.net/api/health",
        "https://home-automation.example.ts.net/api/health/device",
        "https://home-automation.example.ts.net/api/device/status",
    ]