from dashboard.config import Config
from dashboard.render import checks
from tests.helpers import make_row, res

CFG = Config(max_age_days=4)


def test_anything_older_than_max_age_days_gets_a_mark():
    for kind, attrs in [
        ("instance", {"state": "stopped"}),
        ("volume", {}),
        ("eip", {}),
        ("nat", {}),
    ]:
        assert checks(make_row(res(kind, "x", **attrs), age_days=5), CFG) == ("older than 4 days",)
    assert checks(make_row(res("volume", "vol-1"), age_days=4), CFG) == ()
    assert checks(make_row(res("volume", "vol-1"), age_days=None), CFG) == ()


def test_running_instance_with_a_public_ip_gets_a_mark():
    new = make_row(res("instance", "i-1", state="running", public_ip=True), age_days=1)
    assert checks(new, CFG) == ("running with a public IP",)
    old = make_row(res("instance", "i-1", state="running", public_ip=True), age_days=9)
    assert checks(old, CFG) == ("older than 4 days", "running with a public IP")
    stopped = make_row(res("instance", "i-1", state="stopped", public_ip=True), age_days=1)
    private = make_row(res("instance", "i-1", state="running", public_ip=False), age_days=1)
    assert checks(stopped, CFG) == checks(private, CFG) == ()


def test_a_young_elastic_ip_gets_no_mark():
    eip = make_row(res("eip", "eipalloc-1"), age_days=1)
    assert checks(eip, CFG) == ()


def _cluster(**attrs):
    return make_row(res("eks-cluster", "arn", name="demo", version="1.32", **attrs), age_days=1)


def test_eks_cluster_past_standard_support_gets_a_mark():
    for status in ("EXTENDED_SUPPORT", "UNSUPPORTED"):
        assert checks(_cluster(version_status=status), CFG) == (
            "old Kubernetes version 1.32, costs extra every hour",
        )
    assert checks(_cluster(version_status="STANDARD_SUPPORT"), CFG) == ()


def test_eks_cluster_with_unknown_support_status_gets_a_mark():
    # A failed version lookup must never hide the costliest item.
    assert checks(_cluster(version_status=None), CFG) == (
        "support status unknown: check its Kubernetes version, old ones cost extra every hour",
    )


def test_eks_mark_comes_after_the_age_mark():
    old = make_row(
        res("eks-cluster", "arn", version="1.32", version_status="EXTENDED_SUPPORT"), age_days=9
    )
    assert checks(old, CFG) == (
        "older than 4 days",
        "old Kubernetes version 1.32, costs extra every hour",
    )


def test_attached_volume_gets_no_mark():
    attached = make_row(res("volume", "vol-1", attached_instance="i-1"), age_days=30)
    assert checks(attached, CFG) == ()


def test_loose_volume_older_than_max_age_gets_a_mark():
    loose = make_row(res("volume", "vol-1", attached_instance=None), age_days=30)
    assert checks(loose, CFG) == ("older than 4 days",)
