"""Lambda entry point.

Settings come from the DASHBOARD_* variables. The run is the same scan -> build_report ->
render_page chain as the CLI, and its one write is site/index.html.

The response is only {"ok": true}. A failure is logged and re-raised as a generic error,
so no traceback or account data reaches the invoke response. Nothing is kept
between runs.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from .collectors import error_code
from .config import GLOBAL_REGION, Aws, config_from_env
from .people import load_roster_s3
from .pipeline import build_report, scan
from .render import render_page

PAGE_KEY = "site/index.html"
ROSTER_KEY = "config/roster.csv"

log = logging.getLogger(__name__)
# botocore at DEBUG would dump full LookupEvents responses into the logs.
for _name in ("boto3", "botocore", "urllib3"):
    logging.getLogger(_name).setLevel(logging.WARNING)


def make_aws() -> Any:
    import boto3

    return Aws(boto3.Session())


def run(aws: Any, env: Mapping[str, str], now: datetime) -> int:
    """One full run. Returns how many parts were incomplete."""
    cfg = config_from_env(env)
    s3 = aws.client("s3", GLOBAL_REGION)  # the stack's home region
    roster, problem = load_roster_s3(s3, cfg.bucket, ROSTER_KEY)
    if roster is None and problem is None:
        # Not on the page, which students read; a misplaced upload would otherwise go unnoticed.
        log.warning("no roster at %s, using IAM groups", ROSTER_KEY)
    data = scan(aws, cfg, now)
    if problem:
        data.errors.append(problem)
    report = build_report(data, roster)
    s3.put_object(
        Bucket=cfg.bucket,
        Key=PAGE_KEY,
        Body=render_page(report, cfg).encode("utf-8"),
        ContentType="text/html; charset=utf-8",
        CacheControl="no-store",
    )
    return len(report.incomplete)


def handler(event: Any, context: Any) -> dict[str, bool]:
    try:
        incomplete = run(make_aws(), os.environ, datetime.now(UTC))
    except Exception as exc:
        # Type and code only, like the collectors: a botocore message carries the caller ARN
        # with the account ID, and a traceback can carry names.
        log.error(
            "scan failed",
            extra={"error_type": type(exc).__name__, "error_code": error_code(exc)},
        )
        raise RuntimeError("scan failed") from None
    if incomplete:  # the metric filter turns this into IncompleteCount
        log.warning("run incomplete", extra={"incomplete_count": incomplete})
    return {"ok": True}
