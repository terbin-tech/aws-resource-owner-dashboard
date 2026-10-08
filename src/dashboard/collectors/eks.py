"""EKS clusters (with each version's support status) and managed node groups."""

from __future__ import annotations

import logging
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from .base import Resource

log = logging.getLogger(__name__)


def _deleted(exc: ClientError) -> bool:
    """A cluster or node group deleted between List* and Describe*: skipped, not an error."""
    return exc.response.get("Error", {}).get("Code") == "ResourceNotFoundException"


def collect(aws: Any, region: str) -> list[Resource]:
    client = aws.client("eks", region)
    clusters = []
    for page in client.get_paginator("list_clusters").paginate():
        for name in page["clusters"]:
            try:
                clusters.append(client.describe_cluster(name=name)["cluster"])
            except ClientError as exc:
                if not _deleted(exc):
                    raise
    support = _version_status(client, sorted({c["version"] for c in clusters}))
    out = []
    for cluster in clusters:
        out.append(
            Resource(
                kind="eks-cluster",
                id=cluster["arn"],
                region=region,
                name=cluster["name"],
                created_at=cluster.get("createdAt"),
                tags=dict(cluster.get("tags") or {}),
                attrs={
                    "version": cluster["version"],
                    "version_status": support.get(cluster["version"]),
                },
            )
        )
        out.extend(_nodegroups(client, region, cluster))
    return out


def _version_status(client: Any, versions: list[str]) -> dict[str, str | None]:
    """Map each version to its versionStatus.

    A version AWS does not return stays absent, so its cluster shows "support status
    unknown". If the call itself fails, every cluster in the region shows that, and the
    clusters stay.
    """
    if not versions:
        return {}
    out: dict[str, str | None] = {}
    try:
        # Pass clusterVersions alone: the API rejected it together with includeAll. A version
        # missing from the answer shows "support status unknown", which is still marked.
        pages = client.get_paginator("describe_cluster_versions").paginate(clusterVersions=versions)
        for page in pages:
            for entry in page.get("clusterVersions", []):
                out[entry["clusterVersion"]] = entry.get("versionStatus")
    except (BotoCoreError, ClientError) as exc:
        log.warning("DescribeClusterVersions failed: %s", type(exc).__name__)
        return {}
    return out


def _nodegroups(client: Any, region: str, cluster: dict) -> list[Resource]:
    try:
        names = [
            name
            for page in client.get_paginator("list_nodegroups").paginate(
                clusterName=cluster["name"]
            )
            for name in page["nodegroups"]
        ]
    except ClientError as exc:
        if not _deleted(exc):
            raise
        return []
    out = []
    for name in names:
        try:
            group = client.describe_nodegroup(clusterName=cluster["name"], nodegroupName=name)[
                "nodegroup"
            ]
        except ClientError as exc:
            if not _deleted(exc):
                raise
            continue
        out.append(
            Resource(
                kind="eks-nodegroup",
                id=group["nodegroupArn"],
                region=region,
                name=name,
                created_at=group.get("createdAt"),
                tags=dict(group.get("tags") or {}),
                attrs={"cluster_name": cluster["name"]},
            )
        )
    return out
