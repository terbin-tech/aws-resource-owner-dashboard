"""Write the live verification report.

It goes to out/verify.md and contains usernames and resource IDs, so it is never
committed.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable

from .attribution import RULES
from .pipeline import Report, Row, ScanData

MIN_HINT_LENGTH = 4
METHODS = ("event", "indirect", "unknown", "orphaned", "lookup-failed")


def name_hint(row: Row, usernames: Iterable[str]) -> str | None:
    text = f"{row.resource.name} {row.resource.tags.get('Name', '')}".lower()
    for user in sorted(usernames, key=len, reverse=True):
        u = user.lower()
        if len(u) >= MIN_HINT_LENGTH and re.search(
            rf"(?<![a-z0-9]){re.escape(u)}(?![a-z0-9])", text
        ):
            return user
    return None


def render_verify(data: ScanData, report: Report) -> str:
    out = [
        "# Verification report",
        "",
        f"Generated {data.generated_at.isoformat()}. Local only: never commit this file.",
        "",
        "## 1. CloudTrail lookup duration",
        "",
        "| region | duration s | creations | failed |",
        "|---|---|---|---|",
    ]
    for lk in data.lookups:
        # A DetachVolume entry links a disk to an instance; it is not a creation.
        made = sum(1 for c in lk.creations if c.resource_kind != "volume-detach")
        out.append(f"| {lk.region} | {lk.duration_s} | {made} | {lk.error or 'no'} |")

    out += [
        "",
        "## 2. Rule ID paths",
        "",
        "| rule | region | events | with IDs | status |",
        "|---|---|---|---|---|",
    ]
    keys = list(dict.fromkeys(f"{r.event_name}:{r.kind}" for r in RULES))
    for lk in data.lookups:
        for key in keys:
            seen, with_ids = lk.stats.get(key, [0, 0])
            status = (
                "no events in window"
                if seen == 0
                else ("OK" if with_ids == seen else "PATH MISMATCH")
            )
            out.append(f"| {key} | {lk.region} | {seen} | {with_ids} | {status} |")

    out += [
        "",
        "## 3. Owner method per kind",
        "",
        "| kind | " + " | ".join(METHODS) + " | unverified |",
    ]
    out.append("|---" * (len(METHODS) + 2) + "|")
    by_kind: dict[str, Counter] = {}
    for row in report.rows:
        counts = by_kind.setdefault(row.resource.kind, Counter())
        counts[row.owner.method] += 1
        if row.owner.principal and not row.owner.principal.verified:
            counts["unverified"] += 1
    for kind, counts in sorted(by_kind.items()):
        out.append(
            f"| {kind} | " + " | ".join(str(counts[m]) for m in (*METHODS, "unverified")) + " |"
        )
    # A disk matched to its launch is a match by time and zone, not a recorded fact:
    # count it for spot checks.
    matched = sum(1 for row in report.rows if row.owner.detail.startswith("made with "))
    linked = sum(1 for row in report.rows if row.owner.detail.startswith("detached from "))
    out += [
        "",
        f"- disks matched to their launch (same zone, within 10 s): {matched}",
        f"- disks linked by a detach event: {linked}",
    ]

    usernames = [u.name for u in data.users]
    agreements, disagreements = 0, []
    for row in report.rows:
        hint = name_hint(row, usernames)
        if hint is None:
            continue
        creator = row.owner.principal.name if row.owner.principal else "—"
        if creator == hint:
            agreements += 1
        else:
            disagreements.append(
                f"| {row.resource.id} | {row.resource.region} | {hint} | {creator} | {row.owner.method} |"
            )
    out += [
        "",
        "## 4. Name hint vs creator",
        "",
        f'Agreements: {agreements}. Every disagreement below needs an explanation before "done".',
        "",
        "| resource | region | name hint | creator | method |",
        "|---|---|---|---|---|",
        *disagreements,
    ]

    out += [
        "",
        "## 5. Incomplete",
        "",
        *([f"- {m}" for m in report.incomplete] or ["- none"]),
        "",
        "## 6. Mapping",
        "",
        f"- users with resources but no cohort: {report.unmapped_users}",
        f"- roster: {'loaded' if report.roster_loaded else 'not loaded (absent or denied)'}",
        "",
        "## 7. Spot check (manual)",
        "",
        "Confirm 3 to 5 owners you know personally (oldest first):",
        "",
    ]
    oldest = sorted(
        report.rows, key=lambda r: (r.age_days is None, -(r.age_days or 0), r.resource.id)
    )
    for row in oldest[:5]:
        owner = row.owner.principal.label if row.owner.principal else row.owner.method
        out.append(f"- [ ] {row.resource.kind} {row.resource.id} ({row.resource.region}) → {owner}")
    return "\n".join(out) + "\n"
