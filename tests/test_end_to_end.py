"""One fake account through the whole chain: scan -> build_report -> render_page.

Every AWS call is a botocore Stubber response in the shape the real API returns, so a
wiring mistake between the modules shows up here, not in the live run.
"""

import dashboard.attribution as attribution
from dashboard.config import Config
from dashboard.pipeline import build_report, scan
from dashboard.render import render_page
from tests.helpers import INSTANCE, NOW, FakeAws, stub_account

R = "us-east-1"


def test_scan_build_report_render_page(monkeypatch):
    monkeypatch.setattr(attribution.RateLimiter, "wait", lambda self: None)  # no real sleeps
    aws = FakeAws()
    stub_account(aws)
    cfg = Config(regions=(R,), cohort_groups={"DV_FT": "DV_FT"}, max_age_days=7)

    data = scan(aws, cfg, NOW)
    aws.assert_done()
    report = build_report(data, roster=None)
    page = render_page(report, cfg)

    assert data.errors == [] and data.incomplete == []
    (row,) = report.rows
    assert (row.resource.id, row.owner.method, row.card, row.cohort) == (
        INSTANCE,
        "event",
        "student-a",
        "DV_FT",
    )
    assert 'id="card-student-a" data-cohort="DV_FT"' in page
    assert f"#InstanceDetails:instanceId={INSTANCE}" in page
    assert '<span class="muted">older than 7 days</span>' in page
