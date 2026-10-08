from dashboard.collectors import network
from tests.helpers import T0, FakeAws

REGION = "eu-central-1"


def _stub_all(aws):
    s = aws.stub("ec2", REGION)
    s.add_response(
        "describe_nat_gateways",
        {
            "NatGateways": [
                {
                    "NatGatewayId": "nat-0example0000000a1",
                    "State": "available",
                    "CreateTime": T0,
                    "NatGatewayAddresses": [{"AllocationId": "eipalloc-0example00000a1"}],
                },
                {"NatGatewayId": "nat-0example0000000a2", "State": "deleted", "CreateTime": T0},
            ]
        },
    )
    s.add_response(
        "describe_addresses",
        {
            "Addresses": [
                {"AllocationId": "eipalloc-0example00000a1", "AssociationId": "eipassoc-1"},
                {"AllocationId": "eipalloc-0example00000a2"},
            ]
        },
    )
    s.add_response(
        "describe_network_interfaces",
        {
            "NetworkInterfaces": [
                {  # auto-assigned public IP on an instance
                    "NetworkInterfaceId": "eni-0example0000000a1",
                    "InterfaceType": "interface",
                    "Association": {"PublicIp": "192.0.2.1", "IpOwnerId": "amazon"},
                    "Attachment": {"InstanceId": "i-0example0000000a1"},
                },
                {  # ALB-managed ENI
                    "NetworkInterfaceId": "eni-0example0000000a2",
                    "InterfaceType": "interface",
                    "Description": "ELB app/demo-alb/0123456789abcdef",
                    "Association": {"PublicIp": "192.0.2.2", "IpOwnerId": "amazon"},
                },
                {  # EIP-backed: already counted as an eip resource
                    "NetworkInterfaceId": "eni-0example0000000a3",
                    "Association": {
                        "PublicIp": "192.0.2.3",
                        "AllocationId": "eipalloc-0example00000a1",
                    },
                },
                {"NetworkInterfaceId": "eni-0example0000000a4"},  # private only
            ]
        },
    )
    return s


def test_collects_nat_eips_and_public_ips():
    aws = FakeAws()
    _stub_all(aws)
    found = network.collect(aws, REGION)
    aws.assert_done()

    by_id = {r.id: r for r in found}
    assert [r.kind for r in found] == ["nat", "eip", "eip", "public-ip", "public-ip"]
    assert by_id["nat-0example0000000a1"].attrs == {
        "state": "available",
        "eip_allocation_ids": ["eipalloc-0example00000a1"],
    }
    assert by_id["eipalloc-0example00000a1"].attrs == {"nat_gateway_id": "nat-0example0000000a1"}
    assert by_id["eipalloc-0example00000a2"].attrs == {"nat_gateway_id": None}
    assert by_id["eni-0example0000000a1"].attrs["parent_kind"] == "instance"
    assert by_id["eni-0example0000000a1"].attrs["parent_id"] == "i-0example0000000a1"
    assert by_id["eni-0example0000000a2"].attrs["parent_kind"] == "elb-v2"
    assert by_id["eni-0example0000000a2"].attrs["parent_id"] == "app/demo-alb/0123456789abcdef"


def test_an_interface_with_several_public_ips_is_one_resource():
    aws = FakeAws()
    s = aws.stub("ec2", REGION)
    s.add_response("describe_nat_gateways", {"NatGateways": []})
    s.add_response("describe_addresses", {"Addresses": []})
    s.add_response(
        "describe_network_interfaces",
        {
            "NetworkInterfaces": [
                {
                    "NetworkInterfaceId": "eni-0example0000000a5",
                    "Association": {"PublicIp": "192.0.2.5", "IpOwnerId": "amazon"},
                    "Attachment": {"InstanceId": "i-0example0000000a1"},
                    "PrivateIpAddresses": [
                        {
                            "PrivateIpAddress": "10.0.0.5",
                            "Association": {"PublicIp": "192.0.2.5", "IpOwnerId": "amazon"},
                        },
                        {
                            "PrivateIpAddress": "10.0.0.6",
                            "Association": {"PublicIp": "192.0.2.6", "IpOwnerId": "amazon"},
                        },
                        {"PrivateIpAddress": "10.0.0.7"},
                    ],
                }
            ]
        },
    )
    (ip,) = network.collect(aws, REGION)
    aws.assert_done()
    assert (ip.kind, ip.id) == ("public-ip", "eni-0example0000000a5")
