from dataclasses import replace
from datetime import timedelta

import pytest

from dashboard.attribution import Principal, attribute, k8s_cluster_name
from tests.helpers import T0, creation, res

R = "eu-central-1"
CLUSTER = "arn:aws:eks:eu-central-1:111122223333:cluster/demo-eks"
NG = "arn:aws:eks:eu-central-1:111122223333:nodegroup/demo-eks/ng-1/00000000-1111-2222-3333-aaaabbbbcccc"
ALB = "arn:aws:elasticloadbalancing:eu-central-1:111122223333:loadbalancer/app/demo-alb/0123456789abcdef"
SERVICE = Principal("service", "AWSServiceRoleForAutoScaling")


CONTROLLER = Principal("role", "alb-controller-irsa", verified=False)


def owner_of(resources, creations, key, failed=frozenset(), eks_unavailable=frozenset()):
    return attribute(resources, creations, failed, eks_unavailable, lookback_days=90)[key]


def test_direct_event_owner():
    r = res("volume", "vol-0example0000000a1")
    o = owner_of([r], [creation("volume", r.id)], r.key)
    assert (o.principal.name, o.method) == ("student-a", "event")


def test_reused_name_picks_event_near_creation():
    current = res("asg", "demo-asg", created_at=T0)
    old = creation("asg", "demo-asg", "student-b", when=T0 - timedelta(days=20))
    new = creation("asg", "demo-asg", "student-a", when=T0 + timedelta(minutes=1))
    assert owner_of([current], [old, new], current.key).principal.name == "student-a"


def test_reused_name_without_matching_event_is_unknown():
    current = res("asg", "demo-asg", created_at=T0)
    old = creation("asg", "demo-asg", "student-b", when=T0 - timedelta(days=20))
    assert owner_of([current], [old], current.key).method == "unknown"


def test_eks_node_goes_to_nodegroup_creator():
    cluster = res("eks-cluster", CLUSTER, name="demo-eks")
    nodegroup = res("eks-nodegroup", NG, name="ng-1", cluster_name="demo-eks")
    node = res(
        "instance",
        "i-0example0000000a1",
        tags={"eks:cluster-name": "demo-eks", "eks:nodegroup-name": "ng-1"},
    )
    creations = [
        creation("eks-cluster", CLUSTER, "student-b"),
        creation("eks-nodegroup", NG, "student-a"),
        creation("instance", node.id, principal=SERVICE),
    ]
    o = owner_of([cluster, nodegroup, node], creations, node.key)
    assert (o.principal.name, o.method, o.detail) == (
        "student-a",
        "indirect",
        "via node group ng-1",
    )


def test_eks_tag_for_missing_cluster_is_orphaned():
    node = res("instance", "i-0example0000000a1", tags={"eks:cluster-name": "gone"})
    assert owner_of([node], [], node.key).method == "orphaned"


def test_k8s_load_balancer_goes_to_cluster_creator():
    cluster = res("eks-cluster", CLUSTER, name="demo-eks")
    lb = res("elb-v2", ALB, name="k8s-demo", tags={"elbv2.k8s.aws/cluster": "demo-eks"})
    o = owner_of(
        [cluster, lb],
        [creation("eks-cluster", CLUSTER, "student-b"), creation("elb-v2", ALB, principal=SERVICE)],
        lb.key,
    )
    assert (o.principal.name, o.detail) == ("student-b", "via Kubernetes cluster demo-eks")


def test_k8s_resource_of_deleted_cluster_is_orphaned():
    lb = res("elb-v2", ALB, tags={"kubernetes.io/cluster/old-eks": "owned"})
    o = owner_of([lb], [], lb.key)
    assert (o.method, o.detail) == ("orphaned", "Kubernetes cluster old-eks no longer exists")


def test_k8s_load_balancer_created_by_controller_role_goes_to_cluster_creator():
    cluster = res("eks-cluster", CLUSTER, name="demo-eks")
    lb = res("elb-v2", ALB, name="k8s-demo", tags={"elbv2.k8s.aws/cluster": "demo-eks"})
    creations = [
        creation("eks-cluster", CLUSTER, "student-b"),
        creation("elb-v2", ALB, principal=CONTROLLER),
    ]
    o = owner_of([cluster, lb], creations, lb.key)
    assert (o.principal.name, o.method) == ("student-b", "indirect")


def test_role_created_resource_without_tags_keeps_the_unverified_role():
    inst = res("instance", "i-0example0000000a1")
    o = owner_of([inst], [creation("instance", inst.id, principal=CONTROLLER)], inst.key)
    assert (o.principal, o.method) == (CONTROLLER, "event")


def test_unattached_csi_volume_without_cluster_name_is_unknown():
    vol = res("volume", "vol-0example0000000a2", tags={"ebs.csi.aws.com/cluster": "true"})
    o = owner_of([vol], [], vol.key)
    assert (o.method, o.detail) == ("unknown", "Kubernetes resource without a cluster name tag")


def test_attached_csi_volume_without_cluster_name_goes_to_instance():
    inst = res("instance", "i-0example0000000a1")
    vol = res(
        "volume",
        "vol-0example0000000a2",
        tags={"ebs.csi.aws.com/cluster": "true"},
        attached_instance=inst.id,
    )
    o = owner_of(
        [inst, vol],
        [creation("instance", inst.id), creation("volume", vol.id, principal=CONTROLLER)],
        vol.key,
    )
    assert (o.principal.name, o.detail) == ("student-a", "via its instance")


def test_eks_collector_failure_gives_unknown_not_orphaned():
    node = res("instance", "i-0example0000000a1", tags={"eks:cluster-name": "demo-eks"})
    o = owner_of([node], [], node.key, eks_unavailable={R})
    assert (o.method, o.detail) == ("unknown", "EKS cluster list unavailable in eu-central-1")


def test_asg_instance_goes_to_asg_creator():
    group = res("asg", "demo-asg")
    inst = res("instance", "i-0example0000000a1", tags={"aws:autoscaling:groupName": "demo-asg"})
    o = owner_of(
        [group, inst],
        [
            creation("asg", "demo-asg", "student-b"),
            creation("instance", inst.id, principal=SERVICE),
        ],
        inst.key,
    )
    assert (o.principal.name, o.detail) == ("student-b", "via Auto Scaling group demo-asg")


def test_root_volume_goes_to_instance_creator():
    inst = res("instance", "i-0example0000000a1")
    vol = res("volume", "vol-0example0000000a1", attached_instance=inst.id)
    o = owner_of([inst, vol], [creation("instance", inst.id)], vol.key)
    assert (o.principal.name, o.method) == ("student-a", "indirect")


def test_nat_eip_goes_to_nat_creator():
    nat = res("nat", "nat-0example0000000a1")
    eip = res("eip", "eipalloc-0example00000a1", created_at=None, nat_gateway_id=nat.id)
    assert owner_of([nat, eip], [creation("nat", nat.id)], eip.key).principal.name == "student-a"


def test_public_ip_on_alb_goes_to_alb_creator():
    lb = res("elb-v2", ALB, name="demo-alb")
    ip = res(
        "public-ip",
        "eni-0example0000000a2",
        created_at=None,
        parent_kind="elb-v2",
        parent_id="app/demo-alb/0123456789abcdef",
    )
    assert owner_of([lb, ip], [creation("elb-v2", ALB)], ip.key).principal.name == "student-a"


def test_unknown_shows_name_tag_as_guess():
    inst = res("instance", "i-0example0000000a1", tags={"Name": "student-a-web"})
    o = owner_of([inst], [], inst.key)
    assert (o.method, o.guess) == ("unknown", "student-a-web")


def test_unknown_names_the_configured_lookback():
    inst = res("instance", "i-0example0000000a1")
    owners = attribute([inst], [], lookback_days=30)
    assert owners[inst.key].detail == "no creation event in the last 30 days"


def test_failed_region_is_lookup_failed_not_unknown():
    inst = res("instance", "i-0example0000000a1")
    assert owner_of([inst], [], inst.key, failed={R}).method == "lookup-failed"


def test_unverified_role_stays_unverified_through_indirect_rules():
    inst = res("instance", "i-0example0000000a1")
    vol = res("volume", "vol-0example0000000a1", attached_instance=inst.id)
    role = Principal("role", "lab-role", verified=False)
    o = owner_of([inst, vol], [creation("instance", inst.id, principal=role)], vol.key)
    assert o.principal.verified is False


def test_k8s_cluster_name_variants():
    assert k8s_cluster_name({"elbv2.k8s.aws/cluster": "a"}) == "a"
    assert k8s_cluster_name({"kubernetes.io/cluster/b": "owned"}) == "b"
    assert k8s_cluster_name({"karpenter.sh/nodepool": "x", "eks:eks-cluster-name": "c"}) == "c"
    assert (
        k8s_cluster_name({"ebs.csi.aws.com/cluster": "true", "ebs.csi.aws.com/cluster-name": "d"})
        == "d"
    )
    assert k8s_cluster_name({"ebs.csi.aws.com/cluster": "true", "KubernetesCluster": "e"}) == "e"
    assert k8s_cluster_name({"ebs.csi.aws.com/cluster": "true"}) == ""
    assert k8s_cluster_name({"Name": "x"}) is None


def _launch(
    user="student-a",
    *,
    when=T0,
    az="eu-central-1a",
    blank=1,
    instance="i-0example0000000b1",
    principal=None,
):
    return replace(
        creation("instance", instance, user, when=when, principal=principal),
        event_name="RunInstances",
        availability_zone=az,
        blank_volumes=blank,
    )


def _spare(**overrides):
    attrs = {
        "attached_instance": None,
        "availability_zone": "eu-central-1a",
        "from_snapshot": False,
    }
    return res(
        "volume",
        "vol-0example0000000b1",
        created_at=T0 + timedelta(seconds=1),
        **(attrs | overrides),
    )


def test_extra_disk_of_a_gone_instance_goes_to_whoever_launched_it():
    vol = _spare()
    o = owner_of([vol], [_launch()], vol.key)
    assert (o.principal.name, o.method, o.detail) == (
        "student-a",
        "indirect",
        "made with an instance that's gone",
    )
    assert o.principal.verified and not o.guess


def test_extra_disk_detached_from_a_live_instance_names_it():
    vol = _spare()
    inst = res("instance", "i-0example0000000b1")
    o = owner_of([vol, inst], [_launch()], vol.key)
    assert (o.principal.name, o.detail) == ("student-a", "made with instance i-0example0000000b1")


def test_one_person_launching_several_instances_still_matches():
    vol = _spare()
    launches = [_launch(), _launch(instance="i-0example0000000b2")]
    assert owner_of([vol], launches, vol.key).principal.name == "student-a"


@pytest.mark.parametrize(
    ("launches", "volume"),
    [
        ([_launch(), _launch("student-b", instance="i-0example0000000b2")], {}),  # two people
        ([_launch(when=T0 - timedelta(seconds=10))], {}),  # 11 s apart
        ([_launch(az="eu-central-1b")], {}),  # other zone
        ([_launch(blank=0)], {}),  # the launch asked for no extra disk
        ([_launch()], {"from_snapshot": True}),  # a root disk, made from the AMI
        ([_launch(principal=SERVICE)], {}),  # an AWS service launched it
        ([_launch()], {"availability_zone": None}),
    ],
)
def test_extra_disk_without_a_clear_launch_stays_unknown(launches, volume):
    vol = _spare(**volume)
    o = owner_of([vol], launches, vol.key)
    assert (o.principal, o.method, o.detail) == (
        None,
        "unknown",
        "no creation event in the last 90 days",
    )


def test_a_disk_with_its_own_event_ignores_launches():
    vol = _spare()
    own = creation("volume", vol.id, "student-b")
    assert owner_of([vol], [own, _launch()], vol.key).principal.name == "student-b"


def _detached(instance="i-0example0000000c1", vol="vol-0example0000000b1", when=T0):
    return replace(
        creation("volume-detach", vol, "student-z", when=when),
        event_name="DetachVolume",
        instance_id=instance,
    )


def _launch_of(instance, user="student-a", principal=None):
    return replace(
        creation("instance", instance, user, principal=principal), event_name="RunInstances"
    )


def test_detached_disk_of_a_gone_instance_goes_to_whoever_launched_it():
    vol = _spare(from_snapshot=True)  # a root disk: no launch match possible
    o = owner_of([vol], [_detached(), _launch_of("i-0example0000000c1")], vol.key)
    assert (o.principal.name, o.method, o.detail) == (
        "student-a",
        "indirect",
        "detached from an instance that's gone",
    )


def test_detached_disk_of_a_live_instance_names_it():
    vol = _spare()
    inst = res("instance", "i-0example0000000c1")
    o = owner_of([vol, inst], [_detached(), _launch_of(inst.id)], vol.key)
    assert (o.principal.name, o.detail) == (
        "student-a",
        "detached from instance i-0example0000000c1",
    )


def test_detach_record_wins_over_a_launch_match():
    vol = _spare()
    events = [_detached(), _launch_of("i-0example0000000c1", "student-b"), _launch()]
    assert owner_of([vol], events, vol.key).principal.name == "student-b"


def test_detached_twice_from_one_persons_instances_still_matches():
    vol = _spare(from_snapshot=True)
    events = [
        _detached(),
        _detached("i-0example0000000c2", when=T0 + timedelta(hours=1)),
        _launch_of("i-0example0000000c1"),
        _launch_of("i-0example0000000c2"),
    ]
    assert owner_of([vol], events, vol.key).principal.name == "student-a"


@pytest.mark.parametrize(
    "events",
    [
        [
            _detached(),
            _detached("i-0example0000000c2"),
            _launch_of("i-0example0000000c1"),
            _launch_of("i-0example0000000c2", "student-b"),
        ],  # two people
        [_detached()],  # the instance's launch is older than 90 days
        [_detached(), _launch_of("i-0example0000000c1", principal=SERVICE)],
        [
            _detached("i-0example0000000c1", vol="vol-0example0000000zz"),
            _launch_of("i-0example0000000c1"),
        ],
    ],
)
def test_detached_disk_without_one_clear_launcher_stays_unknown(events):
    vol = _spare(from_snapshot=True)
    o = owner_of([vol], events, vol.key)
    assert (o.principal, o.method) == (None, "unknown")


def test_attached_disk_ignores_old_detach_records():
    inst = res("instance", "i-0example0000000a1")
    vol = _spare(attached_instance=inst.id)
    events = [
        creation("instance", inst.id),
        _detached(),
        _launch_of("i-0example0000000c1", "student-b"),
    ]
    o = owner_of([inst, vol], events, vol.key)
    assert (o.principal.name, o.detail) == ("student-a", "via its instance")


def test_moved_disk_goes_to_the_instance_launched_with_it_not_the_last_one():
    vol = _spare(from_snapshot=True)  # made at T0 with instance c1, then moved to c2
    later = replace(
        _launch_of("i-0example0000000c2", "student-b"), event_time=T0 + timedelta(hours=1)
    )
    events = [
        _detached(),
        _detached("i-0example0000000c2"),
        _launch_of("i-0example0000000c1"),
        later,
    ]
    assert owner_of([vol], events, vol.key).principal.name == "student-a"


def test_disk_detached_only_from_an_instance_launched_later_stays_unknown():
    vol = _spare(from_snapshot=True)
    later = replace(
        _launch_of("i-0example0000000c2", "student-b"), event_time=T0 + timedelta(hours=1)
    )
    o = owner_of([vol], [_detached("i-0example0000000c2"), later], vol.key)
    assert (o.principal, o.method) == (None, "unknown")


def test_old_disk_with_a_recent_detach_stays_unknown():
    vol = replace(_spare(from_snapshot=True), created_at=T0 - timedelta(days=200))
    o = owner_of([vol], [_detached(), _launch_of("i-0example0000000c1", "student-b")], vol.key)
    assert (o.principal, o.method) == (None, "unknown")


def test_a_launch_without_extra_disks_in_the_request_still_competes():
    # A launch template can hold the extra disk, so its request shows none.
    vol = _spare()
    events = [_launch(), _launch("student-b", blank=0, instance="i-0example0000000b2")]
    o = owner_of([vol], events, vol.key)
    assert (o.principal, o.method) == (None, "unknown")


def test_failed_lookup_region_names_nobody_from_partial_data():
    gone = _spare()
    detached = _spare(from_snapshot=True)
    detached = replace(detached, id="vol-0example0000000b2")
    events = [_launch(), _detached(vol=detached.id), _launch_of("i-0example0000000c1")]
    owners = attribute([gone, detached], events, frozenset({R}), lookback_days=90)
    assert [owners[v.key].method for v in (gone, detached)] == ["lookup-failed", "lookup-failed"]
