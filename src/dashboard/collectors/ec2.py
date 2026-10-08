"""EC2 instances and EBS volumes."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .base import Resource, tags_of

SKIP_STATES = {"terminated"}


def instances(aws: Any, region: str) -> list[Resource]:
    return _instances(aws.client("ec2", region), region)


def volumes(aws: Any, region: str) -> list[Resource]:
    return _volumes(aws.client("ec2", region), region)


def _instances(client: Any, region: str) -> list[Resource]:
    out = []
    for page in client.get_paginator("describe_instances").paginate():
        for reservation in page["Reservations"]:
            for inst in reservation["Instances"]:
                state = inst["State"]["Name"]
                if state in SKIP_STATES:
                    continue
                tags = tags_of(inst.get("Tags"))
                out.append(
                    Resource(
                        kind="instance",
                        id=inst["InstanceId"],
                        region=region,
                        name=tags.get("Name", ""),
                        created_at=_primary_attach_time(inst) or inst.get("LaunchTime"),
                        tags=tags,
                        attrs={
                            "state": state,
                            # Auto-assigned or Elastic IP; a mark for running instances.
                            "public_ip": bool(inst.get("PublicIpAddress")),
                        },
                    )
                )
    return out


def _primary_attach_time(inst: dict) -> datetime | None:
    """LaunchTime resets on every start; the primary ENI's attach time does not."""
    for eni in inst.get("NetworkInterfaces", []):
        attachment = eni.get("Attachment", {})
        if attachment.get("DeviceIndex") == 0:
            return attachment.get("AttachTime")
    return None


def _volumes(client: Any, region: str) -> list[Resource]:
    out = []
    for page in client.get_paginator("describe_volumes").paginate():
        for vol in page["Volumes"]:
            attached = [
                a["InstanceId"]
                for a in vol.get("Attachments", [])
                if a.get("State") in ("attaching", "attached")
            ]
            tags = tags_of(vol.get("Tags"))
            out.append(
                Resource(
                    kind="volume",
                    id=vol["VolumeId"],
                    region=region,
                    name=tags.get("Name", ""),
                    created_at=vol["CreateTime"],
                    tags=tags,
                    attrs={
                        "attached_instance": attached[0] if attached else None,
                        # a blank disk can be matched to the launch that made it
                        "availability_zone": vol.get("AvailabilityZone"),
                        "from_snapshot": bool(vol.get("SnapshotId")),
                    },
                )
            )
    return out
