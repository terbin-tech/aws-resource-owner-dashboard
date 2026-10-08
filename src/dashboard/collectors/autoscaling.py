"""Auto Scaling groups (used for attribution only)."""

from __future__ import annotations

from typing import Any

from .base import Resource


def collect(aws: Any, region: str) -> list[Resource]:
    client = aws.client("autoscaling", region)
    out = []
    for page in client.get_paginator("describe_auto_scaling_groups").paginate():
        for group in page["AutoScalingGroups"]:
            out.append(
                Resource(
                    kind="asg",
                    id=group["AutoScalingGroupName"],
                    region=region,
                    name=group["AutoScalingGroupName"],
                    created_at=group.get("CreatedTime"),
                    tags={t["Key"]: t.get("Value", "") for t in group.get("Tags", [])},
                )
            )
    return out
