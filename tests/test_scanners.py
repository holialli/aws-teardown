from unittest import mock

from botocore.exceptions import ClientError

from aws_teardown import pricing as p
from aws_teardown import scanners
from aws_teardown.report import render_text
from aws_teardown.scan import scan

from .conftest import REGION


def only(resources, kind):
    return [r for r in resources if r.kind == kind]


def test_empty_account_is_clean(session):
    result = scan(session, [REGION])
    assert result.resources == []
    assert result.total_monthly_cost == 0
    assert "You're clean" in render_text(result)


def test_running_instance_priced_with_public_ip(session, ec2, ami):
    inst = ec2.run_instances(ImageId=ami, InstanceType="t3.micro", MinCount=1, MaxCount=1)["Instances"][0]
    has_public_ip = bool(
        ec2.describe_instances(InstanceIds=[inst["InstanceId"]])["Reservations"][0]["Instances"][0].get(
            "PublicIpAddress"
        )
    )

    [found] = scanners.ec2_instances(session, REGION)

    expected = p.monthly(0.0104) + (p.monthly(p.PUBLIC_IPV4_HOURLY) if has_public_ip else 0)
    assert found.id == inst["InstanceId"]
    assert found.monthly_cost == round(expected, 2)
    assert "may be covered by free tier" in found.note
    assert found.delete_command == f"aws ec2 terminate-instances --instance-ids {inst['InstanceId']} --region {REGION}"


def test_stopped_instance_costs_nothing_but_says_why(session, ec2, ami):
    inst = ec2.run_instances(ImageId=ami, InstanceType="t3.small", MinCount=1, MaxCount=1)["Instances"][0]
    ec2.stop_instances(InstanceIds=[inst["InstanceId"]])

    [found] = scanners.ec2_instances(session, REGION)

    assert found.monthly_cost == 0
    assert "EBS volumes still bill" in found.note


def test_terminated_instance_ignored(session, ec2, ami):
    inst = ec2.run_instances(ImageId=ami, InstanceType="t3.micro", MinCount=1, MaxCount=1)["Instances"][0]
    ec2.terminate_instances(InstanceIds=[inst["InstanceId"]])

    assert scanners.ec2_instances(session, REGION) == []


def test_unknown_instance_type_has_unknown_price(session, ec2, ami):
    ec2.run_instances(ImageId=ami, InstanceType="r5.24xlarge", MinCount=1, MaxCount=1)

    [found] = scanners.ec2_instances(session, REGION)

    # Either None (no public IP) or just the IP charge; never a made-up compute price.
    assert found.monthly_cost in (None, p.monthly(p.PUBLIC_IPV4_HOURLY))
    assert "price not in table" in found.note


def test_instance_with_elastic_ip_not_double_counted(session, ec2, ami):
    inst = ec2.run_instances(ImageId=ami, InstanceType="t3.micro", MinCount=1, MaxCount=1)["Instances"][0]
    alloc = ec2.allocate_address(Domain="vpc")
    ec2.associate_address(InstanceId=inst["InstanceId"], AllocationId=alloc["AllocationId"])

    [instance] = scanners.ec2_instances(session, REGION)
    [eip] = scanners.elastic_ips(session, REGION)

    assert instance.monthly_cost == p.monthly(0.0104)
    assert eip.monthly_cost == p.monthly(p.PUBLIC_IPV4_HOURLY)
    assert eip.note == f"attached to {inst['InstanceId']}"
    assert "disassociate-address" in eip.delete_command


def test_unassociated_elastic_ip(session, ec2):
    alloc = ec2.allocate_address(Domain="vpc")

    [eip] = scanners.elastic_ips(session, REGION)

    assert eip.id == alloc["AllocationId"]
    assert eip.note == "NOT attached to anything"
    assert eip.delete_command == f"aws ec2 release-address --allocation-id {alloc['AllocationId']} --region {REGION}"


def test_nat_gateway_elastic_ip_is_not_disassociated(session, ec2, subnets):
    alloc = ec2.allocate_address(Domain="vpc")
    nat = ec2.create_nat_gateway(SubnetId=subnets[0], AllocationId=alloc["AllocationId"])["NatGateway"]

    [eip] = scanners.elastic_ips(session, REGION)

    assert eip.note == f"used by {nat['NatGatewayId']}; release it after deleting the NAT gateway"
    assert "disassociate" not in eip.delete_command


def test_unattached_volume(session, ec2):
    vol = ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=100, VolumeType="gp3")

    [found] = scanners.ebs_volumes(session, REGION)

    assert found.id == vol["VolumeId"]
    assert found.monthly_cost == 8.00
    assert "UNATTACHED" in found.note
    assert found.delete_command == f"aws ec2 delete-volume --volume-id {vol['VolumeId']} --region {REGION}"


def test_root_volume_has_no_delete_command(session, ec2, ami):
    inst = ec2.run_instances(ImageId=ami, InstanceType="t3.micro", MinCount=1, MaxCount=1)["Instances"][0]

    [root] = scanners.ebs_volumes(session, REGION)

    assert root.note.endswith(f"attached to {inst['InstanceId']} (deleted with it)")
    assert root.delete_command == ""


def test_attached_volume_that_outlives_instance(session, ec2, ami):
    inst = ec2.run_instances(ImageId=ami, InstanceType="t3.micro", MinCount=1, MaxCount=1)["Instances"][0]
    data = ec2.create_volume(AvailabilityZone=inst["Placement"]["AvailabilityZone"], Size=10, VolumeType="gp3")
    ec2.attach_volume(VolumeId=data["VolumeId"], InstanceId=inst["InstanceId"], Device="/dev/sdf")

    found = {v.id: v for v in scanners.ebs_volumes(session, REGION)}

    assert "survives its termination" in found[data["VolumeId"]].note
    assert found[data["VolumeId"]].delete_command


def test_snapshot_priced_by_source_size(session, ec2):
    vol = ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=8, VolumeType="gp2")
    snap = ec2.create_snapshot(VolumeId=vol["VolumeId"])

    found = scanners.ebs_snapshots(session, REGION)

    assert [s.id for s in found] == [snap["SnapshotId"]]
    assert found[0].monthly_cost == 0.40


def test_nat_gateway(session, ec2, subnets):
    alloc = ec2.allocate_address(Domain="vpc")
    nat = ec2.create_nat_gateway(SubnetId=subnets[0], AllocationId=alloc["AllocationId"])["NatGateway"]

    [found] = scanners.nat_gateways(session, REGION)

    assert found.id == nat["NatGatewayId"]
    assert found.monthly_cost == p.monthly(p.NAT_GATEWAY_HOURLY) == 32.85


def test_interface_endpoint_priced_per_az(session, ec2, subnets):
    vpc_id = ec2.describe_vpcs()["Vpcs"][0]["VpcId"]
    ec2.create_vpc_endpoint(
        VpcId=vpc_id,
        ServiceName=f"com.amazonaws.{REGION}.ssm",
        VpcEndpointType="Interface",
        SubnetIds=subnets[:2],
    )
    ec2.create_vpc_endpoint(VpcId=vpc_id, ServiceName=f"com.amazonaws.{REGION}.s3", VpcEndpointType="Gateway")

    [found] = scanners.vpc_interface_endpoints(session, REGION)

    assert found.monthly_cost == p.monthly(p.INTERFACE_ENDPOINT_HOURLY_PER_AZ * 2)


def test_load_balancers(session, subnets):
    elbv2 = session.client("elbv2", region_name=REGION)
    alb = elbv2.create_load_balancer(Name="tutorial-alb", Subnets=subnets[:2])["LoadBalancers"][0]
    elb = session.client("elb", region_name=REGION)
    elb.create_load_balancer(
        LoadBalancerName="old-clb",
        Listeners=[{"Protocol": "HTTP", "LoadBalancerPort": 80, "InstancePort": 80}],
        AvailabilityZones=[f"{REGION}a"],
    )

    found = {r.name: r for r in scanners.load_balancers(session, REGION)}

    assert found["tutorial-alb"].monthly_cost == p.monthly(p.ALB_HOURLY)
    assert found["tutorial-alb"].id == alb["LoadBalancerArn"]
    assert found["old-clb"].monthly_cost == p.monthly(p.CLASSIC_ELB_HOURLY)
    assert "elb delete-load-balancer --load-balancer-name old-clb" in found["old-clb"].delete_command


def test_rds_instance_includes_storage(session):
    rds = session.client("rds", region_name=REGION)
    rds.create_db_instance(
        DBInstanceIdentifier="tutorial-db",
        DBInstanceClass="db.t3.micro",
        Engine="postgres",
        AllocatedStorage=20,
        MasterUsername="admin",
        MasterUserPassword="password123",
    )

    [found] = scanners.rds_instances(session, REGION)

    assert found.monthly_cost == round(p.monthly(0.017) + 20 * p.RDS_STORAGE_GB_MONTH, 2)
    assert "--skip-final-snapshot" in found.delete_command


def test_eks_cluster(session, subnets):
    eks = session.client("eks", region_name=REGION)
    eks.create_cluster(
        name="tutorial-eks",
        roleArn="arn:aws:iam::123456789012:role/eks",
        resourcesVpcConfig={"subnetIds": subnets[:2]},
    )

    [found] = scanners.eks_clusters(session, REGION)

    assert found.monthly_cost == 73.0
    assert "delete-cluster --name tutorial-eks" in found.delete_command


def test_secrets_and_customer_kms_keys(session):
    session.client("secretsmanager", region_name=REGION).create_secret(Name="db-password", SecretString="x")
    kms = session.client("kms", region_name=REGION)
    key = kms.create_key(Description="tutorial key")["KeyMetadata"]["KeyId"]
    deleted = kms.create_key(Description="already going")["KeyMetadata"]["KeyId"]
    kms.schedule_key_deletion(KeyId=deleted, PendingWindowInDays=7)

    [secret] = scanners.secrets(session, REGION)
    keys = scanners.kms_keys(session, REGION)

    assert secret.name == "db-password" and secret.monthly_cost == 0.40
    assert [k.id for k in keys] == [key]


def test_scan_sorts_most_expensive_first(session, ec2, subnets):
    ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=10, VolumeType="gp3")
    alloc = ec2.allocate_address(Domain="vpc")
    ec2.create_nat_gateway(SubnetId=subnets[0], AllocationId=alloc["AllocationId"])

    result = scan(session, [REGION])

    assert [r.kind for r in result.resources] == ["nat-gateway", "elastic-ip", "ebs-volume"]
    assert result.total_monthly_cost == round(32.85 + 3.65 + 0.80, 2)


def test_delete_commands_in_dependency_order(session, ec2, subnets, ami):
    ec2.run_instances(ImageId=ami, InstanceType="t3.micro", MinCount=1, MaxCount=1)
    alloc = ec2.allocate_address(Domain="vpc")
    ec2.create_nat_gateway(SubnetId=subnets[0], AllocationId=alloc["AllocationId"])
    ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=10, VolumeType="gp3")

    text = render_text(scan(session, [REGION]))
    commands = text.split("in this order", 1)[1]

    order = [
        commands.index(cmd) for cmd in ("terminate-instances", "delete-nat-gateway", "release-address", "delete-volume")
    ]
    assert order == sorted(order)


def test_permission_errors_become_warnings(session, ec2):
    ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=10, VolumeType="gp3")
    denied = ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "ListClusters")

    def eks_denied(session, region):
        raise denied

    eks_denied.__name__ = "eks_clusters"
    patched = [eks_denied if fn is scanners.eks_clusters else fn for fn in scanners.ALL]
    with mock.patch.object(scanners, "ALL", patched):
        result = scan(session, [REGION])

    assert [r.kind for r in result.resources] == ["ebs-volume"]
    assert result.warnings == [f"eks_clusters in {REGION}: AccessDeniedException"]
    assert "Could not check" in render_text(result)
