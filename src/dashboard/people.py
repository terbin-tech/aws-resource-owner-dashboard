"""Principal -> student -> cohort. Usernames only, never real names."""

from __future__ import annotations

import csv
import io
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from .attribution import WINDOW, Creation
from .collectors import error_code
from .config import GLOBAL_REGION

ROSTER_COLUMNS = ("username", "primary_username", "cohort")
# IAM UserName pattern (API_CreateUser), ASCII only.
IAM_NAME = re.compile(r"[\w+=,.@-]{1,64}", re.ASCII)
Roster = dict[str, tuple[str, str]]


class RosterError(ValueError):
    """Bad roster file. Messages name line numbers, counts and expected columns, never values."""


@dataclass(frozen=True)
class IamUser:
    name: str
    created_at: datetime


@dataclass(frozen=True)
class Person:
    username: str
    card: str  # card title: the roster's primary_username, else the username
    cohort: str | None


def parse_roster(text: str) -> Roster:
    """Read usernames and cohorts from a roster CSV.

    Errors name line numbers or counts, never values, so a roster with a real-name column
    can't leak it into a log or banner.
    """
    reader = csv.DictReader(io.StringIO(text.removeprefix("\ufeff")))
    reader.fieldnames = [f.strip() for f in reader.fieldnames or []]
    missing = [c for c in ROSTER_COLUMNS if c not in reader.fieldnames]
    if missing:
        raise RosterError(f"roster is missing columns: {', '.join(missing)}")
    if sorted(reader.fieldnames) != sorted(ROSTER_COLUMNS):
        raise RosterError(
            f"roster must have only the columns {', '.join(ROSTER_COLUMNS)} "
            f"(found {len(reader.fieldnames)} columns)"
        )
    entries: list[tuple[int, str, str, str]] = []
    seen: set[str] = set()
    for line, row in enumerate(reader, start=2):
        if None in row:  # more values than columns
            raise RosterError(f"line {line}: more values than columns")
        user = (row["username"] or "").strip()
        if not user or user.startswith("#"):
            continue
        if user in seen:
            raise RosterError(f"line {line}: duplicate username")
        seen.add(user)
        primary = (row["primary_username"] or "").strip() or user
        entries.append((line, user, primary, (row["cohort"] or "").strip()))
    for line, _, primary, _ in entries:
        if primary not in seen and not IAM_NAME.fullmatch(primary):
            raise RosterError(
                f"line {line}: primary_username is neither a roster username nor an IAM user name"
            )
    return {user: (primary, cohort) for _, user, primary, cohort in entries}


def load_roster_file(path: str | None) -> Roster | None:
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return parse_roster(fh.read())


def load_roster_s3(s3: Any, bucket: str, key: str) -> tuple[Roster | None, str | None]:
    """Read the Lambda's roster from config/ in the report bucket.

    Without s3:ListBucket a missing object is AccessDenied, so both mean "no roster", and
    the page does not mention it. Any other error becomes an incomplete banner, never a
    crash.
    """
    try:
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    except ClientError as exc:
        if error_code(exc) in ("NoSuchKey", "AccessDenied", "403", "404"):
            return None, None
        return None, f"roster: {error_code(exc)}"
    except BotoCoreError as exc:
        return None, f"roster: {error_code(exc)}"
    try:
        return parse_roster(body.decode("utf-8")), None
    except UnicodeDecodeError:
        return None, "roster: not UTF-8"
    except RosterError as exc:
        return None, f"roster: {exc}"


def fetch_iam(
    aws: Any, cohort_groups: Mapping[str, str]
) -> tuple[list[IamUser], dict[str, list[str]], list[str]]:
    """Return the IAM users and cohort group members, plus one error line per failed call.

    Each call fails on its own: one renamed group must not hide every user.
    """
    iam = aws.client("iam", GLOBAL_REGION)
    errors: list[str] = []
    try:
        users = [
            IamUser(u["UserName"], u["CreateDate"])
            for page in iam.get_paginator("list_users").paginate()
            for u in page["Users"]
        ]
    except (BotoCoreError, ClientError) as exc:
        users = []
        errors.append(f"iam users: {error_code(exc)}")
    members: dict[str, list[str]] = {}
    for cohort, group in cohort_groups.items():
        try:
            members[cohort] = sorted(
                u["UserName"]
                for page in iam.get_paginator("get_group").paginate(GroupName=group)
                for u in page["Users"]
            )
        except (BotoCoreError, ClientError) as exc:
            errors.append(f"iam group {cohort}: {error_code(exc)}")
    return users, members, errors


def map_people(
    users: list[IamUser],
    members: dict[str, list[str]],
    roster: Roster | None,
    user_creations: list[Creation],
    principals: Iterable[str] = (),
) -> dict[str, Person]:
    """Build one Person per IAM user, roster entry and owning username.

    The roster and the groups don't need an IAM record: a deleted user's resources still
    find their cohort.
    """
    group_of: dict[str, str] = {}
    for cohort in sorted(members):
        for name in members[cohort]:
            group_of.setdefault(name, cohort)

    created_at = {u.name: u.created_at for u in users}
    creator: dict[str, str] = {}
    for c in user_creations:
        if c.resource_kind != "iam-user" or c.principal.kind != "user":
            continue
        made = created_at.get(c.resource_id)
        if made is not None and abs(c.event_time - made) <= WINDOW:  # names can be reused
            creator[c.resource_id] = c.principal.name

    def cohort_of(name: str, seen: frozenset[str]) -> str | None:
        if roster and name in roster and roster[name][1]:
            return roster[name][1]
        if name in group_of:
            return group_of[name]
        parent = creator.get(name)
        if parent and parent not in seen:
            return cohort_of(parent, seen | {name})
        return None

    names = (
        {u.name for u in users}
        | {n for group in members.values() for n in group}
        | set(roster or {})
        | {p for p in principals if p}
    )
    people = {}
    for name in sorted(names):
        cohort = cohort_of(name, frozenset({name}))
        card = roster[name][0] if roster and name in roster else name
        people[name] = Person(name, card, cohort)
    return people
