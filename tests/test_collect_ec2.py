from datetime import timedelta

from dashboard.collectors import ec2
from tests.helpers import T0, FakeAws

REGION = "eu-central-1"


def test_collects_instances_and_volumes():
    aws = FakeAws()
    s = aws.stub("ec2", REGION)
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
                            "LaunchTime": T0 + timedelta(days=3),
                            "Tags": [{"Key": "Name", "Value": "web"}],
                            "NetworkInterfaces": [
                                {"Attachment": {"DeviceIndex": 0, "AttachTime": T0}}
                            ],
                        },
                        {
                            "InstanceId": "i-0example0000000a2",
                            "InstanceType": "t3.micro",
                            "State": {"Name": "terminated", "Code": 48},
                        },
                    ]
                }
            ]
        },
    )
    s.add_response(
        "describe_volumes",
        {
            "Volumes": [
                {
                    "VolumeId": "vol-0example0000000a1",
                    "Size": 8,
                    "VolumeType": "gp3",
                    "CreateTime": T0,
                    "AvailabilityZone": "eu-central-1a",
                    "SnapshotId": "snap-0example0000000a1",
                    "Attachments": [{"InstanceId": "i-0example0000000a1", "State": "attached"}],
                }
            ]
        },
    )

    found = ec2.instances(aws, REGION) + ec2.volumes(aws, REGION)

    aws.assert_done()
    assert [r.key for r in found] == [
        ("instance", REGION, "i-0example0000000a1"),
        ("volume", REGION, "vol-0example0000000a1"),
    ]
    instance, volume = found
    # Age comes from the primary ENI attach time, because LaunchTime resets on every start.
    assert instance.created_at == T0
    assert instance.name == "web"
    assert instance.attrs == {"state": "running", "public_ip": False}
    assert volume.attrs == {
        "attached_instance": "i-0example0000000a1",
        "availability_zone": "eu-central-1a",
        "from_snapshot": True,  # a root disk, from the AMI's snapshot
    }


def test_instance_without_eni_falls_back_to_launch_time():
    aws = FakeAws()
    s = aws.stub("ec2", REGION)
    s.add_response(
        "describe_instances",
        {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": "i-0example0000000a3",
                            "InstanceType": "t3.small",
                            "State": {"Name": "stopped", "Code": 80},
                            "LaunchTime": T0,
                        }
                    ]
                }
            ]
        },
    )
    (instance,) = ec2.instances(aws, REGION)
    assert instance.created_at == T0
    assert instance.attrs["state"] == "stopped"


def test_instance_public_ip_flag():
    aws = FakeAws()
    s = aws.stub("ec2", REGION)
    s.add_response(
        "describe_instances",
        {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": "i-0example0000000a4",
                            "InstanceType": "t3.micro",
                            "State": {"Name": "running", "Code": 16},
                            "LaunchTime": T0,
                            "PublicIpAddress": "192.0.2.10",
                        }
                    ]
                }
            ]
        },
    )
    (instance,) = ec2.instances(aws, REGION)
    aws.assert_done()
    assert instance.attrs["public_ip"] is True
