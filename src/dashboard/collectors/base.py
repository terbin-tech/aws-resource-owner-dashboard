"""The resource model every collector returns."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Resource:
    """One billable or attribution-relevant AWS resource.

    `id` is what CloudTrail creation events are matched on (attribution.RULES).
    `attrs` holds kind-specific facts.
    """

    kind: str
    id: str
    region: str
    name: str = ""
    created_at: datetime | None = None
    tags: dict[str, str] = field(default_factory=dict)
    attrs: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.kind, self.region, self.id)


@dataclass(frozen=True)
class Incomplete:
    service: str
    region: str
    error: str


def tags_of(items: list[dict] | None) -> dict[str, str]:
    return {t["Key"]: t.get("Value", "") for t in items or []}
