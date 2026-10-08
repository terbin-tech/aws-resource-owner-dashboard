"""Wire collectors, attribution and people together for the CLI and the Lambda.

`scan` is the only step that talks to AWS; `build_report` is pure. Nothing is saved
between runs: creator data lives only in memory.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from .attribution import LookupResult, Owner, attribute, event_names, lookup_creations
from .collectors import error_code, run_collectors
from .collectors.base import Incomplete, Resource
from .config import GLOBAL_REGION, Config, per_region
from .people import IamUser, Person, Roster, fetch_iam, map_people

ATTRIBUTION_ONLY = {"asg"}


@dataclass
class ScanData:
    generated_at: datetime
    regions: list[str]
    resources: list[Resource]
    incomplete: list[Incomplete]
    lookups: list[LookupResult]
    users: list[IamUser]
    members: dict[str, list[str]]
    lookback_days: int
    errors: list[str] = field(default_factory=list)


def check_credentials(aws: Any) -> str | None:
    """Return an error code if the credentials are missing or expired, else None.

    It runs before the scan, so a dead session fails in a second, not after minutes of
    empty results. GetCallerIdentity needs no IAM permission.
    """
    try:
        aws.client("sts", GLOBAL_REGION).get_caller_identity()
    except (BotoCoreError, ClientError) as exc:
        return error_code(exc)
    return None


def scan(aws: Any, cfg: Config, now: datetime) -> ScanData:
    resources, incomplete = run_collectors(aws, cfg.regions)

    start = now - timedelta(days=cfg.lookback_days)
    jobs = {r: event_names(include_global=(r == GLOBAL_REGION)) for r in cfg.regions}
    if GLOBAL_REGION not in jobs:  # CreateUser events only exist in us-east-1
        jobs[GLOBAL_REGION] = event_names(include_regional=False, include_global=True)
    results = per_region(
        jobs, lambda r: lookup_creations(aws.client("cloudtrail", r), r, jobs[r], start, now)
    )

    users, members, errors = fetch_iam(aws, cfg.cohort_groups)

    return ScanData(
        generated_at=now,
        regions=list(cfg.regions),
        resources=resources,
        incomplete=incomplete,
        lookups=[results[r] for r in jobs],
        users=users,
        members=members,
        lookback_days=cfg.lookback_days,
        errors=errors,
    )


@dataclass(frozen=True)
class Row:
    resource: Resource
    owner: Owner
    person: Person | None
    age_days: int | None

    @property
    def card(self) -> str | None:
        if self.person:
            return self.person.card
        return self.owner.principal.label if self.owner.principal else None

    @property
    def cohort(self) -> str | None:
        return self.person.cohort if self.person else None


@dataclass
class Card:
    name: str
    cohort: str | None
    rows: list[Row]


@dataclass
class Report:
    generated_at: datetime
    regions: list[str]
    rows: list[Row]
    cards: list[Card]
    unknown: list[Row]
    orphaned: list[Row]
    incomplete: list[str]
    unmapped_users: int  # for the Mapping section of verify.md
    roster_loaded: bool  # for the Mapping section of verify.md
    cohorts: list[str]


def build_report(data: ScanData, roster: Roster | None) -> Report:
    creations = [c for lk in data.lookups for c in lk.creations]
    failed = {lk.region for lk in data.lookups if lk.failed}
    eks_unavailable = {i.region for i in data.incomplete if i.service == "eks"}
    owners = attribute(
        data.resources, creations, failed, eks_unavailable, lookback_days=data.lookback_days
    )
    owning_users = {
        o.principal.name for o in owners.values() if o.principal and o.principal.kind == "user"
    }
    people = map_people(
        data.users,
        data.members,
        roster,
        [c for c in creations if c.resource_kind == "iam-user"],
        owning_users,
    )
    now = data.generated_at

    rows = []
    for r in data.resources:
        if r.kind in ATTRIBUTION_ONLY:
            continue
        owner = owners[r.key]
        person = (
            people.get(owner.principal.name)
            if owner.principal and owner.principal.kind == "user"
            else None
        )
        created = r.created_at or (
            owner.creation.event_time if owner.method == "event" and owner.creation else None
        )
        age = (now - created).days if created else None
        rows.append(Row(r, owner, person, age))

    grouped: dict[str, list[Row]] = defaultdict(list)
    for row in rows:
        if row.card:
            grouped[row.card].append(row)
    cards = []
    for name, card_rows in grouped.items():
        person = next((r.person for r in card_rows if r.person), None)
        cards.append(Card(name, person.cohort if person else None, card_rows))
    seen_cards = set(grouped)
    for p in people.values():  # mapped students with nothing running get a green card
        if p.cohort and p.card not in seen_cards:
            seen_cards.add(p.card)
            cards.append(Card(p.card, p.cohort, []))

    unmapped = {
        row.owner.principal.name
        for row in rows
        if row.owner.principal and row.owner.principal.kind == "user" and not row.cohort
    }
    incomplete = (
        [f"{i.service} in {i.region} ({i.error})" for i in data.incomplete]
        + [f"CloudTrail in {lk.region} ({lk.error})" for lk in data.lookups if lk.failed]
        + data.errors
    )
    return Report(
        generated_at=now,
        regions=data.regions,
        rows=rows,
        cards=cards,
        # Every row without a card is listed here, so nothing drops off the page.
        unknown=[r for r in rows if not r.card and r.owner.method != "orphaned"],
        orphaned=[r for r in rows if r.owner.method == "orphaned"],
        incomplete=incomplete,
        unmapped_users=len(unmapped),
        roster_loaded=roster is not None,
        cohorts=sorted({c.cohort for c in cards if c.cohort}),
    )
