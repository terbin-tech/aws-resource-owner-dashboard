from datetime import timedelta

from dashboard.attribution import RateLimiter, lookup_creations
from tests.helpers import T0, FakeAws, event, raw

REGION = "eu-central-1"
START, END = T0 - timedelta(days=90), T0 + timedelta(days=1)


class NoWait:
    def wait(self):
        pass


def _params(name, end=END, token=None):
    params = {
        "LookupAttributes": [{"AttributeKey": "EventName", "AttributeValue": name}],
        "StartTime": START,
        "EndTime": end,
        "MaxResults": 50,
    }
    if token:
        params["NextToken"] = token
    return params


def test_rate_limiter_spaces_calls():
    now = [0.0]
    slept = []

    def sleep(seconds):
        slept.append(round(seconds, 3))
        now[0] += seconds

    limiter = RateLimiter(per_second=2.0, clock=lambda: now[0], sleep=sleep)
    for _ in range(3):
        limiter.wait()
    assert slept == [0.5, 0.5]


def test_follows_pages_and_dedupes_event_ids():
    aws = FakeAws()
    s = aws.stub("cloudtrail", REGION)
    first = raw(event("run_instances"))
    second = raw(
        event(
            "run_instances",
            eventID="e-2",
            responseElements={"instancesSet": {"items": [{"instanceId": "i-0example0000000a2"}]}},
        )
    )
    s.add_response("lookup_events", {"Events": [first], "NextToken": "t1"}, _params("RunInstances"))
    s.add_response(
        "lookup_events", {"Events": [first, second]}, _params("RunInstances", token="t1")
    )
    s.add_response("lookup_events", {"Events": []}, _params("CreateVolume"))

    result = lookup_creations(
        aws.client("cloudtrail", REGION),
        REGION,
        ["RunInstances", "CreateVolume"],
        START,
        END,
        NoWait(),
    )

    aws.assert_done()
    assert [c.resource_id for c in result.creations] == [
        "i-0example0000000a1",
        "i-0example0000000a2",
    ]
    assert result.stats == {"RunInstances:instance": [2, 2]}
    assert result.failed is False


def test_invalid_next_token_restarts_window_at_oldest_event():
    aws = FakeAws()
    s = aws.stub("cloudtrail", REGION)
    first = raw(event("run_instances"))  # EventTime == T0
    s.add_response(
        "lookup_events", {"Events": [first], "NextToken": "stale"}, _params("RunInstances")
    )
    s.add_client_error(
        "lookup_events",
        "InvalidNextTokenException",
        expected_params=_params("RunInstances", token="stale"),
    )
    s.add_response(
        "lookup_events", {"Events": [first]}, _params("RunInstances", end=T0 + timedelta(seconds=1))
    )

    result = lookup_creations(
        aws.client("cloudtrail", REGION), REGION, ["RunInstances"], START, END, NoWait()
    )

    aws.assert_done()
    assert len(result.creations) == 1
    assert result.failed is False


def test_access_denied_mid_paging_marks_failed_and_keeps_partial():
    aws = FakeAws()
    s = aws.stub("cloudtrail", REGION)
    s.add_response(
        "lookup_events",
        {"Events": [raw(event("run_instances"))], "NextToken": "t1"},
        _params("RunInstances"),
    )
    s.add_client_error(
        "lookup_events",
        "AccessDeniedException",
        expected_params=_params("RunInstances", token="t1"),
    )

    result = lookup_creations(
        aws.client("cloudtrail", REGION),
        REGION,
        ["RunInstances", "CreateVolume"],
        START,
        END,
        NoWait(),
    )

    assert result.failed is True
    assert result.error == "AccessDeniedException"
    assert len(result.creations) == 1
