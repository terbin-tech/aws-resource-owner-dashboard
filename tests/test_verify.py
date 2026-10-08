from dataclasses import replace
from datetime import timedelta

from dashboard.people import IamUser
from dashboard.pipeline import build_report
from dashboard.verify import name_hint, render_verify
from tests.helpers import NOW, T0, creation, make_row, make_scan, res


def test_name_hint_matches_whole_username_only():
    users = ["student-a", "abc"]
    assert name_hint(make_row(res("instance", "i-1", name="student-a-web")), users) == "student-a"
    assert (
        name_hint(make_row(res("instance", "i-1", tags={"Name": "x-student-a"})), users)
        == "student-a"
    )
    assert name_hint(make_row(res("instance", "i-1", name="student-ab")), users) is None
    assert name_hint(make_row(res("instance", "i-1", name="abc")), users) is None  # too short


def test_report_sections():
    agree = res(
        "instance",
        "i-0example0000000a1",
        name="student-a-web",
        state="running",
    )
    disagree = res(
        "instance",
        "i-0example0000000a2",
        name="student-b-db",
        state="running",
    )
    data = make_scan(
        resources=[agree, disagree],
        creations=[creation("instance", agree.id), creation("instance", disagree.id)],
        users=[IamUser("student-a", T0), IamUser("student-b", T0)],
    )
    data.lookups[1].stats = {"RunInstances:instance": [3, 3], "CreateNatGateway:nat": [2, 0]}
    data.lookups[1].duration_s = 12.5
    text = render_verify(data, build_report(data, None))

    assert "| eu-central-1 | 12.5 |" in text
    assert "| CreateNatGateway:nat | eu-central-1 | 2 | 0 | PATH MISMATCH |" in text
    assert "| RunInstances:instance | eu-central-1 | 3 | 3 | OK |" in text
    assert "Agreements: 1" in text
    assert "| i-0example0000000a2 | eu-central-1 | student-b | student-a | event |" in text
    assert "never commit" in text.lower()
    assert "## 5. Incomplete" in text
    assert "## 6. Mapping" in text
    assert "- users with resources but no cohort: 1" in text  # student-a, no groups
    assert "None" not in text


def test_spot_check_lists_the_oldest_first():
    vols = [
        res("volume", f"vol-0example0000000a{i}", created_at=NOW - timedelta(days=days))
        for i, days in ((1, 2), (2, 30), (3, 9))
    ]
    data = make_scan(
        resources=vols,
        creations=[creation("volume", v.id) for v in vols],
        users=[IamUser("student-a", T0)],
    )
    text = render_verify(data, build_report(data, None))
    spot = text[text.index("## 7. Spot check") :]
    assert spot.index("a2 ") < spot.index("a3 ") < spot.index("a1 ")  # 30, 9, 2 days
    assert "- roster: not loaded (absent or denied)" in text


def test_counts_disks_matched_to_their_launch():
    spare = res(
        "volume",
        "vol-0example0000000b1",
        created_at=T0,
        attached_instance=None,
        availability_zone="eu-central-1a",
        from_snapshot=False,
    )
    launch = replace(
        creation("instance", "i-0example0000000b1"),
        event_name="RunInstances",
        availability_zone="eu-central-1a",
        blank_volumes=1,
    )
    data = make_scan(resources=[spare], creations=[launch], users=[IamUser("student-a", T0)])
    text = render_verify(data, build_report(data, None))
    assert "- disks matched to their launch (same zone, within 10 s): 1" in text


def test_counts_disks_linked_by_a_detach_event():
    spare = res(
        "volume",
        "vol-0example0000000c1",
        created_at=T0,
        attached_instance=None,
        availability_zone="eu-central-1a",
        from_snapshot=True,
    )
    detach = replace(
        creation("volume-detach", spare.id),
        event_name="DetachVolume",
        instance_id="i-0example0000000c1",
    )
    data = make_scan(
        resources=[spare],
        creations=[detach, creation("instance", "i-0example0000000c1")],
        users=[IamUser("student-a", T0)],
    )
    text = render_verify(data, build_report(data, None))
    assert "- disks linked by a detach event: 1" in text


def test_detach_links_are_not_counted_as_creations():
    detach = replace(
        creation("volume-detach", "vol-0example0000000c1"),
        event_name="DetachVolume",
        instance_id="i-0example0000000c1",
    )
    data = make_scan(creations=[detach, creation("instance", "i-0example0000000c1")])
    text = render_verify(data, build_report(data, None))
    assert "| eu-central-1 | 0.0 | 1 | no |" in text
