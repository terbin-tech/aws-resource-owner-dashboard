"""Configuration and AWS client plumbing.

Non-account values (regions, max age, lookback) live in the committed settings.json,
which Terraform reads too. Config's defaults repeat them for the tests, and a test
checks that the two agree.
Account values stay out of the repo: the gitignored dashboard.toml (cohort_groups,
roster_path) for local runs, DASHBOARD_* environment variables for the Lambda.
"""

from __future__ import annotations

import json
import os
import re
import threading
import tomllib
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from botocore.config import Config as BotoConfig

DEFAULT_REGIONS: tuple[str, ...] = ("us-east-1", "eu-central-1")
# CloudTrail records IAM events such as CreateUser in us-east-1. It is also the
# stacks' home region.
GLOBAL_REGION = "us-east-1"
SETTINGS_FILE = "settings.json"
SETTINGS_KEYS = frozenset({"regions", "max_age_days", "lookback_days"})
LOCAL_KEYS = frozenset({"cohort_groups", "roster_path"})
# Used with fullmatch. [0-9] because \d also matches non-ASCII digits.
REGION_RE = re.compile(r"[a-z]{2}(-[a-z]+)+-[0-9]")
_BUCKET_RE = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]")

BOTO_CONFIG = BotoConfig(
    retries={"mode": "standard", "total_max_attempts": 8},
    user_agent_extra="aws-resource-owner-dashboard",
)


class ConfigError(ValueError):
    """Invalid settings, local config file or environment value."""


@dataclass(frozen=True)
class Config:
    regions: tuple[str, ...] = DEFAULT_REGIONS
    cohort_groups: Mapping[str, str] = field(default_factory=dict)
    max_age_days: int = 4  # most labs take 2 days or less
    lookback_days: int = 90
    roster_path: str | None = None
    bucket: str | None = None  # the report bucket (Lambda only)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _build(
    *,
    regions: Any,
    max_age_days: Any,
    lookback_days: Any,
    cohort_groups: Any,
    roster_path: str | None = None,
    bucket: str | None = None,
) -> Config:
    """The one validator for both entry points (files and environment)."""
    if not isinstance(regions, list | tuple) or not regions:
        raise ConfigError("regions must be a non-empty list")
    regions = tuple(regions)
    if len(set(regions)) != len(regions):
        raise ConfigError("regions contains duplicates")
    for region in regions:
        if not isinstance(region, str) or not REGION_RE.fullmatch(region):
            raise ConfigError(f"invalid region: {region!r}")
    if not _is_int(max_age_days) or max_age_days < 1:
        raise ConfigError("max_age_days must be a whole number of at least 1")
    if not _is_int(lookback_days) or not 1 <= lookback_days <= 90:
        raise ConfigError("lookback_days must be 1..90 (CloudTrail LookupEvents keeps 90 days)")
    if not isinstance(cohort_groups, Mapping) or not all(
        isinstance(k, str) and isinstance(v, str) and k and v for k, v in cohort_groups.items()
    ):
        raise ConfigError("cohort_groups must map cohort labels to IAM group names")
    return Config(
        regions=regions,
        cohort_groups=dict(cohort_groups),
        max_age_days=max_age_days,
        lookback_days=lookback_days,
        roster_path=roster_path,
        bucket=bucket,
    )


def load_settings(path: str | os.PathLike[str] = SETTINGS_FILE) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        raise ConfigError(f"{path} not found: run from the repo root or pass --settings") from None
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON (line {exc.lineno})") from None
    if not isinstance(data, dict) or set(data) != SETTINGS_KEYS:
        raise ConfigError(f"{path} must have exactly the keys: {', '.join(sorted(SETTINGS_KEYS))}")
    return data


def load_config(
    path: str | None = None, settings_path: str | os.PathLike[str] = SETTINGS_FILE
) -> Config:
    """Local runs: the committed settings.json plus the optional gitignored dashboard.toml."""
    local: dict[str, Any] = {}
    if path:
        try:
            with open(path, "rb") as fh:
                local = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{path}: {exc}") from None
    extra = set(local) - LOCAL_KEYS
    if extra:
        raise ConfigError(f"{path}: unknown keys {', '.join(sorted(extra))}")
    return _build(
        **load_settings(settings_path),
        cohort_groups=local.get("cohort_groups", {}),
        roster_path=local.get("roster_path"),
    )


def config_from_env(env: Mapping[str, str] = os.environ) -> Config:
    """The Lambda: every value from the DASHBOARD_* variables that infra/app sets."""
    names = (
        "regions",
        "max_age_days",
        "lookback_days",
        "cohort_groups",
        "bucket",
    )
    try:
        raw = {name: env[f"DASHBOARD_{name.upper()}"] for name in names}
    except KeyError as exc:
        raise ConfigError(f"missing environment variable {exc.args[0]}") from None
    try:
        max_age_days = int(raw["max_age_days"])
        lookback_days = int(raw["lookback_days"])
        cohort_groups = json.loads(raw["cohort_groups"])
    except ValueError:  # json.JSONDecodeError is a ValueError too
        raise ConfigError("DASHBOARD_* numbers or the cohort groups JSON are malformed") from None
    if not _BUCKET_RE.fullmatch(raw["bucket"]):
        raise ConfigError("DASHBOARD_BUCKET is not a bucket name")
    return _build(
        regions=[r.strip() for r in raw["regions"].split(",")],
        max_age_days=max_age_days,
        lookback_days=lookback_days,
        cohort_groups=cohort_groups,
        bucket=raw["bucket"],
    )


class Aws:
    """One cached boto3 client per (service, region). Safe to share between threads."""

    def __init__(self, session: Any) -> None:
        self._session = session
        self._clients: dict[tuple[str, str], Any] = {}
        self._lock = threading.Lock()

    def client(self, service: str, region: str) -> Any:
        key = (service, region)
        with self._lock:  # boto3 session client creation is not thread-safe
            if key not in self._clients:
                self._clients[key] = self._session.client(
                    service, region_name=region, config=BOTO_CONFIG
                )
            return self._clients[key]


def per_region[T](regions: Iterable[str], fn: Callable[[str], T]) -> dict[str, T]:
    """Run `fn` once per region in parallel. `fn` handles its own expected errors."""
    regions = list(regions)
    with ThreadPoolExecutor(max_workers=max(1, len(regions))) as pool:
        futures = {region: pool.submit(fn, region) for region in regions}
        return {region: future.result() for region, future in futures.items()}
