"""PrometheusResource tests.

The fixture is the exporter's real output, captured from kvmd 4.186.
"""

import httpx
import respx

from aiopikvm import PiKVM
from tests.fixtures import load_text


async def test_get_metrics(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/export/prometheus/metrics").mock(
        return_value=httpx.Response(
            200,
            text=load_text("prometheus_metrics"),
            headers={"Content-Type": "text/plain; charset=utf-8"},
        )
    )
    result = await client.prometheus.get_metrics()
    assert isinstance(result, str)
    assert "pikvm_atx_enabled 0" in result


def test_exporter_emits_no_help_lines() -> None:
    """kvmd writes only ``# TYPE``; the docs used to show ``# HELP`` (#78)."""
    metrics = load_text("prometheus_metrics")
    comments = {line for line in metrics.splitlines() if line.startswith("#")}
    assert comments
    assert all(
        line.startswith("# TYPE ") and line.endswith(" gauge") for line in comments
    )


def test_exporter_covers_only_four_subsystems() -> None:
    """api/export.py reads atx, gpio, health and fan, and nothing else (#78)."""
    prefixes = {
        line.split("_")[1]
        for line in load_text("prometheus_metrics").splitlines()
        if line.startswith("pikvm_")
    }
    assert prefixes <= {"atx", "gpio", "hw", "fan"}
