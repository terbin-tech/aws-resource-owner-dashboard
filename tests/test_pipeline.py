from datetime import timedelta

import dashboard.pipeline as pipeline
from dashboard.attribution import Principal
from dashboard.collectors.base import Incomplete
from dashboard.config import Config
from dashboard.people import IamUser
from tests.helpers import NOW, T0, creation, make_scan, res

ALB = "arn:aws:elasticloadbalancing:eu-central-1:111122223333:loadbalancer/app/demo-alb/0123456789abcdef"


def _scan():
    inst = res(
        "instance",
        "i-0example0000000a1",
        state="running",
        created_at=NOW - timedelta(hours=2),
    )
    old = T0 - timedelta(days=30)
    vol = res(
        "volume",
        "vol-0example0000000a9",
        attached_instance=None,
        tags={"Name": "old"},
        created_at=old,
    )
    lb = res(
        "elb-v2",
        ALB,
        name="k8s-x",
        tags={"elbv2.k8s.aws/cluster": "gone"},
        created_at=old,
    )
    asg = res("asg", "demo-asg")
    return make_scan(
        resources=[inst, vol, lb, asg],
        creations=[creation("instance", inst.id, when=inst.created_at)],
        users=[IamUser("student-a", T0), IamUser("student-b", T0), IamUser("student-c", T0)],
        members={"DV_FT": ["student-a", "student-b"]},
    )


def test_build_report_rows_cards_and_sections():
    report = pipeline.build_report(_scan(), roster=None)

    # An Auto Scaling group only links its instances to an owner; it gets no row.
    assert [r.resource.kind for r in report.rows] == ["instance", "volume", "elb-v2"]
    assert [c.name for c in report.cards if c.rows] == ["student-a"]
    # student-b is a cohort member with nothing running.
    assert [c.name for c in report.cards if not c.rows] == ["student-b"]
    assert report.cohorts == ["DV_FT"]
    assert [r.resource.id for r in report.unknown] == ["vol-0example0000000a9"]
    assert [r.resource.id for r in report.orphaned] == [ALB]
    assert report.unmapped_users == 0
    assert report.roster_loaded is False


def test_unmapped_counts_owning_users_without_cohort():
    data = _scan()
    data.members = {}
    assert pipeline.build_report(data, roster=None).unmapped_users == 1


def test_failed_lookup_and_incomplete_collectors_become_banners():
    data = make_scan(
        resources=[res("instance", "i-0example0000000a1", state="running")],
        failed=("eu-central-1",),
    )
    data.incomplete = [Incomplete("eks", "us-east-1", "AccessDenied")]
    data.errors = ["iam group DV_FT: NoSuchEntity"]
    report = pipeline.build_report(data, roster=None)
    assert report.incomplete == [
        "eks in us-east-1 (AccessDenied)",
        "CloudTrail in eu-central-1 (AccessDenied)",
        "iam group DV_FT: NoSuchEntity",
    ]
    assert report.rows[0].owner.method == "lookup-failed"


def test_eks_collector_failure_is_passed_to_attribution():
    node = res(
        "instance",
        "i-0example0000000a1",
        state="running",
        tags={"eks:cluster-name": "demo-eks"},
    )
    data = make_scan(resources=[node])
    data.incomplete = [Incomplete("eks", "eu-central-1", "AccessDenied")]
    assert pipeline.build_report(data, roster=None).rows[0].owner.method == "unknown"


def test_scan_adds_global_lookup_when_us_east_1_not_configured(monkeypatch):
    calls = []

    class Aws:
        def client(self, service, region):
            return (service, region)

    monkeypatch.setattr(pipeline, "run_collectors", lambda aws, regions: ([], []))
    monkeypatch.setattr(pipeline, "fetch_iam", lambda aws, groups: ([], {}, []))

    def fake_lookup(client, region, names, start, end):
        calls.append((client, names, end - start))
        return pipeline.LookupResult(region)

    monkeypatch.setattr(pipeline, "lookup_creations", fake_lookup)
    data = pipeline.scan(Aws(), Config(regions=("eu-central-1",)), NOW)

    assert sorted((client[1], tuple(names)) for client, names, _ in calls) == [
        ("eu-central-1", tuple(pipeline.event_names())),
        ("us-east-1", ("CreateUser",)),
    ]
    assert all(span == timedelta(days=90) for _, _, span in calls)
    assert data.errors == []


def test_roster_maps_an_owner_whose_iam_user_is_gone():
    inst = res("instance", "i-0example0000000a1", state="running")
    data = make_scan(resources=[inst], creations=[creation("instance", inst.id, "gone-user")])
    report = pipeline.build_report(data, roster={"gone-user": ("student-a", "DV_FT")})
    (card,) = [c for c in report.cards if c.rows]
    assert (card.name, card.cohort) == ("student-a", "DV_FT")
    assert report.unmapped_users == 0


def test_owner_without_a_name_still_gets_a_no_owner_row():
    inst = res("instance", "i-0example0000000a1", state="running")
    nameless = creation("instance", inst.id, principal=Principal("user", ""))
    report = pipeline.build_report(make_scan(resources=[inst], creations=[nameless]), roster=None)
    assert [r.resource.id for r in report.unknown] == [inst.id]


def test_scan_passes_iam_errors_through(monkeypatch):
    class Aws:
        def client(self, service, region):
            return (service, region)

    monkeypatch.setattr(pipeline, "run_collectors", lambda aws, regions: ([], []))
    monkeypatch.setattr(
        pipeline, "fetch_iam", lambda aws, groups: ([], {}, ["iam group DV_FT: NoSuchEntity"])
    )
    monkeypatch.setattr(
        pipeline,
        "lookup_creations",
        lambda client, region, names, start, end: pipeline.LookupResult(region),
    )
    data = pipeline.scan(Aws(), Config(), NOW)
    assert data.errors == ["iam group DV_FT: NoSuchEntity"]
