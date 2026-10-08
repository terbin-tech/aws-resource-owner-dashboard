"""Command line: python -m dashboard <scan|demo>.

scan  reads AWS (read-only, dashboard-dev profile) and writes out/index.html and
      out/verify.md. Everything else stays in memory: no creator data is saved
      between runs. verify.md is local only: never commit it.
demo  renders fake placeholder data to out-demo/index.html, without AWS.

Both read settings.json; dashboard.toml holds only cohort_groups and roster_path.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from botocore.exceptions import BotoCoreError

from .collectors import error_code
from .config import Aws, ConfigError, load_config
from .demo import demo_scan
from .people import RosterError, load_roster_file
from .pipeline import build_report, check_credentials, scan
from .render import render_page
from .verify import render_verify


def make_aws(profile: str) -> Any:
    import boto3

    return Aws(boto3.Session(profile_name=profile))


def _prepare(out: Path) -> None:
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(out, 0o700)


def _write(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(path, 0o600)
    print(f"wrote {path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dashboard", description="AWS resource owner dashboard (read-only)."
    )
    parser.add_argument("command", choices=["scan", "demo"])
    parser.add_argument("--profile", default=os.environ.get("AWS_PROFILE", "dashboard-dev"))
    parser.add_argument("--config", default=None, help="default: dashboard.toml if it exists")
    parser.add_argument("--out", default=None, help="default: out/ for scan, out-demo/ for demo")
    parser.add_argument(
        "--settings", default="settings.json", help="committed non-account settings"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    if args.config and not Path(args.config).exists():
        parser.error(f"config file not found: {args.config}")
    default = Path("dashboard.toml")
    try:
        cfg = load_config(
            args.config or (str(default) if default.exists() else None),
            settings_path=args.settings,
        )
    except ConfigError as exc:
        parser.error(str(exc))

    # Cheap local checks first: a bad roster or out path must not cost a slow scan.
    try:
        roster = load_roster_file(cfg.roster_path) if args.command == "scan" else None
    except RosterError as exc:
        parser.error(str(exc))
    out = Path(args.out or ("out" if args.command == "scan" else "out-demo"))
    _prepare(out)

    if args.command == "scan":
        try:
            aws = make_aws(args.profile)
            problem = check_credentials(aws)
        except BotoCoreError as exc:  # e.g. ProfileNotFound
            problem = error_code(exc)
        if problem:
            print(
                f"error: no usable AWS credentials for profile {args.profile} ({problem}).\n"
                "Passkey: run 'aws login --profile <login profile>' first. "
                "TOTP: run this in a real terminal and enter the MFA code.",
                file=sys.stderr,
            )
            return 1
        data = scan(aws, cfg, datetime.now(UTC))
    else:
        data = demo_scan(datetime.now(UTC).replace(second=0, microsecond=0))

    report = build_report(data, roster)
    _write(out / "index.html", render_page(report, cfg))
    if args.command == "scan":
        _write(out / "verify.md", render_verify(data, report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
