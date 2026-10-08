import pytest
from botocore.exceptions import ClientError

from dashboard.collectors import elb
from tests.helpers import ACCOUNT, T0, FakeAws

REGION = "eu-central-1"
ARN = f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}:loadbalancer/app/demo-alb/0123456789abcdef"
ARN2 = (
    f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}:loadbalancer/app/demo-alb2/0123456789abcdee"
)


def _stub_one_alb(aws):
    v2 = aws.stub("elbv2", REGION)
    v2.add_response(
        "describe_load_balancers",
        {
            "LoadBalancers": [
                {
                    "LoadBalancerArn": ARN,
                    "LoadBalancerName": "demo-alb",
                    "CreatedTime": T0,
                    "Type": "application",
                }
            ]
        },
    )
    # botocore's model requires Tags to have at least one entry when present, so omit it.
    v2.add_response(
        "describe_tags", {"TagDescriptions": [{"ResourceArn": ARN}]}, {"ResourceArns": [ARN]}
    )
    return v2


def test_collects_classic_and_v2():
    aws = FakeAws()
    classic = aws.stub("elb", REGION)
    classic.add_response(
        "describe_load_balancers",
        {
            "LoadBalancerDescriptions": [
                {"LoadBalancerName": "demo-classic", "CreatedTime": T0, "Instances": []}
            ]
        },
    )
    classic.add_response(
        "describe_tags",
        {
            "TagDescriptions": [
                {"LoadBalancerName": "demo-classic", "Tags": [{"Key": "team", "Value": "x"}]}
            ]
        },
        {"LoadBalancerNames": ["demo-classic"]},
    )
    _stub_one_alb(aws)

    found = elb.classic(aws, REGION) + elb.v2(aws, REGION)
    aws.assert_done()

    assert [(r.kind, r.id) for r in found] == [("elb-classic", "demo-classic"), ("elb-v2", ARN)]
    assert found[0].tags == {"team": "x"}
    assert found[1].name == "demo-alb"


def test_no_load_balancers_makes_no_tag_calls():
    aws = FakeAws()
    aws.stub("elb", REGION).add_response(
        "describe_load_balancers", {"LoadBalancerDescriptions": []}
    )
    aws.stub("elbv2", REGION).add_response("describe_load_balancers", {"LoadBalancers": []})
    assert elb.classic(aws, REGION) + elb.v2(aws, REGION) == []
    aws.assert_done()


def test_load_balancer_deleted_before_its_tags_are_read_is_dropped():
    aws = FakeAws()
    v2 = aws.stub("elbv2", REGION)
    lbs = [
        {"LoadBalancerArn": arn, "LoadBalancerName": name, "CreatedTime": T0}
        for arn, name in ((ARN, "demo-alb"), (ARN2, "demo-alb2"))
    ]
    v2.add_response("describe_load_balancers", {"LoadBalancers": lbs})
    v2.add_client_error(
        "describe_tags",
        service_error_code="LoadBalancerNotFound",
        http_status_code=400,
        expected_params={"ResourceArns": [ARN, ARN2]},
    )
    v2.add_response(
        "describe_tags", {"TagDescriptions": [{"ResourceArn": ARN}]}, {"ResourceArns": [ARN]}
    )
    v2.add_client_error(
        "describe_tags",
        service_error_code="LoadBalancerNotFound",
        http_status_code=400,
        expected_params={"ResourceArns": [ARN2]},
    )
    found = elb.v2(aws, REGION)
    aws.assert_done()
    assert [r.id for r in found] == [ARN]


def test_other_tag_errors_still_fail_the_collector():
    aws = FakeAws()
    v2 = aws.stub("elbv2", REGION)
    v2.add_response(
        "describe_load_balancers",
        {"LoadBalancers": [{"LoadBalancerArn": ARN, "LoadBalancerName": "demo-alb"}]},
    )
    v2.add_client_error("describe_tags", service_error_code="AccessDenied", http_status_code=403)
    with pytest.raises(ClientError):
        elb.v2(aws, REGION)
