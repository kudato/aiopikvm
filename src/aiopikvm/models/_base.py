"""Base model for all PiKVM API responses."""

from pydantic import BaseModel, ConfigDict


class _Base(BaseModel):
    """Base model with ``extra="allow"`` for forward-compatible parsing."""

    model_config = ConfigDict(extra="allow")
