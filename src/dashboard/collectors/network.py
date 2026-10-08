"""Elastic IPs, auto-assigned public IPv4 addresses and NAT gateways."""

from __future__ import annotations

import re
from typing import Any

from .base import Resource, tags_of

ACTIVE_NAT_STATES = {"pending", "available"}
_ELB_V2_DESCRIPTION = re.compile(r"ELB ((?:app|net|gwy)/[^/]+/[0-9a-f]+)")
_ELB_CLASSIC_DESCRIPTION = re.compile(r"ELB ([A-Za-z0-9-]+)")


def collect(aws: Any, region: str) -> list[Resource]:
    client = aws.client("ec2", region)
    nats = _nat_gateways(client, region)
    nat_by_allocation = {a: n.id for n in nats for a in n.attrs["eip_allocation_ids"]}
    return nats + _elastic_ips(client, region, nat_by_allocation) + _public_ips(client, region)


def _nat_gateways(client: Any, region: str) -> list[Resource]:
    out = []
    for page in client.get_paginator("describe_nat_gateways").paginate():
        for nat in page["NatGateways"]:
            if nat.get("State") not in ACTIVE_NAT_STATES:
                continue
            tags = tags_of(nat.get("Tags"))
            out.append(
                Resource(
                    kind="nat",
                    id=nat["NatGatewayId"],
                    region=region,
                    name=tags.get("Name", ""),
                    created_at=nat.get("CreateTime"),
                    tags=tags,
                    attrs={
                        "state": nat["State"],
                        "eip_allocation_ids": [
                            a["AllocationId"]
                            for a in nat.get("NatGatewayAddresses", [])
                            if a.get("AllocationId")
                        ],
                    },
                )
            )
    return out


def _elastic_ips(client: Any, region: str, nat_by_allocation: dict[str, str]) -> list[Resource]:
    out = []
    for address in client.describe_addresses()["Addresses"]:
        allocation = address.get("AllocationId")
        if not allocation:
            continue
        tags = tags_of(address.get("Tags"))
        out.append(
            Resource(
                kind="eip",
                id=allocation,
                region=region,
                name=tags.get("Name", ""),
                tags=tags,
                attrs={"nat_gateway_id": nat_by_allocation.get(allocation)},
            )
        )
    return out


def _public_ips(client: Any, region: str) -> list[Resource]:
    """Auto-assigned public IPv4 only; EIP-backed addresses are counted as `eip`."""
    out = []
    for page in client.get_paginator("describe_network_interfaces").paginate():
        for eni in page["NetworkInterfaces"]:
            associations = [p.get("Association") or {} for p in eni.get("PrivateIpAddresses", [])]
            if not associations:
                associations = [eni.get("Association") or {}]
            if not any(a.get("PublicIp") and not a.get("AllocationId") for a in associations):
                continue
            parent_kind, parent_id = _parent_of(eni)
            out.append(
                Resource(
                    kind="public-ip",
                    id=eni["NetworkInterfaceId"],
                    region=region,
                    tags=tags_of(eni.get("TagSet")),
                    attrs={"parent_kind": parent_kind, "parent_id": parent_id},
                )
            )
    return out


def _parent_of(eni: dict) -> tuple[str | None, str | None]:
    instance_id = (eni.get("Attachment") or {}).get("InstanceId")
    if instance_id:
        return "instance", instance_id
    description = eni.get("Description", "")
    if match := _ELB_V2_DESCRIPTION.fullmatch(description):
        return "elb-v2", match.group(1)
    if match := _ELB_CLASSIC_DESCRIPTION.fullmatch(description):
        return "elb-classic", match.group(1)
    return None, None
