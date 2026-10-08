"""Render the single self-contained HTML page.

Each resource row that needs a look gets a "check this" mark.
"""

from __future__ import annotations

import base64
import hashlib
import html
import re
from datetime import UTC, datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

from .collectors.base import Resource
from .config import REGION_RE, Config
from .pipeline import Card, Report, Row


def checks(row: Row, cfg: Config) -> tuple[str, ...]:
    """Return why a resource gets a "check this" mark: age, a public IP, an old EKS version."""
    r = row.resource
    out = []
    # An attached volume goes away with its instance, so it gets no mark of its own.
    if r.kind == "volume" and r.attrs.get("attached_instance"):
        return ()
    if row.age_days is not None and row.age_days > cfg.max_age_days:
        out.append(f"older than {cfg.max_age_days} days")
    if r.kind == "instance" and r.attrs.get("state") == "running" and r.attrs.get("public_ip"):
        out.append("running with a public IP")
    if r.kind == "eks-cluster":
        # Past end of standard support costs $0.60/h, not $0.10/h, and is on by default
        # (https://docs.aws.amazon.com/eks/latest/userguide/kubernetes-versions.html).
        status = r.attrs.get("version_status")
        if status in ("EXTENDED_SUPPORT", "UNSUPPORTED"):
            out.append(f"old Kubernetes version {r.attrs.get('version')}, costs extra every hour")
        elif status is None:  # never let a failed version lookup hide the costliest item
            out.append(
                "support status unknown: check its Kubernetes version, "
                "old ones cost extra every hour"
            )
    return tuple(out)


def check_count(card: Card, cfg: Config) -> int:
    return sum(1 for r in card.rows if checks(r, cfg))


CSS = """
:root{color-scheme:light dark;--bg:#fff;--fg:#1a1a1a;--muted:#5f6368;--line:#e2e2e2;
--warn-bg:#fff4e5;--warn-fg:#7a4100;--ok-bg:#e8f5e9;--ok-fg:#1b5e20;--badge:#eef1f5;
--bad-bg:#fdecea;--bad-fg:#8a1c1c}
@media (prefers-color-scheme:dark){:root{--bg:#121417;--fg:#e8eaed;--muted:#9aa0a6;
--line:#2c3036;--warn-bg:#3a2a12;--warn-fg:#ffcc80;--ok-bg:#16301a;--ok-fg:#a5d6a7;--badge:#2a2f36;
--bad-bg:#3b1414;--bad-fg:#ffb4ab}}
*{box-sizing:border-box}
body{margin:0;padding:16px;background:var(--bg);color:var(--fg);
font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1100px;margin:0 auto}
h1{font-size:1.4rem;margin:0 0 4px}h2{font-size:1.15rem;margin:28px 0 8px}h3{font-size:1rem;margin:0 0 8px}
.muted{color:var(--muted)}
.banner{background:var(--warn-bg);color:var(--warn-fg);padding:8px 12px;border-radius:6px;margin:8px 0}
.banner.stale{background:var(--bad-bg);color:var(--bad-fg);font-weight:600}
.banner.ok{background:var(--ok-bg);color:var(--ok-fg)}
.check{font-weight:600;color:var(--bad-fg)}
tr.marked{background:var(--bad-bg)}
.vh{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap}
footer{margin:28px 0 8px}
input,select,button{font:inherit}
.wrap{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:.92rem}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-weight:600;color:var(--muted)}
.num{text-align:right;white-space:nowrap}
.badge{display:inline-block;background:var(--badge);border-radius:999px;padding:0 8px;
font-size:.78rem;margin-left:4px;white-space:nowrap}
.card{border:1px solid var(--line);border-radius:8px;padding:12px;margin:12px 0}
.card.clean{background:var(--ok-bg);color:var(--ok-fg)}
a{color:inherit}
@media (max-width:600px){.wrap thead{display:none}
.wrap tr{display:block;border-bottom:1px solid var(--line);padding:4px 0}
.wrap td{display:block;border:0;padding:2px 8px}.wrap .num{text-align:left}}
[hidden]{display:none!important}
"""

JS = """
(function () {
  var select = document.getElementById("cohort");
  var label = document.getElementById("cohort-label");
  var cards = document.getElementById("cards");
  var stale = document.getElementById("stale");
  var who = document.getElementById("find-me");
  var forget = document.getElementById("forget-me");
  var allClear = document.getElementById("all-clear");
  var noMatch = document.getElementById("no-match");
  var showing = document.getElementById("showing");
  var showingName = document.getElementById("showing-name");
  var showAll = document.getElementById("show-all");
  var noOwner = document.getElementById("no-owner");
  var clusterGone = document.getElementById("cluster-gone");
  var only = document.getElementById("only-checks");
  var overview = document.getElementById("overview");
  var resultCount = document.getElementById("result-count");
  var KEY = "dashboard-find-me";
  var STALE_MS = 26 * 3600 * 1000;
  var generated = Date.parse(stale.getAttribute("data-generated"));
  if (!isNaN(generated) && Date.now() - generated > STALE_MS) { stale.hidden = false; }
  var TIME = { hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZoneName: "shortOffset" };
  function part(parts, type) {
    for (var i = 0; i < parts.length; i++) { if (parts[i].type === type) { return parts[i].value; } }
    return "";
  }
  try { var parts = new Intl.DateTimeFormat(undefined, TIME).formatToParts(new Date(document.getElementById("generated").getAttribute("datetime"))); document.getElementById("local-time").textContent = part(parts, "hour") + ":" + part(parts, "minute") + " your time, " + part(parts, "timeZoneName"); } catch (err) { /* older browser: the server text stays */ }
  try { who.value = localStorage.getItem(KEY) || ""; } catch (err) { who.value = ""; }
  var current = "";
  // Only a cohort in the select counts: #no-owner, #cluster-gone and #top are jump links.
  function cohort() {
    var h;
    try { h = decodeURIComponent(location.hash.slice(1)); } catch (err) { return current; }
    for (var i = 0; i < select.options.length; i++) {
      if (select.options[i].value === h) { current = h; }
    }
    return current;
  }
  function typed() { return who.value.trim().toLowerCase(); }
  function order(key) {
    var list = Array.prototype.slice.call(cards.querySelectorAll("article.card"));
    list.sort(function (a, b) { return Number(a.getAttribute(key)) - Number(b.getAttribute(key)); });
    for (var i = 0; i < list.length; i++) { cards.appendChild(list[i]); }
  }
  var sortedBy = "data-alpha";
  var timer = null;
  function apply() {
    var c = cohort();
    var u = typed();
    var items = document.querySelectorAll("[data-cohort]");
    var matched = 0;
    var todo = 0;
    var shown = 0;
    var marked = 0;
    for (var i = 0; i < items.length; i++) {
      var el = items[i];
      var owner = (el.getAttribute("data-user") || "").toLowerCase();
      var hide = (c !== "" && el.getAttribute("data-cohort") !== c) || (u !== "" && owner !== u) ||
        (only.checked && el.getAttribute("data-checks") === "0");
      el.hidden = hide;
      if (!hide) {
        shown += 1;
        if (el.getAttribute("data-checks") !== "0") { marked += 1; }
      }
      if (!hide && u !== "") {
        matched += 1;
        todo += Number(el.getAttribute("data-checks") || "0");
      }
    }
    allClear.hidden = u === "" || matched === 0 || todo > 0;
    noMatch.hidden = u === "" || matched > 0;
    showing.hidden = u === "";
    showingName.textContent = who.value.trim();
    var students = items.length === 1 ? "1 student" : items.length + " students";
    var text = (shown === items.length ? students : "Showing " + shown + " of " + students) +
      " · " + marked + " with something to check";
    if (resultCount.textContent !== text) { resultCount.textContent = text; }
    overview.hidden = u !== "";
    noOwner.hidden = u !== "";
    clusterGone.hidden = u !== "";
    label.textContent = c === "" ? "all cohorts" : c === "-" ? "no cohort" : c;
    select.value = c;
    var key = c === "" && u === "" ? "data-alpha" : "data-rank";
    if (key !== sortedBy) {
      order(key);
      sortedBy = key;
    }
  }
  select.addEventListener("change", function () {
    location.hash = encodeURIComponent(select.value);
  });
  who.addEventListener("input", function () {
    clearTimeout(timer);
    timer = setTimeout(function () {
      try { if (typed() === "") { localStorage.removeItem(KEY); } else { localStorage.setItem(KEY, who.value.trim()); } } catch (err) { /* storage blocked: the filter still works */ }
      apply();
    }, 150);
  });
  forget.addEventListener("click", function () {
    who.value = "";
    try { localStorage.removeItem(KEY); } catch (err) { /* nothing stored */ }
    apply();
  });
  showAll.addEventListener("click", function () {
    who.value = "";
    apply();
  });
  only.addEventListener("change", apply);
  window.addEventListener("hashchange", apply);
  apply();
})();
"""

KIND_LABEL = {
    "instance": "EC2 instance",
    "volume": "EBS volume",
    "eip": "Elastic IP",
    "public-ip": "Public IPv4",
    "nat": "NAT gateway",
    "elb-classic": "Classic load balancer",
    "elb-v2": "Load balancer",
    "eks-cluster": "EKS cluster",
    "eks-nodegroup": "EKS node group",
}
BERLIN = ZoneInfo("Europe/Berlin")
# Lowercase alphanumerics: real IDs are hex, placeholders contain "example". Either way
# nothing that could break out of an attribute or URL gets through.
_IDS = {
    "instance": re.compile(r"^i-[0-9a-z]{8,17}$"),
    "volume": re.compile(r"^vol-[0-9a-z]{8,17}$"),
    "eip": re.compile(r"^eipalloc-[0-9a-z]{8,17}$"),
    "public-ip": re.compile(r"^eni-[0-9a-z]{8,17}$"),
    "nat": re.compile(r"^nat-[0-9a-z]{8,17}$"),
    "elb-classic": re.compile(r"^[A-Za-z0-9-]{1,32}$"),
    "elb-v2": re.compile(
        r"^arn:aws:elasticloadbalancing:[a-z0-9-]+:[0-9]{12}:loadbalancer/(app|net|gwy)/[A-Za-z0-9-]{1,32}/[0-9a-f]{16}$"
    ),
}
_EKS_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")


def e(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _sha256(text: str) -> str:
    return "sha256-" + base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode()


def console_link(r: Resource) -> str | None:
    """Built only from fixed templates and IDs that pass a strict regex."""
    if not REGION_RE.fullmatch(r.region):
        return None
    if r.kind in _IDS and not _IDS[r.kind].fullmatch(r.id):
        return None
    region = r.region
    ec2 = f"https://{region}.console.aws.amazon.com/ec2/home?region={region}#"
    match r.kind:
        case "instance":
            return f"{ec2}InstanceDetails:instanceId={r.id}"
        case "volume":
            return f"{ec2}VolumeDetails:volumeId={r.id}"
        case "eip":
            return f"{ec2}ElasticIpDetails:AllocationId={r.id}"
        case "public-ip":
            return f"{ec2}NetworkInterface:networkInterfaceId={r.id}"
        case "nat":
            return f"https://{region}.console.aws.amazon.com/vpcconsole/home?region={region}#NatGatewayDetails:natGatewayId={r.id}"
        case "elb-classic":
            return f"{ec2}LoadBalancers:search={r.id}"
        case "elb-v2":
            return f"{ec2}LoadBalancer:loadBalancerArn={quote(r.id, safe='')}"
        case "eks-cluster" if _EKS_NAME.fullmatch(r.name):
            return f"https://{region}.console.aws.amazon.com/eks/clusters/{r.name}?region={region}"
        case "eks-nodegroup" if _EKS_NAME.fullmatch(r.name) and _EKS_NAME.fullmatch(
            r.attrs.get("cluster_name", "")
        ):
            return (
                f"https://{region}.console.aws.amazon.com/eks/clusters/"
                f"{r.attrs['cluster_name']}/nodegroups/{r.name}?region={region}"
            )
    return None


def _label(r: Resource) -> str:
    if r.kind in ("elb-v2", "elb-classic", "eks-cluster", "eks-nodegroup"):
        return r.name or r.id
    return f"{r.id} ({r.name})" if r.name else r.id


NEW_TAB = '<span aria-hidden="true"> ↗</span><span class="vh"> (opens in new tab)</span>'


def _resource_cell(r: Resource) -> str:
    """Name or ID, linked to the console when the ID passes its regex."""
    text = e(_label(r))
    link = console_link(r)
    # The arrow is for the eye, the hidden words for screen readers (WCAG G201).
    return (
        f'<a href="{e(link)}" rel="noopener noreferrer" target="_blank">{text}{NEW_TAB}</a>'
        if link
        else text
    )


def _type(r: Resource) -> str:
    return e(KIND_LABEL.get(r.kind, r.kind))


def _n(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _age(days: int | None) -> str:
    return "—" if days is None else f"{days} d"


def _badges(row: Row) -> str:
    """Only the two warnings that the owner may be wrong."""
    out = []
    if row.owner.principal and not row.owner.principal.verified:
        out.append("unverified")
    if row.owner.guess:
        out.append("guess")
    return "".join(f'<span class="badge">{e(b)}</span>' for b in out)


def _check_cell(reasons: tuple[str, ...]) -> str:
    if not reasons:
        return "<td></td>"
    # Plain text, never a link: a status mark shouldn't look clickable (GOV.UK Tag).
    return (
        '<td><strong class="check">Check this:</strong> '
        f'<span class="muted">{e(", ".join(reasons))}</span></td>'
    )


def _cohort_attr(cohort: str | None) -> str:
    return f' data-cohort="{e(cohort or "-")}"'


def _user_attr(name: str | None) -> str:
    """The find-me filter compares this attribute with the typed name."""
    return f' data-user="{e(name or "")}"'


def _table(head: list[str], body: list[str]) -> str:
    cells = "".join(f"<th>{e(h)}</th>" for h in head)
    return f'<div class="wrap"><table><thead><tr>{cells}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


STALE_TEXT = "This page is more than a day old: the scheduled runs may have failed. Tell a teacher."


def _offset(when: datetime) -> str:
    """UTC+1 or UTC+2: a number, because zone abbreviations are ambiguous (IANA theory.html)."""
    offset = when.utcoffset()
    minutes = int(offset.total_seconds()) // 60 if offset else 0
    hours, rest = divmod(abs(minutes), 60)
    return f"UTC{'-' if minutes < 0 else '+'}{hours}" + (f":{rest:02d}" if rest else "")


def _header(report: Report) -> str:
    utc = report.generated_at.strftime("%Y-%m-%d %H:%M UTC")
    cet = report.generated_at.astimezone(BERLIN)
    # Students live all over Europe: say "Central European time", never "Berlin". The
    # script swaps this for the viewer's own time when the browser can.
    local = f"{cet:%H:%M} Central European time, {_offset(cet)}"
    stamp = report.generated_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    # The script shows this when the page is over 26 h old: yesterday's run failed.
    stale = f'<p class="banner stale" id="stale" data-generated="{stamp}" hidden>{STALE_TEXT}</p>'
    incomplete = (
        f'<p class="banner" id="incomplete">Incomplete: {e("; ".join(report.incomplete))}</p>'
        if report.incomplete
        else ""
    )
    options = ['<option value="">All cohorts</option>']
    options += [f'<option value="{e(c)}">{e(c)}</option>' for c in report.cohorts]
    options.append('<option value="-">No cohort</option>')
    return (
        "<header><h1>What did I forget to delete?</h1>"
        "<p>Type your username, delete what you no longer need.</p>"
        f'<p class="muted">Generated <time id="generated" datetime="{stamp}">{e(utc)}</time>'
        f' (<span id="local-time">{e(local)}</span>) · regions {e(", ".join(report.regions))}</p>'
        f"{stale}{incomplete}"
        f'<p><label for="cohort">Cohort</label> <select id="cohort">{"".join(options)}</select>'
        ' <span class="muted">showing <span id="cohort-label">all cohorts</span></span></p>'
        '<p><input id="only-checks" type="checkbox"> '
        '<label for="only-checks">Only show students with something to check</label></p>'
        '<p><label for="find-me">Find me</label> <input id="find-me" type="text" '
        'autocomplete="off" spellcheck="false" placeholder="your IAM username"> '
        '<button id="forget-me" type="button">Forget me</button> <span class="muted">'
        "remembered on this device only</span></p>"
        '<p id="showing" hidden>Showing only <strong id="showing-name"></strong> · '
        '<button id="show-all" type="button">Show everyone</button></p>'
        '<p class="banner ok" id="all-clear" hidden>All clear: nothing to check for this '
        "username.</p>"
        '<p class="banner" id="no-match" hidden>Nothing found for this username. Check the '
        "spelling, or you have nothing running.</p></header>"
    )


def _card(card: Card, cfg: Config, rank: int, alpha: int) -> str:
    marks = [checks(r, cfg) for r in card.rows]
    to_check = sum(1 for m in marks if m)
    cohort = (
        f'<span class="badge">{e(card.cohort)}</span>'
        if card.cohort
        else '<span class="badge">no cohort</span>'
    )
    head = f"<h3>{e(card.name)} {cohort}</h3>"
    attrs = (
        f' id="card-{e(card.name)}"{_cohort_attr(card.cohort)}'
        f' data-rank="{rank}" data-alpha="{alpha}" data-checks="{to_check}"{_user_attr(card.name)}'
    )
    if not card.rows:
        return f'<article class="card clean"{attrs}>{head}<p>No resources running.</p></article>'
    body = [
        f"<tr{' class="marked"' if m else ''}><td>{_resource_cell(r.resource)}{_badges(r)}"
        f"{f' <span class="muted">{e(r.owner.detail)}</span>' if r.owner.method == 'indirect' else ''}</td>"
        f"<td>{_type(r.resource)}</td><td>{e(r.resource.region)}</td>"
        f'<td class="num">{_age(r.age_days)}</td>{_check_cell(m)}</tr>'
        for r, m in zip(card.rows, marks, strict=True)
    ]
    summary = f'<p class="muted">{to_check} to check · {_n(len(card.rows), "resource")}</p>'
    return (
        f'<article class="card"{attrs}>{head}{summary}'
        + _table(["Resource", "Type", "Region", "Age", "Check"], body)
        + "</article>"
    )


def _owner_problems(report: Report) -> str:
    def rows(items: list[Row]) -> list[str]:
        return [
            f"<tr><td>{_resource_cell(r.resource)}</td><td>{_type(r.resource)}</td>"
            f'<td>{e(r.resource.region)}</td><td class="num">{_age(r.age_days)}</td>'
            f"<td>{e(r.owner.detail)}{e(f' · Name tag: {r.owner.guess} (guess)') if r.owner.guess else ''}</td></tr>"
            for r in items
        ]

    head = ["Resource", "Type", "Region", "Age", "Why"]
    unknown = _table(head, rows(report.unknown)) if report.unknown else "<p>None.</p>"
    orphaned = _table(head, rows(report.orphaned)) if report.orphaned else "<p>None.</p>"
    return (
        f'<section id="no-owner"><h2>No owner found (ask before deleting)</h2>{unknown}</section>'
        f'<section id="cluster-gone"><h2>Cluster gone (ask a teacher)</h2>{orphaned}</section>'
    )


def _jump(count: int, text: str, target: str) -> str:
    """The whole phrase is the link text (WCAG 2.4.4); nothing to jump to at 0."""
    words = e(f"{count} {text}")
    return f'<a href="#{target}">{words}</a>' if count else words


def _overview(students: int, marked: int, report: Report) -> str:
    """Render the one line above the cards.

    The script keeps the student counts in step with the filters. It is a status region,
    so screen readers hear the new count (WCAG 4.1.3). The two links jump to the sections
    at the bottom (WCAG G124); those rows have no cohort.
    """
    return (
        '<nav id="overview" aria-label="On this page"><p>'
        '<span id="result-count" role="status" aria-live="polite">'
        f"{_n(students, 'student')} · {marked} with something to check</span>"
        f" · {_jump(len(report.unknown), 'with no owner', 'no-owner')}"
        f" · {_jump(len(report.orphaned), 'cluster gone', 'cluster-gone')}</p></nav>"
    )


FOOTER = (
    '<footer><p class="muted">Visible to everyone with access to this AWS account. '
    "Please don't share screenshots outside the course.</p></footer>"
)


def render_page(report: Report, cfg: Config) -> str:
    count = {c.name: check_count(c, cfg) for c in report.cards}
    # Alphabetical by default; while a cohort or name filter is set the script
    # switches to data-rank: most marked rows first, then by name.
    by_need = sorted(report.cards, key=lambda c: (-count[c.name], c.name.casefold(), c.name))
    rank = {c.name: i for i, c in enumerate(by_need)}
    cards = sorted(report.cards, key=lambda c: (c.name.casefold(), c.name))
    listed = "".join(_card(c, cfg, rank[c.name], i) for i, c in enumerate(cards))
    body = (
        _header(report)
        + _overview(len(report.cards), sum(1 for n in count.values() if n), report)
        + '<section id="cards"><h2>By student</h2>'
        + (
            '<p class="muted">Resource names open the AWS console in a new tab.</p>' + listed
            if listed
            else "<p>Nothing running.</p>"
        )
        + "</section>"
        + _owner_problems(report)
        + '<p><a href="#top">Back to top</a></p>'
        + FOOTER
    )
    csp = (
        f"default-src 'none'; style-src '{_sha256(CSS)}'; script-src '{_sha256(JS)}'; "
        "img-src data:; base-uri 'none'; form-action 'none'"
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<meta http-equiv="Content-Security-Policy" content="{csp}">'
        '<meta name="referrer" content="no-referrer">'
        '<meta name="robots" content="noindex"><title>Forgotten AWS resources</title>'
        f'<style>{CSS}</style></head><body><main id="top">{body}</main>'
        f"<script>{JS}</script></body></html>"
    )
