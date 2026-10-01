from urllib.request import Request

import pytest

from scripts import health_monitor


class FakeResponse:
    """Minimal successful HTTP response for probe tests."""

    status: int = 200

    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def test_probe_checks_remote_api_and_device_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Remote watchdog probes process reachability and cached device state."""
    requested_urls: list[str] = []

    def fake_urlopen(request: Request, timeout: float) -> FakeResponse:
        requested_urls.append(request.full_url)
        assert timeout == 12
        if request.full_url.endswith("/api/health"):
            body = b'{"status":"ok"}'
        elif request.full_url.endswith("/api/health/device"):
            body = b'{"reachable":true,"last_ok_at":"2026-10-02T10:00:00+00:00","last_error_kind":null}'
        else:
            body = b'{"is_on":false}'
        return FakeResponse(body)

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


def test_probe_marks_cached_unreachable_device_unhealthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bodies = iter(
        [
            b'{"status":"ok"}',
            b'{"reachable":false,"last_ok_at":"2026-10-02T09:00:00+00:00","last_error_kind":"TimeoutError"}',
            b'{"is_on":null}',
        ]
    )

    def fake_urlopen(_: Request, timeout: float) -> FakeResponse:
        return FakeResponse(next(bodies))

    monkeypatch.setattr(health_monitor, "urlopen", fake_urlopen)

    results = health_monitor.probe("https://home-automation.example.ts.net", 12)

    assert results["process"]["ok"] is True
    assert results["device_info"]["status"] == 200
    assert results["device_info"]["ok"] is False
    assert results["device_info"]["last_error_kind"] == "TimeoutError"
    assert results["device_status"]["ok"] is False