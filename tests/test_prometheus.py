"""PrometheusResource tests."""

import httpx
import respx

from aiopikvm import PiKVM

METRICS = """# HELP pikvm_atx_enabled ATX enabled
# TYPE pikvm_atx_enabled gauge
pikvm_atx_enabled 1
"""


async def test_get_metrics(mock_api: respx.MockRouter, client: PiKVM) -> None:
    mock_api.get("/api/export/prometheus/metrics").mock(
        return_value=httpx.Response(
            200, text=METRICS, headers={"Content-Type": "text/plain"}
        )
    )
    result = await client.prometheus.get_metrics()
    assert "pikvm_atx_enabled" in result
    assert isinstance(result, str)
