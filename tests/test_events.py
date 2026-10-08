import pytest

from dashboard.attribution import Principal, event_names, parse_event, principal_of
from tests.helpers import ACCOUNT, T0, event, raw


@pytest.mark.parametrize(
    ("fixture", "kind", "resource_id"),
    [
        ("run_instances", "instance", "i-0example0000000a1"),
        ("create_volume", "volume", "vol-0example0000000a1"),
        ("allocate_address", "eip", "eipalloc-0example00000a1"),
        ("create_nat_gateway", "nat", "nat-0example0000000a1"),
        ("create_lb_classic", "elb-classic", "demo-classic"),
        (
            "create_lb_v2",
            "elb-v2",
            f"arn:aws:elasticloadbalancing:eu-central-1:{ACCOUNT}:loadbalancer/app/demo-alb/0123456789abcdef",
        ),
        ("create_cluster", "eks-cluster", f"arn:aws:eks:eu-central-1:{ACCOUNT}:cluster/demo-eks"),
        (
            "create_nodegroup",
            "eks-nodegroup",
            f"arn:aws:eks:eu-central-1:{ACCOUNT}:nodegroup/demo-eks/ng-1/00000000-1111-2222-3333-aaaabbbbcccc",
        ),
        ("create_asg", "asg", "demo-asg"),
        ("create_user", "iam-user", "student-a-test"),
    ],
)
def test_each_rule_extracts_its_id(fixture, kind, resource_id):
    (creation,) = parse_event(raw(event(fixture)), "eu-central-1")
    assert creation.resource_kind == kind
    assert creation.resource_id == resource_id
    assert creation.event_time == T0
    assert creation.principal == Principal("user", "student-a")
    assert creation.key == (kind, "eu-central-1", resource_id)


@pytest.mark.parametrize("fixture", ["run_instances_failed", "run_instances_dry_run"])
def test_events_with_error_code_are_skipped(fixture):
    assert parse_event(raw(event(fixture)), "eu-central-1") == []


def test_unrelated_event_is_ignored():
    detail = event("run_instances", eventSource="ecs.amazonaws.com", eventName="CreateCluster")
    assert parse_event(raw(detail), "eu-central-1") == []


def test_stats_count_matches_and_ids():
    stats: dict[str, list[int]] = {}
    parse_event(raw(event("run_instances")), "eu-central-1", stats)
    parse_event(
        raw(event("run_instances", responseElements={"unexpected": {}}, eventID="e2")),
        "eu-central-1",
        stats,
    )
    assert stats == {"RunInstances:instance": [2, 1]}


def test_event_names_per_region_kind():
    assert event_names() == [
        "RunInstances",
        "CreateVolume",
        "DetachVolume",
        "AllocateAddress",
        "CreateNatGateway",
        "CreateLoadBalancer",
        "CreateCluster",
        "CreateNodegroup",
        "CreateAutoScalingGroup",
    ]
    assert event_names(include_regional=False, include_global=True) == ["CreateUser"]


@pytest.mark.parametrize(
    ("identity", "expected"),
    [
        (
            {
                "type": "IAMUser",
                "userName": "student-a",
                "invokedBy": "cloudformation.amazonaws.com",
            },
            Principal("user", "student-a"),
        ),
        ({"type": "Root", "arn": f"arn:aws:iam::{ACCOUNT}:root"}, Principal("root", "root")),
        (
            {"type": "AWSService", "invokedBy": "eks.amazonaws.com"},
            Principal("service", "eks.amazonaws.com"),
        ),
        (
            {
                "type": "AssumedRole",
                "sessionContext": {
                    "sessionIssuer": {
                        "arn": f"arn:aws:iam::{ACCOUNT}:role/aws-service-role/autoscaling.amazonaws.com/AWSServiceRoleForAutoScaling",
                        "userName": "AWSServiceRoleForAutoScaling",
                    }
                },
            },
            Principal("service", "AWSServiceRoleForAutoScaling"),
        ),
        (
            {
                "type": "AssumedRole",
                "sessionContext": {
                    "sessionIssuer": {
                        "arn": f"arn:aws:iam::{ACCOUNT}:role/lab-role",
                        "userName": "lab-role",
                    },
                    "sourceIdentity": "student-b",
                },
            },
            Principal("user", "student-b", verified=False),
        ),
        (
            {
                "type": "AssumedRole",
                "sessionContext": {
                    "sessionIssuer": {
                        "arn": f"arn:aws:iam::{ACCOUNT}:role/lab-role",
                        "userName": "lab-role",
                    }
                },
            },
            Principal("role", "lab-role", verified=False),
        ),
        (
            {"type": "IdentityCenterUser", "arn": "arn:aws:identitystore:::user/x"},
            Principal("other", "arn:aws:identitystore:::user/x", verified=False),
        ),
        ({}, Principal("other", "unknown", verified=False)),
        (
            {"type": "IAMUser", "arn": f"arn:aws:iam::{ACCOUNT}:user/x"},
            Principal("other", "x", verified=False),  # the name, not the ARN with the account ID
        ),
        ({"type": "IAMUser"}, Principal("other", "IAMUser", verified=False)),
    ],
)
def test_principal_of(identity, expected):
    assert principal_of(identity) == expected


def test_role_label_marks_role():
    assert Principal("role", "lab-role", verified=False).label == "role:lab-role"
    assert Principal("user", "student-a").label == "student-a"


def test_run_instances_keeps_the_zone_and_the_blank_disks_it_asked_for():
    detail = event(
        "run_instances",
        requestParameters={
            "instanceType": "t3.micro",
            "blockDeviceMapping": {
                "items": [
                    {
                        "deviceName": "/dev/sdb",
                        "ebs": {"volumeSize": 10, "deleteOnTermination": False},
                    },
                    {"deviceName": "/dev/sdc", "ebs": {"snapshotId": "snap-0example0000000a1"}},
                    {"deviceName": "/dev/sdd", "noDevice": {}},
                ]
            },
        },
        responseElements={
            "instancesSet": {
                "items": [
                    {
                        "instanceId": "i-0example0000000a1",
                        "placement": {"availabilityZone": "eu-central-1a"},
                    }
                ]
            }
        },
    )
    [c] = parse_event(raw(detail), "eu-central-1")
    assert (c.availability_zone, c.blank_volumes) == ("eu-central-1a", 1)


def test_other_events_have_no_launch_details():
    [c] = parse_event(raw(event("run_instances")), "eu-central-1")
    assert (c.availability_zone, c.blank_volumes) == ("", 0)
    [v] = parse_event(raw(event("create_volume")), "eu-central-1")
    assert (v.availability_zone, v.blank_volumes) == ("", 0)


def _detach(**overrides):
    detail = {
        "eventSource": "ec2.amazonaws.com",
        "eventName": "DetachVolume",
        "eventID": "e-detach-volume",
        "requestParameters": {"volumeId": "vol-0example0000000c1", "force": False},
        "responseElements": {
            "volumeId": "vol-0example0000000c1",
            "instanceId": "i-0example0000000c1",
            "device": "/dev/sdb",
            "status": "detaching",
        },
    }
    return event("run_instances", **(detail | overrides))


def test_detach_volume_links_the_disk_to_its_instance():
    [d] = parse_event(raw(_detach()), "eu-central-1")
    assert (d.resource_kind, d.resource_id, d.instance_id) == (
        "volume-detach",
        "vol-0example0000000c1",
        "i-0example0000000c1",
    )


def test_detach_volume_takes_the_instance_from_the_request_too():
    detail = _detach(
        requestParameters={
            "volumeId": "vol-0example0000000c1",
            "instanceId": "i-0example0000000c2",
        },
        responseElements={"volumeId": "vol-0example0000000c1"},
    )
    [d] = parse_event(raw(detail), "eu-central-1")
    assert d.instance_id == "i-0example0000000c2"


def test_creation_events_link_no_instance():
    [c] = parse_event(raw(event("create_volume")), "eu-central-1")
    assert c.instance_id == ""


def _launch_event(mappings):
    return event(
        "run_instances",
        requestParameters={"blockDeviceMapping": {"items": mappings}},
        responseElements={
            "instancesSet": {
                "items": [
                    {
                        "instanceId": "i-0example0000000a1",
                        "rootDeviceName": "/dev/xvda",
                        "placement": {"availabilityZone": "eu-central-1a"},
                    }
                ]
            }
        },
    )


def test_a_bigger_root_disk_is_not_an_extra_blank_disk():
    root = {"deviceName": "/dev/xvda", "ebs": {"volumeSize": 20}}  # Terraform root_block_device
    extra = {"deviceName": "/dev/sdb", "ebs": {"volumeSize": 10}}
    [only_root] = parse_event(raw(_launch_event([root])), "eu-central-1")
    [both] = parse_event(raw(_launch_event([root, extra])), "eu-central-1")
    assert (only_root.blank_volumes, both.blank_volumes) == (0, 1)
