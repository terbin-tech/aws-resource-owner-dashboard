"""Who created what.

CloudTrail creation events, the identities behind them, indirect owners (via an
instance, a cluster or a launch) and orphans. Stateless: nothing here is stored.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from .collectors import error_code
from .collectors.base import Resource


@dataclass(frozen=True)
class Principal:
    kind: str  # "user" | "root" | "service" | "role" | "other"
    name: str
    verified: bool = True

    @property
    def label(self) -> str:
        return f"role:{self.name}" if self.kind == "role" else self.name


@dataclass(frozen=True)
class Creation:
    event_id: str
    event_name: str
    event_time: datetime
    region: str
    resource_kind: str
    resource_id: str
    principal: Principal
    # RunInstances only: the instance's zone, and how many EBS disks the request asked for
    # without a snapshot. Such disks get no CreateVolume event of their own.
    availability_zone: str = ""
    blank_volumes: int = 0
    # DetachVolume only: the instance the disk was detached from.
    instance_id: str = ""

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.resource_kind, self.region, self.resource_id)


def _get(obj: Any, *path: str) -> Any:
    for part in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(part)
    return obj


def find_key(obj: Any, key: str) -> list[Any]:
    """All values stored under `key` anywhere in `obj` (newer EC2 APIs wrap responses)."""
    found: list[Any] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                found.append(v)
            else:
                found.extend(find_key(v, key))
    elif isinstance(obj, list):
        for v in obj:
            found.extend(find_key(v, key))
    return found


def _from_response(key: str) -> Callable[[dict], list[Any]]:
    return lambda e: find_key(e.get("responseElements"), key)


def _from_request(key: str) -> Callable[[dict], list[Any]]:
    return lambda e: [_get(e, "requestParameters", key)]


@dataclass(frozen=True)
class Rule:
    event_source: str
    event_name: str
    kind: str
    ids: Callable[[dict], list[Any]]
    api_version: str | None = None
    reusable_name: bool = False
    global_only: bool = False


# Order matters only for CreateLoadBalancer without apiVersion: v2 is tried first.
RULES: tuple[Rule, ...] = (
    Rule(
        "ec2.amazonaws.com",
        "RunInstances",
        "instance",
        lambda e: [
            i.get("instanceId") for i in _get(e, "responseElements", "instancesSet", "items") or []
        ],
    ),
    Rule("ec2.amazonaws.com", "CreateVolume", "volume", _from_response("volumeId")),
    # Not a creation: links a kept disk to the instance whose launch made it.
    Rule(
        "ec2.amazonaws.com",
        "DetachVolume",
        "volume-detach",
        lambda e: [
            _get(e, "responseElements", "volumeId") or _get(e, "requestParameters", "volumeId")
        ],
    ),
    Rule("ec2.amazonaws.com", "AllocateAddress", "eip", _from_response("allocationId")),
    Rule("ec2.amazonaws.com", "CreateNatGateway", "nat", _from_response("natGatewayId")),
    Rule(
        "elasticloadbalancing.amazonaws.com",
        "CreateLoadBalancer",
        "elb-v2",
        _from_response("loadBalancerArn"),
        api_version="2015-12-01",
    ),
    Rule(
        "elasticloadbalancing.amazonaws.com",
        "CreateLoadBalancer",
        "elb-classic",
        _from_request("loadBalancerName"),
        api_version="2012-06-01",
        reusable_name=True,
    ),
    Rule(
        "eks.amazonaws.com",
        "CreateCluster",
        "eks-cluster",
        lambda e: [_get(e, "responseElements", "cluster", "arn")],
        reusable_name=True,
    ),
    Rule("eks.amazonaws.com", "CreateNodegroup", "eks-nodegroup", _from_response("nodegroupArn")),
    Rule(
        "autoscaling.amazonaws.com",
        "CreateAutoScalingGroup",
        "asg",
        _from_request("autoScalingGroupName"),
        reusable_name=True,
    ),
    Rule(
        "iam.amazonaws.com",
        "CreateUser",
        "iam-user",
        lambda e: [_get(e, "responseElements", "user", "userName")],
        reusable_name=True,
        global_only=True,
    ),
)

REUSABLE_KINDS = frozenset(rule.kind for rule in RULES if rule.reusable_name)


def event_names(include_regional: bool = True, include_global: bool = False) -> list[str]:
    names: list[str] = []
    for rule in RULES:
        wanted = include_global if rule.global_only else include_regional
        if wanted and rule.event_name not in names:
            names.append(rule.event_name)
    return names


def principal_of(identity: dict) -> Principal:
    kind = identity.get("type")
    if kind == "IAMUser":  # also covers invokedBy CloudFormation/eksctl stacks
        if name := identity.get("userName"):
            return Principal("user", name)
        # No userName: title the card with the ARN's last part, never the whole ARN
        # (it contains the account ID).
        arn = identity.get("arn") or ""
        return Principal("other", arn.rsplit("/", 1)[-1] or "IAMUser", verified=False)
    if kind == "Root":
        return Principal("root", "root")
    if kind == "AWSService":
        return Principal("service", identity.get("invokedBy", "aws-service"))
    if kind == "AssumedRole":
        context = identity.get("sessionContext") or {}
        issuer = context.get("sessionIssuer") or {}
        if "/aws-service-role/" in issuer.get("arn", ""):
            return Principal("service", issuer.get("userName", ""))
        source = context.get("sourceIdentity") or identity.get("sourceIdentity")
        if source:
            # Chosen by the caller unless the role's trust policy enforces
            # sts:SourceIdentity, which this account doesn't.
            return Principal("user", source, verified=False)
        # Session names are chosen by the caller and can be spoofed.
        return Principal("role", issuer.get("userName") or issuer.get("arn", ""), verified=False)
    return Principal("other", identity.get("arn") or kind or "unknown", verified=False)


def parse_event(
    raw: dict, region: str, stats: dict[str, list[int]] | None = None
) -> list[Creation]:
    detail = json.loads(raw["CloudTrailEvent"])
    if detail.get("errorCode"):  # failed calls and dry runs created nothing
        return []
    source, name, version = (
        detail.get("eventSource"),
        detail.get("eventName"),
        detail.get("apiVersion"),
    )
    for rule in RULES:
        if (rule.event_source, rule.event_name) != (source, name):
            continue
        if rule.api_version and version and rule.api_version != version:
            continue
        counts = (
            [0, 0] if stats is None else stats.setdefault(f"{rule.event_name}:{rule.kind}", [0, 0])
        )
        counts[0] += 1
        ids = list(dict.fromkeys(i for i in rule.ids(detail) if isinstance(i, str) and i))
        if not ids:
            continue
        counts[1] += 1
        principal = principal_of(detail.get("userIdentity") or {})
        when = datetime.fromisoformat(detail["eventTime"])
        zones, blank = _launch_details(detail) if rule.kind == "instance" else ({}, 0)
        detached_from = (
            _get(detail, "responseElements", "instanceId")
            or _get(detail, "requestParameters", "instanceId")
            if rule.kind == "volume-detach"
            else ""
        )
        return [
            Creation(
                raw["EventId"],
                rule.event_name,
                when,
                region,
                rule.kind,
                rid,
                principal,
                zones.get(rid, ""),
                blank,
                detached_from if isinstance(detached_from, str) else "",
            )
            for rid in ids
        ]
    return []


def _launch_details(detail: dict) -> tuple[dict[str, str], int]:
    """Each instance's zone, and the EBS disks the request asked for without a snapshot."""
    items = _get(detail, "responseElements", "instancesSet", "items") or []
    zones = {
        i["instanceId"]: zone
        for i in items
        if isinstance(i, dict)
        and isinstance(i.get("instanceId"), str)
        and isinstance(zone := _get(i, "placement", "availabilityZone"), str)
    }
    # A bigger root disk (Terraform root_block_device, the console's storage step) is sent
    # without a snapshot too, but it is the root disk, made from the AMI: not an extra one.
    roots = {i.get("rootDeviceName") for i in items if isinstance(i, dict)}
    mappings = _get(detail, "requestParameters", "blockDeviceMapping", "items") or []
    blank = sum(
        1
        for m in mappings
        if isinstance(m, dict)
        and isinstance(m.get("ebs"), dict)
        and not m["ebs"].get("snapshotId")
        and m.get("deviceName") not in roots
    )
    return zones, blank


LOOKUP_RATE = 1.8  # LookupEvents allows 2/s per account and region; not adjustable
MAX_WINDOW_RESTARTS = 5


class RateLimiter:
    """Keeps one caller at or below `per_second` calls."""

    def __init__(
        self, per_second: float = LOOKUP_RATE, clock=time.monotonic, sleep=time.sleep
    ) -> None:
        self._interval = 1.0 / per_second
        self._clock = clock
        self._sleep = sleep
        self._next: float | None = None

    def wait(self) -> None:
        now = self._clock()
        if self._next is not None and now < self._next:
            self._sleep(self._next - now)
            now = self._next
        self._next = now + self._interval


@dataclass
class LookupResult:
    region: str
    creations: list[Creation] = field(default_factory=list)
    stats: dict[str, list[int]] = field(default_factory=dict)
    failed: bool = False
    error: str = ""
    duration_s: float = 0.0


def lookup_creations(
    client: Any,
    region: str,
    names: list[str],
    start: datetime,
    end: datetime,
    limiter: RateLimiter | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> LookupResult:
    """Look up the creation events named in `names` for one region.

    LookupEvents filters on one attribute, so this runs one query per event name. A
    failure keeps what was read so far and marks the region failed, so its owners show
    `lookup-failed`, not `unknown`.
    """
    limiter = limiter or RateLimiter()
    result = LookupResult(region)
    seen: set[str] = set()
    started = clock()
    try:
        for name in names:
            for item in _events(client, name, start, end, limiter):
                if item["EventId"] in seen:
                    continue
                seen.add(item["EventId"])
                result.creations.extend(parse_event(item, region, result.stats))
    except (BotoCoreError, ClientError) as exc:
        result.failed = True
        result.error = error_code(exc)
    result.duration_s = round(clock() - started, 1)
    return result


def _events(client: Any, name: str, start: datetime, end: datetime, limiter: Any) -> Iterator[dict]:
    token: str | None = None
    window_end = end
    oldest: datetime | None = None
    restarts = 0
    while True:
        limiter.wait()
        kwargs: dict[str, Any] = {
            "LookupAttributes": [{"AttributeKey": "EventName", "AttributeValue": name}],
            "StartTime": start,
            "EndTime": window_end,
            "MaxResults": 50,
        }
        if token:
            kwargs["NextToken"] = token
        try:
            response = client.lookup_events(**kwargs)
        except ClientError as exc:
            if (
                error_code(exc) == "InvalidNextTokenException"
                and oldest
                and restarts < MAX_WINDOW_RESTARTS
            ):
                restarts += 1
                window_end = oldest + timedelta(seconds=1)  # overlap; duplicates dropped by EventId
                token = None
                continue
            raise
        for item in response.get("Events", []):
            if oldest is None or item["EventTime"] < oldest:
                oldest = item["EventTime"]
            yield item
        token = response.get("NextToken")
        if not token:
            return


WINDOW = timedelta(minutes=15)  # reused names: accept only events this close to creation
# A disk made by RunInstances is created within a second of the call (measured: 0-1 s; the
# next launch in the same zone was 114 s away).
LAUNCH_WINDOW = timedelta(seconds=10)
_K8S_MARKERS = ("ebs.csi.aws.com/cluster", "karpenter.sh/nodepool")


@dataclass(frozen=True)
class Owner:
    principal: Principal | None
    method: str  # "event" | "indirect" | "unknown" | "orphaned" | "lookup-failed"
    detail: str = ""
    creation: Creation | None = None
    guess: str = ""


def k8s_cluster_name(tags: dict[str, str]) -> str | None:
    """Return the cluster name of a Kubernetes-created resource.

    "" means it is one but the name is not tagged; None means it is not a Kubernetes
    resource.
    """
    if value := tags.get("elbv2.k8s.aws/cluster"):
        return value
    for key in tags:
        if key.startswith("kubernetes.io/cluster/"):
            return key.removeprefix("kubernetes.io/cluster/")
    if value := tags.get("eks:eks-cluster-name"):
        return value
    if value := tags.get("ebs.csi.aws.com/cluster-name"):
        return value
    if any(marker in tags for marker in _K8S_MARKERS):
        return tags.get("KubernetesCluster", "")
    return None


def direct_creation(
    resource: Resource, by_key: dict[tuple[str, str, str], list[Creation]]
) -> Creation | None:
    candidates = by_key.get(resource.key, [])
    if resource.kind in REUSABLE_KINDS and resource.created_at:
        candidates = [c for c in candidates if abs(c.event_time - resource.created_at) <= WINDOW]
    return min(candidates, key=lambda c: c.event_time, default=None)


DIRECT_KINDS = {"user", "root"}


def attribute(
    resources: list[Resource],
    creations: list[Creation],
    failed_regions: frozenset[str] | set[str] = frozenset(),
    eks_unavailable: frozenset[str] | set[str] = frozenset(),
    *,
    lookback_days: int,
) -> dict[tuple[str, str, str], Owner]:
    return _Attributor(resources, creations, failed_regions, eks_unavailable, lookback_days).run()


class _Attributor:
    def __init__(
        self,
        resources: list[Resource],
        creations: list[Creation],
        failed: frozenset[str] | set[str],
        eks_unavailable: frozenset[str] | set[str],
        lookback_days: int,
    ) -> None:
        self.resources = resources
        self.failed = failed
        self.eks_unavailable = eks_unavailable
        self.lookback_days = lookback_days
        self.by_key: dict[tuple[str, str, str], list[Creation]] = defaultdict(list)
        for c in creations:
            self.by_key[c.key].append(c)
        self.index = {r.key: r for r in resources}
        self.clusters = {(r.region, r.name): r for r in resources if r.kind == "eks-cluster"}
        self.nodegroups = {
            (r.region, r.attrs.get("cluster_name"), r.name): r
            for r in resources
            if r.kind == "eks-nodegroup"
        }
        self.alb_by_suffix = {
            (r.region, r.id.split(":loadbalancer/", 1)[-1]): r
            for r in resources
            if r.kind == "elb-v2"
        }
        self.launches = [c for c in creations if c.event_name == "RunInstances"]
        self.detaches: dict[tuple[str, str], set[str]] = defaultdict(set)
        for c in creations:
            if c.resource_kind == "volume-detach" and c.instance_id:
                self.detaches[(c.region, c.resource_id)].add(c.instance_id)
        self.owners: dict[tuple[str, str, str], Owner] = {}

    def run(self) -> dict[tuple[str, str, str], Owner]:
        for r in self.resources:
            self.resolve(r, frozenset())
        return self.owners

    def resolve(self, r: Resource, stack: frozenset) -> Owner:
        if r.key in self.owners:
            return self.owners[r.key]
        if r.key in stack:
            return Owner(None, "unknown", "attribution cycle")
        stack = stack | {r.key}
        found = direct_creation(r, self.by_key)
        if found and found.principal.kind in DIRECT_KINDS:
            owner = Owner(found.principal, "event", "", found)
        else:
            # Roles (IRSA, Pod Identity, the EKS cluster role) and AWS services act for
            # whoever owns the cluster or group: the tag rules know who.
            owner = (
                self.indirect(r, stack)
                or self.role_owner(found)
                or (None if found else self.detached_from(r, stack) or self.launched_with(r))
                or self.fallback(r, found)
            )
        self.owners[r.key] = owner
        return owner

    def via(self, parent: Resource, stack: frozenset, detail: str) -> Owner | None:
        o = self.resolve(parent, stack)
        if o.principal is None:
            return None
        return Owner(o.principal, "indirect", detail, o.creation)

    def indirect(self, r: Resource, stack: frozenset) -> Owner | None:
        tags = r.tags
        # EKS managed node group or cluster tags
        if cluster := tags.get("eks:cluster-name"):
            nodegroup = tags.get("eks:nodegroup-name")
            if nodegroup and (g := self.nodegroups.get((r.region, cluster, nodegroup))):
                if o := self.via(g, stack, f"via node group {nodegroup}"):
                    return o
            if c := self.clusters.get((r.region, cluster)):
                if o := self.via(c, stack, f"via cluster {cluster}"):
                    return o
            else:
                return self.missing_cluster(r, f"EKS cluster {cluster}")
        # Kubernetes-created resources ("" = Kubernetes, but no cluster name: fall through)
        name = k8s_cluster_name(tags)
        if name:
            if c := self.clusters.get((r.region, name)):
                if o := self.via(c, stack, f"via Kubernetes cluster {name}"):
                    return o
            else:
                return self.missing_cluster(r, f"Kubernetes cluster {name}")
        # Self-managed Auto Scaling group
        if (group := tags.get("aws:autoscaling:groupName")) and (
            a := self.index.get(("asg", r.region, group))
        ):
            if o := self.via(a, stack, f"via Auto Scaling group {group}"):
                return o
        # Volume without its own event -> its instance
        if r.kind == "volume" and (i := r.attrs.get("attached_instance")):
            if (inst := self.index.get(("instance", r.region, i))) and (
                o := self.via(inst, stack, "via its instance")
            ):
                return o
        # EIP on a NAT gateway -> the NAT gateway
        if r.kind == "eip" and (n := r.attrs.get("nat_gateway_id")):
            if (nat := self.index.get(("nat", r.region, n))) and (
                o := self.via(nat, stack, "via its NAT gateway")
            ):
                return o
        # Auto-assigned public IPv4 -> what it is attached to
        if r.kind == "public-ip" and (parent := self.parent_of(r)):
            if o := self.via(parent, stack, f"via its {parent.kind}"):
                return o
        return None

    def detached_from(self, r: Resource, stack: frozenset) -> Owner | None:
        """Give an unattached disk the owner of the instance it was detached from.

        This is for a disk with no event of its own, and the detach is a recorded link.
        Only an instance launched when the disk was made counts: a disk can move to another
        instance, and an old disk (its CreateVolume older than the lookback window) can be
        detached recently. Only if that is one person.
        """
        if (
            r.kind != "volume"
            or r.attrs.get("attached_instance")
            or r.created_at is None
            or r.region in self.failed  # partial data: show lookup-failed, name nobody
        ):
            return None
        owners = []
        for i in sorted(self.detaches.get((r.region, r.id), ())):
            launch = direct_creation(
                Resource("instance", i, r.region, "", None, {}, {}), self.by_key
            )
            if launch is None or abs(launch.event_time - r.created_at) > LAUNCH_WINDOW:
                continue  # not the instance this disk was made with
            if inst := self.index.get(("instance", r.region, i)):
                o = self.via(inst, stack, f"detached from instance {i}")
            elif launch.principal.kind != "service":
                o = Owner(
                    launch.principal, "indirect", "detached from an instance that's gone", launch
                )
            else:
                o = None
            if o is None:
                return None
            owners.append(o)
        if len({o.principal for o in owners}) != 1:
            return None
        return owners[-1]

    def launched_with(self, r: Resource) -> Owner | None:
        """Give an unattached blank disk the owner of the launch that made it.

        This is for a disk with no event of its own: the RunInstances call in its zone,
        within LAUNCH_WINDOW, that asked for a blank disk. Only if one person matches.
        """
        zone = r.attrs.get("availability_zone")
        if (
            r.kind != "volume"
            or r.attrs.get("attached_instance")
            or r.attrs.get("from_snapshot")
            or not zone
            or r.created_at is None
            or r.region in self.failed  # partial data: show lookup-failed, name nobody
        ):
            return None
        # Every launch in the zone and window competes, also one whose request shows no extra
        # disk: a launch template or AMI can hold it.
        hits = [
            c
            for c in self.launches
            if c.region == r.region
            and c.availability_zone == zone
            and abs(c.event_time - r.created_at) <= LAUNCH_WINDOW
        ]
        people = {c.principal for c in hits}
        asked = [c for c in hits if c.blank_volumes]
        if len(people) != 1 or not asked or hits[0].principal.kind == "service":
            return None
        launch = min(asked, key=lambda c: abs(c.event_time - r.created_at))
        detail = (
            f"made with instance {launch.resource_id}"
            if ("instance", r.region, launch.resource_id) in self.index
            else "made with an instance that's gone"
        )
        return Owner(launch.principal, "indirect", detail, launch)

    def missing_cluster(self, r: Resource, what: str) -> Owner:
        if r.region in self.eks_unavailable:  # incomplete, not orphaned
            return Owner(None, "unknown", f"EKS cluster list unavailable in {r.region}")
        return Owner(None, "orphaned", f"{what} no longer exists")

    @staticmethod
    def role_owner(found: Creation | None) -> Owner | None:
        if found and found.principal.kind != "service":
            return Owner(found.principal, "event", "", found)
        return None

    def parent_of(self, r: Resource) -> Resource | None:
        kind, parent_id = r.attrs.get("parent_kind"), r.attrs.get("parent_id")
        if kind == "elb-v2":
            return self.alb_by_suffix.get((r.region, parent_id))
        if kind:
            return self.index.get((kind, r.region, parent_id))
        return None

    def fallback(self, r: Resource, found: Creation | None) -> Owner:
        if r.region in self.failed:
            return Owner(None, "lookup-failed", "CloudTrail lookup failed in this region")
        if found:
            return Owner(None, "unknown", f"created by AWS service {found.principal.name}", found)
        if k8s_cluster_name(r.tags) == "":
            return Owner(None, "unknown", "Kubernetes resource without a cluster name tag")
        return Owner(
            None,
            "unknown",
            f"no creation event in the last {self.lookback_days} days",
            guess=r.tags.get("Name", "") or r.name,
        )
