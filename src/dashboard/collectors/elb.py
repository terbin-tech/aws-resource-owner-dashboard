"""Classic, application, network and gateway load balancers."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from botocore.exceptions import ClientError

from .base import Resource, tags_of

TAG_BATCH = 20  # DescribeTags accepts at most 20 names/ARNs per call
GONE = {"LoadBalancerNotFound"}  # deleted during the scan


def classic(aws: Any, region: str) -> list[Resource]:
    return _classic(aws.client("elb", region), region)


def v2(aws: Any, region: str) -> list[Resource]:
    return _v2(aws.client("elbv2", region), region)


def _batches(items: list[str]) -> Iterator[list[str]]:
    for i in range(0, len(items), TAG_BATCH):
        yield items[i : i + TAG_BATCH]


def _gone(exc: ClientError) -> bool:
    return exc.response.get("Error", {}).get("Code") in GONE


def _tags(
    describe: Callable[..., dict], param: str, key: str, ids: list[str]
) -> tuple[dict[str, dict[str, str]], set[str]]:
    """Return the tags per load balancer, plus the ones deleted during the scan.

    If a batch fails because one of them is gone, the batch is retried one at a time.
    """
    tags: dict[str, dict[str, str]] = {}
    gone: set[str] = set()
    for batch in _batches(ids):
        try:
            found = describe(**{param: batch})["TagDescriptions"]
        except ClientError as exc:
            if not _gone(exc):
                raise
            found = []
            for one in batch:
                try:
                    found += describe(**{param: [one]})["TagDescriptions"]
                except ClientError as single:
                    if not _gone(single):
                        raise
                    gone.add(one)
        for desc in found:
            tags[desc[key]] = tags_of(desc.get("Tags"))
    return tags, gone


def _classic(client: Any, region: str) -> list[Resource]:
    lbs = [
        lb
        for page in client.get_paginator("describe_load_balancers").paginate()
        for lb in page["LoadBalancerDescriptions"]
    ]
    names = [lb["LoadBalancerName"] for lb in lbs]
    tags, gone = _tags(client.describe_tags, "LoadBalancerNames", "LoadBalancerName", names)
    return [
        Resource(
            kind="elb-classic",
            id=lb["LoadBalancerName"],
            region=region,
            name=lb["LoadBalancerName"],
            created_at=lb.get("CreatedTime"),
            tags=tags.get(lb["LoadBalancerName"], {}),
        )
        for lb in lbs
        if lb["LoadBalancerName"] not in gone
    ]


def _v2(client: Any, region: str) -> list[Resource]:
    lbs = [
        lb
        for page in client.get_paginator("describe_load_balancers").paginate()
        for lb in page["LoadBalancers"]
    ]
    arns = [lb["LoadBalancerArn"] for lb in lbs]
    tags, gone = _tags(client.describe_tags, "ResourceArns", "ResourceArn", arns)
    out = []
    for lb in lbs:
        if lb["LoadBalancerArn"] in gone:
            continue
        out.append(
            Resource(
                kind="elb-v2",
                id=lb["LoadBalancerArn"],
                region=region,
                name=lb["LoadBalancerName"],
                created_at=lb.get("CreatedTime"),
                tags=tags.get(lb["LoadBalancerArn"], {}),
            )
        )
    return out
