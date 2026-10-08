"""Shared test helpers. Everything here is fake: placeholder account 111122223333."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import boto3
from botocore.stub import Stubber

from dashboard.attribution import Creation, Principal
from dashboard.collectors.base import Resource

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
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
