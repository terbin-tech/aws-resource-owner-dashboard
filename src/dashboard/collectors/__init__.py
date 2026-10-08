"""Read-only inventory collectors.

Each collector is isolated: if one fails, only that service in that region is
marked incomplete and the run continues.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from ..config import per_region
from . import autoscaling, ec2, eks, elb, network
from .base import Incomplete, Resource

log = logging.getLogger(__name__)

# One entry per API family, so one failing call hides as little as possible.
COLLECTORS: tuple[tuple[str, Callable[[Any, str], list[Resource]]], ...] = (
    ("ec2", ec2.instances),
    ("ebs", ec2.volumes),
    ("network", network.collect),
    ("elb", elb.classic),
    ("elbv2", elb.v2),
    ("eks", eks.collect),
    ("autoscaling", autoscaling.collect),
)


def error_code(exc: Exception) -> str:
    if isinstance(exc, ClientError):
        return exc.response.get("Error", {}).get("Code", "ClientError")
    return type(exc).__name__


def run_collectors(aws: Any, regions: Iterable[str]) -> tuple[list[Resource], list[Incomplete]]:
    regions = list(regions)

    def one_region(region: str) -> tuple[list[Resource], list[Incomplete]]:
        found: list[Resource] = []
        gaps: list[Incomplete] = []
        for service, collect in COLLECTORS:
            try:
                found.extend(collect(aws, region))
            except (BotoCoreError, ClientError) as exc:
                log.warning("collector %s failed in %s: %s", service, region, error_code(exc))
                gaps.append(Incomplete(service, region, error_code(exc)))
        return found, gaps

    results = per_region(regions, one_region)
    resources = [r for region in regions for r in results[region][0]]
    incomplete = [g for region in regions for g in results[region][1]]
    return resources, incomplete
