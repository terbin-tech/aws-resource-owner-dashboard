import logging
from io import BytesIO

import pytest
from botocore.exceptions import ClientError
from botocore.response import StreamingBody
from botocore.stub import ANY

import dashboard.attribution as attribution
import dashboard.handler as handler_mod
from tests.helpers import FakeAws, stub_account

BUCKET = "example-report-bucket"
ENV = {
    "DASHBOARD_REGIONS": "us-east-1",
    "DASHBOARD_MAX_AGE_DAYS": "7",
    "DASHBOARD_LOOKBACK_DAYS": "90",
    "DASHBOARD_COHORT_GROUPS": '{"DV_FT": "DV_FT"}',
    "DASHBOARD_BUCKET": BUCKET,
}
PUT = {
    "Bucket": BUCKET,
    "Key": "site/index.html",
    "Body": ANY,
    "ContentType": "text/html; charset=utf-8",
    "CacheControl": "no-store",
}


@pytest.fixture
def account(monkeypatch):
    monkeypatch.setattr(attribution.RateLimiter, "wait", lambda self: None)  # no real sleeps
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    aws = FakeAws()
    stub_account(aws)
    monkeypatch.setattr(handler_mod, "make_aws", lambda: aws)
    return aws


def _capture_put(aws):
    captured = {}
    aws.client("s3", "us-east-1").meta.events.register(
        "provide-client-params.s3.PutObject", lambda params, **_: captured.update(params)
    )
    return captured


def test_handler_writes_the_page_and_returns_only_ok(account):
    s3 = account.stub("s3")
    s3.add_client_error(
        "get_object",
        service_error_code="NoSuchKey",
        http_status_code=404,
        expected_params={"Bucket": BUCKET, "Key": "config/roster.csv"},
    )
    s3.add_response("put_object", {}, PUT)
    captured = _capture_put(account)

    assert handler_mod.handler({}, None) == {"ok": True}
    account.assert_done()
    page = captured["Body"].decode("utf-8")
    assert 'id="card-student-a" data-cohort="DV_FT"' in page
    assert 'id="stale" data-generated="' in page


def test_handler_uses_the_roster_from_the_bucket(account, caplog):
    body = b"username,primary_username,cohort\nstudent-a,student-a,DV_PT\n"
    s3 = account.stub("s3")
    s3.add_response("get_object", {"Body": StreamingBody(BytesIO(body), len(body))})
    s3.add_response("put_object", {}, PUT)
    captured = _capture_put(account)

    with caplog.at_level(logging.WARNING, logger="dashboard.handler"):
        handler_mod.handler({}, None)
    page = captured["Body"].decode("utf-8")
    assert 'id="card-student-a" data-cohort="DV_PT"' in page
    assert caplog.records == []


@pytest.mark.parametrize("code,status", [("NoSuchKey", 404), ("AccessDenied", 403)])
def test_missing_roster_logs_one_warning_and_no_incomplete_count(account, caplog, code, status):
    s3 = account.stub("s3")
    s3.add_client_error("get_object", service_error_code=code, http_status_code=status)
    s3.add_response("put_object", {}, PUT)

    with caplog.at_level(logging.WARNING, logger="dashboard.handler"):
        assert handler_mod.handler({}, None) == {"ok": True}
    (record,) = caplog.records
    assert record.getMessage() == "no roster at config/roster.csv, using IAM groups"
    assert not hasattr(record, "incomplete_count")  # must not trip the incomplete alarm


def test_failure_raises_a_generic_error(monkeypatch, caplog):
    for key in ENV:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(handler_mod, "make_aws", FakeAws)
    with pytest.raises(RuntimeError) as exc:
        handler_mod.handler({}, None)
    assert str(exc.value) == "scan failed"
    assert exc.value.__cause__ is None
    assert exc.value.__suppress_context__
    assert "scan failed" in caplog.text


def test_failure_log_has_only_the_error_type_and_code(monkeypatch, caplog):
    arn = "arn:aws:sts::111122223333:assumed-role/example-role/session"

    def boom(aws, env, now):
        error = {"Code": "AccessDenied", "Message": f"User: {arn} is not authorized"}
        raise ClientError({"Error": error}, "GetGroup")

    monkeypatch.setattr(handler_mod, "make_aws", lambda: None)
    monkeypatch.setattr(handler_mod, "run", boom)
    with pytest.raises(RuntimeError, match="^scan failed$"):
        handler_mod.handler({}, None)
    (record,) = [r for r in caplog.records if r.name == "dashboard.handler"]
    assert record.getMessage() == "scan failed"
    assert record.exc_info is None  # no traceback, no exception message
    assert (record.error_type, record.error_code) == ("ClientError", "AccessDenied")
    assert "111122223333" not in caplog.text
    assert "not authorized" not in caplog.text


def test_incomplete_run_logs_one_warning_with_only_the_count(monkeypatch, caplog):
    monkeypatch.setattr(handler_mod, "make_aws", lambda: None)
    monkeypatch.setattr(handler_mod, "run", lambda aws, env, now: 2)
    with caplog.at_level(logging.WARNING, logger="dashboard.handler"):
        assert handler_mod.handler({}, None) == {"ok": True}
    (record,) = caplog.records
    assert record.getMessage() == "run incomplete"
    assert record.incomplete_count == 2


def test_complete_run_logs_nothing(monkeypatch, caplog):
    monkeypatch.setattr(handler_mod, "make_aws", lambda: None)
    monkeypatch.setattr(handler_mod, "run", lambda aws, env, now: 0)
    with caplog.at_level(logging.WARNING):
        handler_mod.handler({}, None)
    assert caplog.records == []


def test_library_loggers_never_below_warning():
    for name in ("boto3", "botocore", "urllib3"):
        assert logging.getLogger(name).level == logging.WARNING
