"""Prometheus API — metrics export."""

from aiopikvm._base_resource import BaseResource


class PrometheusResource(BaseResource):
    """Prometheus metrics export for PiKVM."""

    async def get_metrics(self) -> str:
        """Get Prometheus metrics in text exposition format.

        Returns:
            Prometheus metrics as plain text.
        """
        response = await self._get_raw(
            "/api/export/prometheus/metrics",
            accept="text/plain",
        )
        return response.text
