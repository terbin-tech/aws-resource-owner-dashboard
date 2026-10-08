from botocore.exceptions import ClientError

import dashboard.collectors as collectors
from dashboard.collectors.base import Incomplete, Resource
from tests.helpers import T0, FakeAws


def test_one_failing_collector_only_marks_that_service_incomplete(monkeypatch):
    def ok(aws, region):
        return [Resource(kind="volume", id=f"vol-{region}", region=region)]

    def broken(aws, region):
        if region == "eu-central-1":
            raise ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, "DescribeX")
        return []

    monkeypatch.setattr(collectors, "COLLECTORS", (("ec2", ok), ("eks", broken)))
    resources, incomplete = collectors.run_collectors(None, ("us-east-1", "eu-central-1"))
    assert [r.id for r in resources] == ["vol-us-east-1", "vol-eu-central-1"]
    assert incomplete == [Incomplete("eks", "eu-central-1", "AccessDenied")]


def test_each_collector_covers_one_api_family():
    assert [name for name, _ in collectors.COLLECTORS] == [
        "ec2",
        "ebs",
        "network",
        "elb",
        "elbv2",
        "eks",
        "autoscaling",
    ]


def test_failing_volumes_do_not_hide_instances(monkeypatch):
    region = "eu-central-1"
    aws = FakeAws()
    s = aws.stub("ec2", region)
    s.add_response(
        "describe_instances",
        {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": "i-0example0000000a1",
                            "InstanceType": "t3.micro",
                            "State": {"Name": "running", "Code": 16},
                            "LaunchTime": T0,
                        }
                    ]
                }
            ]
        },
    )
    s.add_client_error(
        "describe_volumes", service_error_code="UnauthorizedOperation", http_status_code=403
    )
    only_ec2 = tuple(c for c in collectors.COLLECTORS if c[0] in ("ec2", "ebs"))
    monkeypatch.setattr(collectors, "COLLECTORS", only_ec2)
    resources, incomplete = collectors.run_collectors(aws, (region,))
    assert [r.id for r in resources] == ["i-0example0000000a1"]
    assert incomplete == [Incomplete("ebs", region, "UnauthorizedOperation")]
