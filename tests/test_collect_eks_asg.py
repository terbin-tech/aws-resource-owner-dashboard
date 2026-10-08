from datetime import UTC, datetime

import pytest
from botocore.exceptions import ClientError

from dashboard.collectors import autoscaling, eks
from tests.helpers import ACCOUNT, T0, FakeAws

REGION = "eu-central-1"
CLUSTER_ARN = f"arn:aws:eks:{REGION}:{ACCOUNT}:cluster/demo-eks"
NG_ARN = (
    f"arn:aws:eks:{REGION}:{ACCOUNT}:nodegroup/demo-eks/ng-1/00000000-1111-2222-3333-aaaabbbbcccc"
)


def _stub_cluster(aws, version_entries):
    s = aws.stub("eks", REGION)
    s.add_response("list_clusters", {"clusters": ["demo-eks"]})
    s.add_response(
        "describe_cluster",
        {
            "cluster": {
                "name": "demo-eks",
                "arn": CLUSTER_ARN,
                "version": "1.32",
                "createdAt": T0,
                "status": "ACTIVE",
                "tags": {"team": "x"},
                "upgradePolicy": {"supportType": "EXTENDED"},
            }
        },
        {"name": "demo-eks"},
    )
    s.add_response(
        "describe_cluster_versions",
        {"clusterVersions": version_entries},
        {"clusterVersions": ["1.32"]},
    )
    s.add_response("list_nodegroups", {"nodegroups": ["ng-1"]}, {"clusterName": "demo-eks"})
    s.add_response(
        "describe_nodegroup",
        {
            "nodegroup": {
                "nodegroupName": "ng-1",
                "nodegroupArn": NG_ARN,
                "clusterName": "demo-eks",
                "createdAt": T0,
            }
        },
        {"clusterName": "demo-eks", "nodegroupName": "ng-1"},
    )


def test_cluster_gets_its_version_status_and_nodegroups():
    aws = FakeAws()
    _stub_cluster(
        aws,
        [
            {
                "clusterVersion": "1.32",
                "versionStatus": "EXTENDED_SUPPORT",
                "endOfStandardSupportDate": datetime(2026, 3, 23, tzinfo=UTC),
            }
        ],
    )
    cluster, nodegroup = eks.collect(aws, REGION)
    aws.assert_done()
    assert cluster.key == ("eks-cluster", REGION, CLUSTER_ARN)
    assert cluster.name == "demo-eks"
    assert cluster.attrs == {"version": "1.32", "version_status": "EXTENDED_SUPPORT"}
    assert nodegroup.key == ("eks-nodegroup", REGION, NG_ARN)
    assert nodegroup.attrs == {"cluster_name": "demo-eks"}


def test_version_missing_from_describe_cluster_versions_stays_unknown():
    aws = FakeAws()
    _stub_cluster(aws, [])
    cluster, _ = eks.collect(aws, REGION)
    assert cluster.attrs["version_status"] is None


def test_no_clusters_makes_no_version_call():
    aws = FakeAws()
    aws.stub("eks", REGION).add_response("list_clusters", {"clusters": []})
    assert eks.collect(aws, REGION) == []
    aws.assert_done()


def test_collects_auto_scaling_groups():
    aws = FakeAws()
    aws.stub("autoscaling", REGION).add_response(
        "describe_auto_scaling_groups",
        {
            "AutoScalingGroups": [
                {
                    "AutoScalingGroupName": "demo-asg",
                    "MinSize": 0,
                    "MaxSize": 2,
                    "DesiredCapacity": 1,
                    "DefaultCooldown": 300,
                    "AvailabilityZones": ["eu-central-1a"],
                    "HealthCheckType": "EC2",
                    "CreatedTime": T0,
                    "Tags": [{"Key": "Name", "Value": "web"}],
                }
            ]
        },
    )
    (group,) = autoscaling.collect(aws, REGION)
    assert group.key == ("asg", REGION, "demo-asg")
    assert group.created_at == T0
    assert group.tags == {"Name": "web"}


def _cluster(name="demo-eks"):
    return {
        "name": name,
        "arn": f"arn:aws:eks:{REGION}:{ACCOUNT}:cluster/{name}",
        "version": "1.32",
        "createdAt": T0,
        "status": "ACTIVE",
    }


@pytest.mark.parametrize(
    ("code", "status"),
    [("InvalidParameterException", 400), ("AccessDeniedException", 403)],
    ids=["invalid-parameter", "access-denied"],
)
def test_rejected_version_call_keeps_the_cluster_with_unknown_support(code, status):
    aws = FakeAws()
    s = aws.stub("eks", REGION)
    s.add_response("list_clusters", {"clusters": ["demo-eks"]})
    s.add_response("describe_cluster", {"cluster": _cluster()}, {"name": "demo-eks"})
    s.add_client_error(
        "describe_cluster_versions", service_error_code=code, http_status_code=status
    )
    s.add_response("list_nodegroups", {"nodegroups": []}, {"clusterName": "demo-eks"})
    (cluster,) = eks.collect(aws, REGION)
    aws.assert_done()
    assert (cluster.kind, cluster.name) == ("eks-cluster", "demo-eks")
    assert cluster.attrs["version_status"] is None


def test_cluster_or_nodegroup_deleted_during_the_scan_is_skipped():
    aws = FakeAws()
    s = aws.stub("eks", REGION)
    s.add_response("list_clusters", {"clusters": ["gone-eks", "demo-eks"]})
    s.add_client_error(
        "describe_cluster",
        service_error_code="ResourceNotFoundException",
        http_status_code=404,
        expected_params={"name": "gone-eks"},
    )
    s.add_response("describe_cluster", {"cluster": _cluster()}, {"name": "demo-eks"})
    s.add_response(
        "describe_cluster_versions",
        {"clusterVersions": []},
        {"clusterVersions": ["1.32"]},
    )
    s.add_response("list_nodegroups", {"nodegroups": ["ng-gone"]}, {"clusterName": "demo-eks"})
    s.add_client_error(
        "describe_nodegroup",
        service_error_code="ResourceNotFoundException",
        http_status_code=404,
        expected_params={"clusterName": "demo-eks", "nodegroupName": "ng-gone"},
    )
    found = eks.collect(aws, REGION)
    aws.assert_done()
    assert [(r.kind, r.name) for r in found] == [("eks-cluster", "demo-eks")]


def test_other_eks_errors_still_fail_the_collector():
    aws = FakeAws()
    s = aws.stub("eks", REGION)
    s.add_response("list_clusters", {"clusters": ["demo-eks"]})
    s.add_client_error(
        "describe_cluster", service_error_code="AccessDeniedException", http_status_code=403
    )
    with pytest.raises(ClientError):
        eks.collect(aws, REGION)
