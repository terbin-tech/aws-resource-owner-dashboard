import os
import re
import stat
from datetime import UTC, datetime, timedelta

import pytest
from botocore.exceptions import ProfileNotFound

import dashboard.__main__ as cli
from dashboard.config import Config
from dashboard.demo import demo_scan
from dashboard.pipeline import build_report
from dashboard.render import checks
from tests.helpers import FakeAws


def _config(tmp_path, text=""):
    path = tmp_path / "dashboard.toml"
    path.write_text(text)
    return str(path)


def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def test_demo_writes_only_the_page_with_private_modes(tmp_path):
    out = tmp_path / "out-demo"
    assert cli.main(["demo", "--out", str(out), "--config", _config(tmp_path)]) == 0
    assert sorted(os.listdir(out)) == ["index.html"]
    page = (out / "index.html").read_text()
    assert "<title>Forgotten AWS resources</title>" in page
    assert "$" not in page
    assert '<strong class="check">Check this:</strong>' in page
    assert _mode(out) == 0o700
    assert _mode(out / "index.html") == 0o600


def test_scan_writes_page_and_verify_report_and_nothing_else(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "make_aws", lambda profile: object())
    monkeypatch.setattr(cli, "check_credentials", lambda aws: None)
    monkeypatch.setattr(cli, "scan", lambda aws, cfg, now: demo_scan())
    out = tmp_path / "out"
    assert cli.main(["scan", "--out", str(out), "--config", _config(tmp_path)]) == 0
    assert sorted(os.listdir(out)) == ["index.html", "verify.md"]  # who created what is never saved
    assert "# Verification report" in (out / "verify.md").read_text()
    assert _mode(out / "verify.md") == 0o600


def test_demo_data_uses_placeholders_only():
    data = demo_scan()
    assert all(
        "111122223333" in r.id or "example" in r.id or r.kind == "asg" for r in data.resources
    )
    assert {u.name for u in data.users} <= {"student-a", "student-b", "student-c"}


def test_explicit_config_that_does_not_exist_is_an_error(tmp_path):
    with pytest.raises(SystemExit) as exc:
        cli.main(["demo", "--out", str(tmp_path / "o"), "--config", str(tmp_path / "nope.toml")])
    assert exc.value.code == 2
    assert not (tmp_path / "o").exists()


def test_out_dir_parents_are_created(tmp_path):
    out = tmp_path / "a" / "b" / "out-demo"
    assert cli.main(["demo", "--out", str(out), "--config", _config(tmp_path)]) == 0
    assert (out / "index.html").exists()


def test_bad_roster_fails_before_any_aws_call(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "make_aws", lambda profile: calls.append(profile))
    roster = tmp_path / "roster.csv"
    roster.write_text("username\nstudent-a\n")
    cfg = _config(tmp_path, f'roster_path = "{roster}"\n')
    with pytest.raises(SystemExit) as exc:
        cli.main(["scan", "--out", str(tmp_path / "out"), "--config", cfg])
    assert exc.value.code == 2
    assert calls == []


def test_bad_config_fails_before_any_aws_call(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "make_aws", lambda profile: calls.append(profile))
    cfg = _config(tmp_path, "regions = []\n")
    with pytest.raises(SystemExit) as exc:
        cli.main(["scan", "--out", str(tmp_path / "out"), "--config", cfg])
    assert exc.value.code == 2
    assert calls == []


def test_expired_credentials_stop_before_the_scan(tmp_path, monkeypatch, capsys):
    aws = FakeAws()
    aws.stub("sts", "us-east-1").add_client_error(
        "get_caller_identity", service_error_code="ExpiredToken", http_status_code=403
    )
    monkeypatch.setattr(cli, "make_aws", lambda profile: aws)
    monkeypatch.setattr(cli, "scan", lambda aws, cfg, now: pytest.fail("scan must not run"))
    out = tmp_path / "out"
    assert cli.main(["scan", "--out", str(out), "--config", _config(tmp_path)]) == 1
    assert "ExpiredToken" in capsys.readouterr().err
    assert not (out / "index.html").exists()


def test_unknown_profile_stops_before_the_scan(tmp_path, monkeypatch, capsys):
    def no_profile(profile):
        raise ProfileNotFound(profile=profile)

    monkeypatch.setattr(cli, "make_aws", no_profile)
    cfg = _config(tmp_path)
    assert cli.main(["scan", "--out", str(tmp_path / "out"), "--config", cfg]) == 1
    assert "ProfileNotFound" in capsys.readouterr().err


def test_missing_settings_file_is_an_error(tmp_path):
    with pytest.raises(SystemExit) as exc:
        cli.main(
            [
                "demo",
                "--out",
                str(tmp_path / "o"),
                "--config",
                _config(tmp_path),
                "--settings",
                str(tmp_path / "nope.json"),
            ]
        )
    assert exc.value.code == 2


def test_demo_page_is_dated_now_so_it_is_never_stale(tmp_path):
    out = tmp_path / "o"
    before = datetime.now(UTC).replace(second=0, microsecond=0)
    assert cli.main(["demo", "--out", str(out), "--config", _config(tmp_path)]) == 0
    stamp = re.search(r'data-generated="([^"]+)"', (out / "index.html").read_text()).group(1)
    assert datetime.fromisoformat(stamp) >= before - timedelta(minutes=1)


def test_demo_shows_each_case_with_a_name_that_says_what_it_is():
    data = demo_scan()
    report = build_report(data, None)
    details = {r.owner.detail for r in report.rows}
    assert {
        "via its instance",
        "made with an instance that's gone",
        "detached from an instance that's gone",
    } <= details
    assert [r.owner.detail for r in report.unknown] == ["no creation event in the last 90 days"]
    assert len(report.orphaned) == 1
    assert any(not c.rows for c in report.cards)  # an all-clear card
    reasons = {why for r in report.rows for why in checks(r, Config())}
    assert reasons == {
        "older than 4 days",
        "running with a public IP",
        "old Kubernetes version 1.32, costs extra every hour",
    }
    for r in data.resources:  # each name says which case the row shows
        assert r.name.startswith(("instance-", "volume-", "eip-", "lb-", "eks-")), r.name
    assert data.generated_at == datetime(
        2026, 10, 9, 5, 0, tzinfo=UTC
    )  # 07:00 Berlin time, the morning run
