"""Shared test helpers. Everything here is fake: placeholder account 111122223333."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import boto3
from botocore.stub import Stubber

from dashboard.attribution import Creation, LookupResult, Owner, Principal, event_names
from dashboard.collectors.base import Resource
from dashboard.people import Person
from dashboard.pipeline import Card, Report, Row, ScanData

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
NOW = T0 + timedelta(days=1)
ACCOUNT = "111122223333"
FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "events.json").read_text())

STUDENT_A = {
    "type": "IAMUser",
    "principalId": "AIDAEXAMPLEUSERA",
    "arn": f"arn:aws:iam::{ACCOUNT}:user/student-a",
    "accountId": ACCOUNT,
    "userName": "student-a",
}


def event(name: str, **overrides) -> dict:
    """A CloudTrail event detail: defaults + fixture + overrides."""
    base = {
        "eventVersion": "1.10",
        "userIdentity": STUDENT_A,
        "eventTime": "2026-10-01T12:00:00Z",
        "awsRegion": "eu-central-1",
    }
    return base | FIXTURES[name] | overrides


def raw(detail: dict) -> dict:
    """Wrap a detail the way LookupEvents returns it."""
    return {
        "EventId": detail["eventID"],
        "EventName": detail["eventName"],
        "EventTime": datetime.fromisoformat(detail["eventTime"]),
        "CloudTrailEvent": json.dumps(detail),
    }


class FakeAws:
    """Same interface as dashboard.config.Aws, backed by botocore Stubbers."""

    def __init__(self) -> None:
        self._clients: dict[tuple[str, str], object] = {}
        self._stubs: dict[tuple[str, str], Stubber] = {}

    def stub(self, service: str, region: str = "us-east-1") -> Stubber:
        key = (service, region)
        if key not in self._stubs:
            client = boto3.client(
                service,
                region_name=region,
                aws_access_key_id="testing",
                aws_secret_access_key="testing",
            )
            stubber = Stubber(client)
            stubber.activate()
            self._clients[key] = client
            self._stubs[key] = stubber
        return self._stubs[key]

    def client(self, service: str, region: str):
        return self._clients[(service, region)]

    def assert_done(self) -> None:
        for stubber in self._stubs.values():
            stubber.assert_no_pending_responses()


INSTANCE = "i-0example0000000a1"
IAM_USER = {
    "Path": "/",
    "UserName": "student-a",
    "UserId": "AIDAEXAMPLEUSERA",
    "Arn": f"arn:aws:iam::{ACCOUNT}:user/student-a",
    "CreateDate": T0,
}


def group_response(name: str) -> dict:
    """Return a get_group response with student-a as the only member."""
    return {
        "Group": {
            "Path": "/",
            "GroupName": name,
            "GroupId": "AGPAEXAMPLEGROUP1",
            "Arn": f"arn:aws:iam::{ACCOUNT}:group/{name}",
            "CreateDate": T0,
        },
        "Users": [IAM_USER],
    }


def stub_account(aws: FakeAws, region: str = "us-east-1") -> None:
    """Stub every call one full scan makes: one running instance that student-a made."""
    ec2 = aws.stub("ec2", region)
    ec2.add_response(
        "describe_instances",
        {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": INSTANCE,
                            "InstanceType": "t3.micro",
                            "State": {"Name": "running", "Code": 16},
                            "LaunchTime": NOW - timedelta(days=10),
                            "Tags": [{"Key": "Name", "Value": "web"}],
                        }
                    ]
                }
            ]
        },
    )
    ec2.add_response("describe_volumes", {"Volumes": []})
    ec2.add_response("describe_nat_gateways", {"NatGateways": []})
    ec2.add_response("describe_addresses", {"Addresses": []})
    ec2.add_response("describe_network_interfaces", {"NetworkInterfaces": []})
    aws.stub("elb", region).add_response(
        "describe_load_balancers", {"LoadBalancerDescriptions": []}
    )
    aws.stub("elbv2", region).add_response("describe_load_balancers", {"LoadBalancers": []})
    aws.stub("eks", region).add_response("list_clusters", {"clusters": []})
    aws.stub("autoscaling", region).add_response(
        "describe_auto_scaling_groups", {"AutoScalingGroups": []}
    )
    trail = aws.stub("cloudtrail", region)
    created = event("run_instances", eventTime=(NOW - timedelta(days=10)).isoformat())
    trail.add_response("lookup_events", {"Events": [raw(created)]})
    for _ in event_names(include_global=True)[1:]:
        trail.add_response("lookup_events", {"Events": []})
    iam = aws.stub("iam", region)
    iam.add_response("list_users", {"Users": [IAM_USER]})
    iam.add_response("get_group", group_response("DV_FT"), {"GroupName": "DV_FT"})


def res(kind, id, region="eu-central-1", *, name="", created_at=T0, tags=None, **attrs) -> Resource:
    return Resource(
        kind=kind,
        id=id,
        region=region,
        name=name,
        created_at=created_at,
        tags=tags or {},
        attrs=attrs,
    )


def creation(
    kind, id, user="student-a", *, region="eu-central-1", when=T0, principal=None
) -> Creation:
    return Creation(
        event_id=f"e-{kind}-{id}-{when.isoformat()}",
        event_name="Create",
        event_time=when,
        region=region,
        resource_kind=kind,
        resource_id=id,
        principal=principal or Principal("user", user),
    )


def make_scan(resources=(), creations=(), users=(), members=None, failed=(), now=NOW) -> ScanData:
    lookups = [
        LookupResult(
            region=r,
            creations=[c for c in creations if c.region == r],
            failed=r in failed,
            error="AccessDenied" if r in failed else "",
        )
        for r in ("us-east-1", "eu-central-1")
    ]
    return ScanData(
        generated_at=now,
        regions=["us-east-1", "eu-central-1"],
        resources=list(resources),
        incomplete=[],
        lookups=lookups,
        users=list(users),
        members=members or {},
        lookback_days=90,
    )


def make_row(
    resource, user="student-a", cohort="DV_FT", age_days=1, method="event", detail=""
) -> Row:
    principal = Principal("user", user) if user else None
    person = Person(user, user, cohort) if user else None
    return Row(resource, Owner(principal, method, detail), person, age_days)


def make_report(rows=(), cards=None, **overrides) -> Report:
    rows = list(rows)
    if cards is None:
        cards = [Card(r.card, r.cohort, [r]) for r in rows if r.card]
    fields = {
        "generated_at": NOW,
        "regions": ["us-east-1", "eu-central-1"],
        "rows": rows,
        "cards": cards,
        "unknown": [],
        "orphaned": [],
        "incomplete": [],
        "unmapped_users": 0,
        "roster_loaded": True,
        "cohorts": sorted({c.cohort for c in cards if c.cohort}),
    }
    return Report(**(fields | overrides))
