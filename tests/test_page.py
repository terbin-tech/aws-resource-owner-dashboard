import base64
import hashlib
import re
from datetime import UTC, datetime

from dashboard.config import Config
from dashboard.pipeline import Card
from dashboard.render import CSS, JS, console_link, render_page
from tests.helpers import make_report, make_row, res

CFG = Config()
NEW_TAB = '<span aria-hidden="true"> ↗</span><span class="vh"> (opens in new tab)</span>'
ALB = "arn:aws:elasticloadbalancing:eu-central-1:111122223333:loadbalancer/app/demo-alb/0123456789abcdef"


def _sha(text):
    return "sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode()


def test_malicious_tag_and_cohort_stay_inert():
    evil = "<script>alert(1)</script>\"'><img src=x onerror=alert(1)>"
    row = make_row(
        res("instance", "i-0example0000000a1", name=evil, state="running"),
        cohort=evil,
    )
    page = render_page(make_report([row]), CFG)
    assert page.count("<script>") == 1  # only our own
    assert "<img" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_csp_hashes_match_inline_blocks():
    page = render_page(make_report(), CFG)
    csp = re.search(r'http-equiv="Content-Security-Policy" content="([^"]+)"', page).group(1)
    assert f"style-src '{_sha(CSS)}'" in csp
    assert f"script-src '{_sha(JS)}'" in csp
    assert csp.startswith("default-src 'none'")
    assert f"<style>{CSS}</style>" in page
    assert f"<script>{JS}</script>" in page
    # The console Open URL carries a token.
    assert '<meta name="referrer" content="no-referrer">' in page


def test_no_inline_style_or_event_handler_attributes():
    row = make_row(res("eip", "eipalloc-0example00000a1"), age_days=30)
    page = render_page(make_report([row]), CFG)
    assert not re.search(r"\sstyle=", page)
    assert not re.search(r"\son[a-z]+=", page)


def test_console_links_only_for_valid_ids():
    assert console_link(res("instance", "i-0example0000000a1")) == (
        "https://eu-central-1.console.aws.amazon.com/ec2/home?region=eu-central-1"
        "#InstanceDetails:instanceId=i-0example0000000a1"
    )
    assert console_link(res("instance", 'i-1"><x')) is None
    assert console_link(res("instance", "i-0example0000000a1", region="bad region")) is None
    assert "loadBalancerArn=arn%3Aaws%3Aelasticloadbalancing" in console_link(
        res("elb-v2", ALB, name="demo-alb")
    )
    assert console_link(res("eks-cluster", "arn", name="demo-eks")).endswith(
        "/eks/clusters/demo-eks?region=eu-central-1"
    )
    assert console_link(res("eks-cluster", "arn", name="../x")) is None
    assert console_link(res("instance", "i-0example0000000a1\n")) is None  # fullmatch, not match
    # [0-9], not \d: \d also matches non-ASCII digits such as "١" (Arabic-Indic one).
    assert console_link(res("instance", "i-0example0000000a1", region="eu-central-١")) is None
    assert console_link(res("elb-v2", ALB.replace("111122223333", "١١١١٢٢٢٢٣٣٣٣"))) is None


def test_empty_report_renders():
    page = render_page(make_report(), CFG)
    assert "<h2>By student</h2><p>Nothing running.</p>" in page


def test_incomplete_is_one_small_banner_on_top():
    messages = ["eks in us-east-1 (AccessDenied)", "CloudTrail in eu-central-1 (Throttling)"]
    page = render_page(make_report(incomplete=messages), CFG)
    banner = (
        '<p class="banner" id="incomplete">Incomplete: eks in us-east-1 (AccessDenied); '
        "CloudTrail in eu-central-1 (Throttling)</p>"
    )
    assert page.count('id="incomplete"') == 1
    assert page.index(banner) < page.index("<h2>By student</h2>")
    assert 'id="incomplete"' not in render_page(make_report(), CFG)


def test_page_leaves_out_the_teacher_diagnostics():
    old = make_row(
        res("instance", "i-0example0000000a1", state="running", public_ip=True), age_days=30
    )
    page = render_page(make_report([old], unmapped_users=3, roster_loaded=False), CFG)
    # They belong in the local verify.md, never on the page every student reads.
    assert "unmapped" not in page
    assert "roster: not loaded" not in page


def test_student_wording():
    row = make_row(res("eip", "eipalloc-0example00000a1"), age_days=30)
    page = render_page(make_report([row]), CFG)
    assert "<title>Forgotten AWS resources</title>" in page
    assert (
        "<h1>What did I forget to delete?</h1>"
        "<p>Type your username, delete what you no longer need.</p>"
    ) in page
    assert "<h2>By student</h2>" in page
    assert "<h2>No owner found (ask before deleting)</h2>" in page
    assert "<h2>Cluster gone (ask a teacher)</h2>" in page
    assert (
        "Visible to everyone with access to this AWS account. "
        "Please don't share screenshots outside the course." in page
    )


def test_cards_alphabetical_with_the_check_rank_for_filters():
    calm = make_row(
        res("instance", "i-1", state="running", public_ip=False), user="student-a", age_days=1
    )
    busy = make_row(res("volume", "vol-1"), user="student-b", age_days=10)
    page = render_page(make_report([busy, calm]), CFG)
    assert page.index('id="card-student-a"') < page.index('id="card-student-b"')
    assert (
        'id="card-student-a" data-cohort="DV_FT" data-rank="1" data-alpha="0" data-checks="0"'
        in page
    )
    assert (
        'id="card-student-b" data-cohort="DV_FT" data-rank="0" data-alpha="1" data-checks="1"'
        in page
    )
    assert 'var key = c === "" && u === "" ? "data-alpha" : "data-rank";' in JS
    assert "order(key);" in JS


def test_cohort_filter_options_and_data_attributes():
    row = make_row(res("instance", "i-1", state="running"), cohort="DV_FT")
    page = render_page(make_report([row]), CFG)
    assert '<option value="DV_FT">DV_FT</option>' in page
    assert 'data-cohort="DV_FT"' in page


def test_stale_banner_is_hidden_with_the_generated_time():
    page = render_page(make_report(), CFG)
    assert (
        '<p class="banner stale" id="stale" data-generated="2026-10-02T12:00:00Z" hidden>'
        "This page is more than a day old: the scheduled runs may have failed. Tell a teacher.</p>"
    ) in page
    assert page.index('id="stale"') < page.index("<h2>By student</h2>")
    assert "26 * 3600 * 1000" in JS


def test_card_columns_and_phone_layout():
    row = make_row(res("instance", "i-0example0000000a1", name="web", state="running"), age_days=2)
    page = render_page(make_report([row]), CFG)
    assert "<th>Resource</th><th>Type</th><th>Region</th><th>Age</th><th>Check</th>" in page
    assert (
        ">i-0example0000000a1 (web)" + NEW_TAB + "</a></td><td>EC2 instance</td>"
        '<td>eu-central-1</td><td class="num">2 d</td><td></td></tr>'
    ) in page
    assert "@media (max-width:600px)" in CSS


def test_check_marks_in_card_rows():
    old_public = make_row(
        res("instance", "i-0example0000000a1", state="running", public_ip=True), age_days=30
    )
    fresh = make_row(res("volume", "vol-0example0000000a1"), age_days=1)
    card = Card("student-a", "DV_FT", [old_public, fresh])
    page = render_page(make_report([old_public, fresh], cards=[card]), CFG)
    assert '<tr class="marked"><td><a href="' in page
    assert (
        '<td><strong class="check">Check this:</strong> '
        '<span class="muted">older than 4 days, running with a public IP</span></td>'
    ) in page
    assert page.count('<tr class="marked">') == 1
    assert "tr.marked{background:var(--bad-bg)}" in CSS
    assert page.count('class="check"') == 1
    assert '<p class="muted">1 to check · 2 resources</p>' in page


def test_all_clear_counts_card_marks():
    assert 'getAttribute("data-checks")' in JS
    assert "data-action" not in JS
    page = render_page(make_report(), CFG)
    assert (
        '<p class="banner ok" id="all-clear" hidden>'
        "All clear: nothing to check for this username.</p>"
    ) in page


def test_find_me_controls_and_user_attributes():
    row = make_row(res("eip", "eipalloc-1"), age_days=30)
    page = render_page(make_report([row]), CFG)
    assert '<input id="find-me" type="text"' in page
    assert '<button id="forget-me" type="button">Forget me</button>' in page
    assert '<p class="banner" id="no-match" hidden>' in page
    assert page.count('data-user="student-a"') == 1  # the card only


def test_find_me_script_keeps_the_name_out_of_html_and_the_url():
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "user="):
        assert sink not in JS
    assert 'getAttribute("data-user")' in JS
    assert all("try {" in line for line in JS.splitlines() if "localStorage" in line)
    for arg in re.findall(r"querySelector(?:All)?\(([^)]*)\)", JS):
        assert arg.startswith('"')  # only literal selectors, never the typed name


def test_malicious_username_is_escaped_in_data_user():
    evil = 'x" onmouseover="alert(1)'
    row = make_row(res("eip", "eipalloc-1"), user=evil)
    page = render_page(make_report([row]), CFG)
    assert 'data-user="x&quot; onmouseover=&quot;alert(1)"' in page
    assert 'data-user="x" onmouseover' not in page


def test_showing_line_follows_find_me():
    row = make_row(res("eip", "eipalloc-1"), age_days=30)
    page = render_page(make_report([row]), CFG)
    assert page.index('id="cohort"') < page.index('id="find-me"')
    assert page.index('id="find-me"') < page.index('id="showing"')
    assert '<label for="find-me">Find me</label>' in page


def test_show_all_does_not_forget_the_name():
    assert 'document.getElementById("show-all")' in JS
    handler = JS[JS.index("showAll.addEventListener") :]
    handler = handler[: handler.index("});")]
    assert 'who.value = ""' in handler
    assert "localStorage" not in handler
    assert 'getElementById("showing-name")' in JS
    assert re.search(r"showingName\.textContent\s*=", JS)


def test_no_owner_section_has_an_id_for_the_filter():
    row = make_row(res("eip", "eipalloc-1"), user=None)
    page = render_page(make_report([row], unknown=[row]), CFG)
    assert '<section id="no-owner">' in page
    assert 'getElementById("no-owner")' in JS


def test_cluster_gone_section_hides_while_filtered():
    row = make_row(res("eks-cluster", "demo-cluster", region="us-east-1"), user=None)
    page = render_page(make_report([row], orphaned=[row]), CFG)
    assert '<section id="cluster-gone">' in page
    assert 'getElementById("cluster-gone")' in JS
    assert "clusterGone.hidden = u !== " in JS


def test_find_me_input_is_debounced_and_sorts_only_on_key_change():
    assert "setTimeout(" in JS
    assert "clearTimeout(" in JS
    assert "sortedBy" in JS
    lines = JS.splitlines()
    calls = [
        i for i, line in enumerate(lines) if "order(" in line and "function order(" not in line
    ]
    assert calls
    for i in calls:
        assert re.search(r"if \(.*sortedBy", lines[i - 1])


def test_generated_time_names_central_european_time_with_its_offset():
    summer = render_page(make_report(), CFG)  # 2026-10-02 12:00 UTC: summer time, UTC+2
    assert (
        'Generated <time id="generated" datetime="2026-10-02T12:00:00Z">2026-10-02 12:00 UTC</time>'
        ' (<span id="local-time">14:00 Central European time, UTC+2</span>)'
    ) in summer
    winter = render_page(make_report(generated_at=datetime(2026, 1, 15, 6, 0, tzinfo=UTC)), CFG)
    assert '(<span id="local-time">07:00 Central European time, UTC+1</span>)' in winter
    assert "Berlin" not in summer


def test_script_shows_the_generated_time_in_the_viewers_zone():
    assert 'getElementById("generated")' in JS
    assert 'getAttribute("datetime")' in JS
    for option in ('hourCycle: "h23"', 'timeZoneName: "shortOffset"'):
        assert option in JS
    assert "your time, " in JS
    # Older browsers reject shortOffset: the server text stays.
    line = next(line for line in JS.splitlines() if "Intl.DateTimeFormat" in line)
    assert "try {" in line


def test_console_links_say_they_open_a_new_tab():
    row = make_row(res("instance", "i-0example0000000a1", state="running"), age_days=2)
    page = render_page(make_report([row]), CFG)
    assert (
        '<h2>By student</h2><p class="muted">Resource names open the AWS console in a new tab.</p>'
    ) in page
    assert page.count(NEW_TAB) == page.count('target="_blank"') == 1
    assert ".vh{" in CSS  # visually hidden, still read out
    assert "<h2>By student</h2><p>Nothing running.</p>" in render_page(make_report(), CFG)


def _two_cards():
    calm = make_row(
        res("instance", "i-1", state="running", public_ip=False), user="student-a", age_days=1
    )
    busy = make_row(res("volume", "vol-1"), user="student-b", age_days=10)
    return render_page(make_report([busy, calm]), CFG)


def test_only_marked_checkbox_sits_between_cohort_and_find_me():
    page = _two_cards()
    assert (
        '<p><input id="only-checks" type="checkbox"> '
        '<label for="only-checks">Only show students with something to check</label></p>'
    ) in page
    assert page.index('id="cohort"') < page.index('id="only-checks"') < page.index('id="find-me"')
    assert 'getElementById("only-checks")' in JS
    assert 'only.addEventListener("change", apply)' in JS
    assert 'only.checked && el.getAttribute("data-checks") === "0"' in JS
    assert "localStorage" not in "".join(line for line in JS.splitlines() if "only" in line)


def test_overview_line_counts_students_and_announces_changes():
    page = _two_cards()
    assert (
        '<nav id="overview" aria-label="On this page"><p>'
        '<span id="result-count" role="status" aria-live="polite">'
        "2 students · 1 with something to check</span>"
    ) in page
    assert page.index('id="showing"') < page.index('id="overview"')
    assert page.index('id="overview"') < page.index("<h2>By student</h2>")
    assert '"Showing " + shown + " of "' in JS
    assert 'overview.hidden = u !== ""' in JS
    assert "resultCount.textContent !== text" in JS  # announce only real changes


def test_overview_links_to_the_bottom_sections_by_count():
    lost = make_row(res("eip", "eipalloc-1"), user=None)
    page = render_page(make_report([lost], unknown=[lost]), CFG)
    assert (
        '<span id="result-count" role="status" aria-live="polite">0 students · 0 with something'
        ' to check</span> · <a href="#no-owner">1 with no owner</a> · 0 cluster gone</p></nav>'
    ) in page
    gone = make_row(res("eks-cluster", "demo-cluster", region="us-east-1"), user=None)
    page = render_page(make_report([gone], orphaned=[gone, gone]), CFG)
    assert ' · 0 with no owner · <a href="#cluster-gone">2 cluster gone</a></p></nav>' in page


def test_back_to_top_link_before_the_footer():
    page = render_page(make_report(), CFG)
    assert '<main id="top">' in page
    assert '</section><p><a href="#top">Back to top</a></p><footer>' in page


def test_a_url_hash_that_is_not_a_cohort_keeps_the_current_cohort():
    # #no-owner, #cluster-gone and #top are links, not cohorts: filtering by them would hide every card.
    assert "select.options[i].value === h" in JS
    assert "return current;" in JS
