"""Fake placeholder data for the demo page and the CLI tests.

One row per case the page can show; each name says what the row shows, so nobody
mistakes it for the class.

python -m dashboard demo    # writes out-demo/index.html, no AWS needed
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from .attribution import Creation, LookupResult, Principal
from .collectors.base import Resource
from .config import Config
from .people import IamUser
from .pipeline import ScanData

NOW = datetime(2026, 10, 9, 5, 0, tzinfo=UTC)  # a scheduled run: 07:00 Central European time
ACCT = "111122223333"
EU = "eu-central-1"
US = "us-east-1"
EKS_ARN = f"arn:aws:eks:{EU}:{ACCT}:cluster/eks-cluster-old-kubernetes-version"


def _resource(kind, id, region, name, created, **attrs) -> Resource:
    return Resource(kind, id, region, name, created, {"Name": name}, attrs)


def _launch(instance, region, user, when, zone="", blank=0) -> Creation:
    return Creation(
        f"e-{instance}", "RunInstances", when, region, "instance", instance,
        Principal("user", user), zone, blank,
    )  # fmt: skip


def demo_scan(now: datetime = NOW) -> ScanData:
    day = timedelta(days=1)
    gone_launch = _launch("i-0example0000000a9", EU, "student-b", now - 6 * day, "eu-central-1a", 1)
    detached_launch = _launch("i-0example0000000a8", US, "student-a", now - 5 * day)
    resources = [
        _resource(
            "instance", "i-0example0000000a1", EU, "instance-running-public-ip", now - 2 * day,
            state="running", public_ip=True,
        ),
        _resource(
            "volume", "vol-0example0000000a1", EU, "volume-attached-to-its-instance", now - 2 * day,
            attached_instance="i-0example0000000a1",
            availability_zone="eu-central-1a", from_snapshot=True,
        ),
        _resource(
            "instance", "i-0example0000000a2", EU, "instance-older-than-4-days", now - 6 * day,
            state="stopped", public_ip=False,
        ),
        _resource(
            "eip", "eipalloc-0example00000a1", EU, "eip-older-than-4-days", now - 9 * day,
            nat_gateway_id=None,
        ),
        _resource(
            "volume", "vol-0example0000000a2", EU, "volume-made-with-a-gone-instance",
            gone_launch.event_time, attached_instance=None,
            availability_zone="eu-central-1a", from_snapshot=False,
        ),
        _resource(
            "volume", "vol-0example0000000a3", US, "volume-detached-from-a-gone-instance",
            detached_launch.event_time, attached_instance=None,
            availability_zone="us-east-1a", from_snapshot=True,
        ),
        _resource(
            "volume", "vol-0example0000000a4", US, "volume-no-creation-event", now - 120 * day,
            attached_instance=None,
            availability_zone="us-east-1a", from_snapshot=False,
        ),
        _resource(
            "eks-cluster", EKS_ARN, EU, "eks-cluster-old-kubernetes-version", now - day,
            version="1.32", version_status="EXTENDED_SUPPORT",
        ),
        replace(
            _resource(
                "elb-v2",
                f"arn:aws:elasticloadbalancing:{EU}:{ACCT}:loadbalancer/app/lb-cluster-gone/0123456789abcdef",
                EU, "lb-cluster-gone", now - day,
            ),
            tags={"elbv2.k8s.aws/cluster": "cluster-gone"},
        ),
    ]  # fmt: skip
    creations = [
        _launch("i-0example0000000a1", EU, "student-a", now - 2 * day),
        _launch("i-0example0000000a2", EU, "student-b", now - 6 * day),
        Creation(
            "e-eip", "AllocateAddress", now - 9 * day, EU, "eip", "eipalloc-0example00000a1",
            Principal("user", "student-b"),
        ),
        Creation(
            "e-eks", "CreateCluster", now - day, EU, "eks-cluster", EKS_ARN,
            Principal("user", "student-a"),
        ),
        gone_launch,
        detached_launch,
        Creation(
            "e-detach", "DetachVolume", now - 2 * day, US, "volume-detach", "vol-0example0000000a3",
            Principal("user", "student-a"), instance_id="i-0example0000000a8",
        ),
    ]  # fmt: skip
    users = [IamUser(n, now - 30 * day) for n in ("student-a", "student-b", "student-c")]
    return ScanData(
        generated_at=now,
        regions=[US, EU],
        resources=resources,
        incomplete=[],
        lookups=[
            LookupResult(US, [c for c in creations if c.region == US], duration_s=8.0),
            LookupResult(EU, [c for c in creations if c.region == EU], duration_s=14.2),
        ],
        users=users,
        members={"DV_FT": ["student-a", "student-c"], "DV_PT": ["student-b"]},
        lookback_days=Config().lookback_days,
    )
