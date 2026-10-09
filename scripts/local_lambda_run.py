"""Run the Lambda code path locally and read-only, to check it before a deploy.

    PYTHONPATH=build/lambda python scripts/local_lambda_run.py --profile dashboard-dev

The same handler.run as the Lambda, imported from the built bundle, against the live
account with the dashboard-dev role. The page goes to out/index.html instead of S3, and
the roster read behaves as "absent" (the dev role has no S3 access). Prints counts only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

import dashboard
from dashboard import handler
from dashboard.config import Aws, Config, load_config


def lambda_env(cfg: Config, bucket: str) -> dict[str, str]:
    """The DASHBOARD_* variables that infra/app sets, built from the local config."""
    return {
        "DASHBOARD_REGIONS": ",".join(cfg.regions),
        "DASHBOARD_MAX_AGE_DAYS": str(cfg.max_age_days),
        "DASHBOARD_LOOKBACK_DAYS": str(cfg.lookback_days),
        "DASHBOARD_COHORT_GROUPS": json.dumps(dict(cfg.cohort_groups)),
        "DASHBOARD_BUCKET": bucket,
    }


class LocalS3:
    """Stands in for the report bucket: no roster, and the page goes to out/."""

    def __init__(self, out: Path) -> None:
        self.out = out
        self.size = 0

    def get_object(self, **_: Any) -> Any:
        raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")

    def put_object(self, *, Key: str, Body: bytes, ContentType: str, **_: Any) -> None:
        if Key != handler.PAGE_KEY or not ContentType.startswith("text/html"):
            raise ValueError("unexpected put_object")
        fd = os.open(self.out / "index.html", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(Body)
        self.size = len(Body)


class LocalAws(Aws):
    def __init__(self, session: Any, s3: LocalS3) -> None:
        super().__init__(session)
        self._s3 = s3

    def client(self, service: str, region: str) -> Any:
        return self._s3 if service == "s3" else super().client(service, region)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="dashboard-dev")
    parser.add_argument("--config", default="dashboard.toml")
    args = parser.parse_args()
    cfg = load_config(args.config if Path(args.config).exists() else None)
    out = Path("out")
    out.mkdir(mode=0o700, exist_ok=True)
    s3 = LocalS3(out)
    bundle = Path(dashboard.__file__).resolve().is_relative_to(Path("build/lambda").resolve())
    aws = LocalAws(boto3.Session(profile_name=args.profile), s3)
    incomplete = handler.run(aws, lambda_env(cfg, "local-check"), datetime.now(UTC))
    print(f"from bundle: {bundle}; incomplete: {incomplete}; page bytes: {s3.size}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
