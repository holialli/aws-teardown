"""One function per resource type. Each takes a boto3 session and a region
and returns a list of Resources. They only ever call Describe/List APIs."""

from . import pricing as p
from .models import Resource


def _tags(raw) -> dict:
    return {t["Key"]: t["Value"] for t in raw or []}


def _paginate(client, op, key, **kwargs):
    for page in client.get_paginator(op).paginate(**kwargs):
        yield from page.get(key, [])


def ec2_instances(session, region):
    ec2 = session.client("ec2", region_name=region)
    elastic_ips = {a["PublicIp"] for a in ec2.describe_addresses().get("Addresses", [])}
    found = []
    for reservation in _paginate(ec2, "describe_instances", "Reservations"):
        for inst in reservation["Instances"]:
            state = inst["State"]["Name"]
            if state in ("terminated", "shutting-down"):
                continue
            itype = inst["InstanceType"]
            tags = _tags(inst.get("Tags"))
            cost, notes = None, []
            if state == "stopped":
                cost = 0.0
                notes.append("stopped: no compute charge, its EBS volumes still bill")
            else:
                hourly = p.EC2_HOURLY.get(itype)
                if hourly is None:
                    notes.append(f"{itype}: price not in table")
                else:
                    cost = p.monthly(hourly)
                public_ip = inst.get("PublicIpAddress")
                # Elastic IPs are counted separately so they aren't billed twice here.
                if public_ip and public_ip not in elastic_ips:
                    cost = (cost or 0) + p.monthly(p.PUBLIC_IPV4_HOURLY)
                    notes.append("includes public IPv4")
                if itype in p.FREE_TIER_EC2:
                    notes.append("may be covered by free tier")
            found.append(
                Resource(
                    kind="ec2-instance",
                    region=region,
                    id=inst["InstanceId"],
                    name=tags.get("Name", ""),
                    monthly_cost=cost,
                    note=f"{itype}, {state}" + (f"; {'; '.join(notes)}" if notes else ""),
                    delete_command=f"aws ec2 terminate-instances --instance-ids {inst['InstanceId']} --region {region}",
                    tags=tags,
                )
            )
    return found


def ebs_volumes(session, region):
    ec2 = session.client("ec2", region_name=region)
    # The instance's block device mapping is the authoritative DeleteOnTermination flag.
    delete_on_termination = {
        mapping["Ebs"]["VolumeId"]: mapping["Ebs"].get("DeleteOnTermination", False)
        for reservation in _paginate(ec2, "describe_instances", "Reservations")
        for inst in reservation["Instances"]
        for mapping in inst.get("BlockDeviceMappings", [])
        if "Ebs" in mapping
    }
    found = []
    for vol in _paginate(ec2, "describe_volumes", "Volumes"):
        tags = _tags(vol.get("Tags"))
        rate = p.EBS_GB_MONTH.get(vol["VolumeType"])
        size = f"{vol['Size']} GiB {vol['VolumeType']}"
        delete = f"aws ec2 delete-volume --volume-id {vol['VolumeId']} --region {region}"
        attachment = (vol.get("Attachments") or [None])[0]
        if attachment is None:
            note = f"{size}, UNATTACHED"
        elif delete_on_termination.get(vol["VolumeId"], attachment.get("DeleteOnTermination")):
            # Goes away with the instance; a delete command here would just fail while attached.
            note = f"{size}, attached to {attachment['InstanceId']} (deleted with it)"
            delete = ""
        else:
            note = f"{size}, attached to {attachment['InstanceId']}, survives its termination"
        found.append(
            Resource(
                kind="ebs-volume",
                region=region,
                id=vol["VolumeId"],
                name=tags.get("Name", ""),
                monthly_cost=round(vol["Size"] * rate, 2) if rate else None,
                note=note,
                delete_command=delete,
                tags=tags,
            )
        )
    return found


def ebs_snapshots(session, region):
    ec2 = session.client("ec2", region_name=region)
    found = []
    for snap in _paginate(ec2, "describe_snapshots", "Snapshots", OwnerIds=["self"]):
        tags = _tags(snap.get("Tags"))
        found.append(
            Resource(
                kind="ebs-snapshot",
                region=region,
                id=snap["SnapshotId"],
                name=tags.get("Name", ""),
                monthly_cost=round(snap["VolumeSize"] * p.EBS_SNAPSHOT_GB_MONTH, 2),
                note=f"{snap['VolumeSize']} GiB source volume (upper bound, snapshots are incremental)",
                delete_command=f"aws ec2 delete-snapshot --snapshot-id {snap['SnapshotId']} --region {region}",
                tags=tags,
            )
        )
    return found


def elastic_ips(session, region):
    ec2 = session.client("ec2", region_name=region)
    nat_by_allocation = {
        address["AllocationId"]: nat["NatGatewayId"]
        for nat in _paginate(ec2, "describe_nat_gateways", "NatGateways")
        if nat["State"] in ("pending", "available")
        for address in nat.get("NatGatewayAddresses", [])
        if address.get("AllocationId")
    }
    found = []
    for addr in ec2.describe_addresses().get("Addresses", []):
        tags = _tags(addr.get("Tags"))
        allocation_id = addr.get("AllocationId", addr["PublicIp"])
        release = f"aws ec2 release-address --allocation-id {allocation_id} --region {region}"
        nat_id = nat_by_allocation.get(allocation_id)
        if nat_id:
            # A NAT gateway's address can't be disassociated; it frees up when the NAT is deleted.
            note = f"used by {nat_id}; release it after deleting the NAT gateway"
        elif addr.get("InstanceId"):
            note = f"attached to {addr['InstanceId']}"
            release = (
                f"aws ec2 disassociate-address --association-id {addr['AssociationId']} --region {region} && {release}"
            )
        elif addr.get("AssociationId"):
            note = f"attached to {addr.get('NetworkInterfaceId', 'a network interface')}"
        else:
            note = "NOT attached to anything"
        found.append(
            Resource(
                kind="elastic-ip",
                region=region,
                id=allocation_id,
                name=tags.get("Name", addr["PublicIp"]),
                monthly_cost=p.monthly(p.PUBLIC_IPV4_HOURLY),
                note=note,
                delete_command=release,
                tags=tags,
            )
        )
    return found


def nat_gateways(session, region):
    ec2 = session.client("ec2", region_name=region)
    found = []
    for nat in _paginate(ec2, "describe_nat_gateways", "NatGateways"):
        if nat["State"] not in ("pending", "available"):
            continue
        tags = _tags(nat.get("Tags"))
        found.append(
            Resource(
                kind="nat-gateway",
                region=region,
                id=nat["NatGatewayId"],
                name=tags.get("Name", ""),
                monthly_cost=p.monthly(p.NAT_GATEWAY_HOURLY),
                note="plus $0.045/GB processed; its Elastic IP is listed separately",
                delete_command=f"aws ec2 delete-nat-gateway --nat-gateway-id {nat['NatGatewayId']} --region {region}",
                tags=tags,
            )
        )
    return found


def vpc_interface_endpoints(session, region):
    ec2 = session.client("ec2", region_name=region)
    found = []
    for ep in _paginate(ec2, "describe_vpc_endpoints", "VpcEndpoints"):
        if ep.get("VpcEndpointType") != "Interface" or ep.get("State", "").lower() in (
            "deleted",
            "deleting",
        ):
            continue
        azs = max(len(ep.get("SubnetIds", [])), 1)
        tags = _tags(ep.get("Tags"))
        found.append(
            Resource(
                kind="vpc-endpoint",
                region=region,
                id=ep["VpcEndpointId"],
                name=tags.get("Name", ep.get("ServiceName", "")),
                monthly_cost=p.monthly(p.INTERFACE_ENDPOINT_HOURLY_PER_AZ * azs),
                note=f"interface endpoint in {azs} AZ(s) for {ep.get('ServiceName', '?')}",
                delete_command=f"aws ec2 delete-vpc-endpoints --vpc-endpoint-ids {ep['VpcEndpointId']} --region {region}",
                tags=tags,
            )
        )
    return found


def load_balancers(session, region):
    elbv2 = session.client("elbv2", region_name=region)
    hourly = {
        "application": p.ALB_HOURLY,
        "network": p.NLB_HOURLY,
        "gateway": p.GWLB_HOURLY,
    }
    found = []
    for lb in _paginate(elbv2, "describe_load_balancers", "LoadBalancers"):
        lb_type = lb.get("Type", "application")
        found.append(
            Resource(
                kind="load-balancer",
                region=region,
                id=lb["LoadBalancerArn"],
                name=lb["LoadBalancerName"],
                monthly_cost=p.monthly(hourly.get(lb_type, p.ALB_HOURLY)),
                note=f"{lb_type} load balancer, plus capacity units",
                delete_command=f"aws elbv2 delete-load-balancer --load-balancer-arn {lb['LoadBalancerArn']} --region {region}",
            )
        )
    elb = session.client("elb", region_name=region)
    for lb in _paginate(elb, "describe_load_balancers", "LoadBalancerDescriptions"):
        found.append(
            Resource(
                kind="load-balancer",
                region=region,
                id=lb["LoadBalancerName"],
                name=lb["LoadBalancerName"],
                monthly_cost=p.monthly(p.CLASSIC_ELB_HOURLY),
                note="classic load balancer",
                delete_command=f"aws elb delete-load-balancer --load-balancer-name {lb['LoadBalancerName']} --region {region}",
            )
        )
    return found


def rds_instances(session, region):
    rds = session.client("rds", region_name=region)
    found = []
    for db in _paginate(rds, "describe_db_instances", "DBInstances"):
        status = db["DBInstanceStatus"]
        if status == "deleting":
            continue
        dbclass = db["DBInstanceClass"]
        storage = round(db.get("AllocatedStorage", 0) * p.RDS_STORAGE_GB_MONTH, 2)
        notes = [f"{dbclass} {db['Engine']}, {status}"]
        if status == "stopped":
            cost = storage
            notes.append("stopped instances restart on their own after 7 days")
        else:
            hourly = p.RDS_HOURLY.get(dbclass)
            if hourly is None:
                cost = None
                notes.append("price not in table")
            else:
                cost = round(p.monthly(hourly) * (2 if db.get("MultiAZ") else 1) + storage, 2)
            if db.get("MultiAZ"):
                notes.append("Multi-AZ doubles the instance price")
            if dbclass in p.FREE_TIER_RDS and not db.get("MultiAZ"):
                notes.append("may be covered by free tier")
        found.append(
            Resource(
                kind="rds-instance",
                region=region,
                id=db["DBInstanceIdentifier"],
                name=db["DBInstanceIdentifier"],
                monthly_cost=cost,
                note="; ".join(notes),
                delete_command=(
                    f"aws rds delete-db-instance --db-instance-identifier {db['DBInstanceIdentifier']} "
                    f"--skip-final-snapshot --region {region}"
                ),
            )
        )
    return found


def eks_clusters(session, region):
    eks = session.client("eks", region_name=region)
    found = []
    for name in _paginate(eks, "list_clusters", "clusters"):
        found.append(
            Resource(
                kind="eks-cluster",
                region=region,
                id=name,
                name=name,
                monthly_cost=p.monthly(p.EKS_CLUSTER_HOURLY),
                note="control plane only; worker nodes show up as EC2 instances",
                delete_command=(
                    f"aws eks list-nodegroups --cluster-name {name} --region {region}  "
                    f"# delete each nodegroup first, then: aws eks delete-cluster --name {name} --region {region}"
                ),
            )
        )
    return found


def secrets(session, region):
    sm = session.client("secretsmanager", region_name=region)
    found = []
    for secret in _paginate(sm, "list_secrets", "SecretList"):
        if secret.get("DeletedDate"):
            continue
        found.append(
            Resource(
                kind="secret",
                region=region,
                id=secret["ARN"],
                name=secret["Name"],
                monthly_cost=p.SECRET_MONTHLY,
                note="Secrets Manager secret",
                delete_command=f"aws secretsmanager delete-secret --secret-id {secret['Name']} --region {region}",
                tags=_tags(secret.get("Tags")),
            )
        )
    return found


def kms_keys(session, region):
    kms = session.client("kms", region_name=region)
    found = []
    for key in _paginate(kms, "list_keys", "Keys"):
        meta = kms.describe_key(KeyId=key["KeyId"])["KeyMetadata"]
        if meta.get("KeyManager") != "CUSTOMER" or meta.get("KeyState") in (
            "PendingDeletion",
            "PendingReplicaDeletion",
        ):
            continue
        found.append(
            Resource(
                kind="kms-key",
                region=region,
                id=meta["KeyId"],
                name=meta.get("Description", ""),
                monthly_cost=p.KMS_KEY_MONTHLY,
                note=f"customer-managed key, {meta.get('KeyState', '?').lower()}",
                delete_command=f"aws kms schedule-key-deletion --key-id {meta['KeyId']} --pending-window-in-days 7 --region {region}",
            )
        )
    return found


def sagemaker(session, region):
    sm = session.client("sagemaker", region_name=region)
    found = []
    for nb in _paginate(sm, "list_notebook_instances", "NotebookInstances"):
        if nb["NotebookInstanceStatus"] not in ("InService", "Pending"):
            continue
        hourly = p.SAGEMAKER_NOTEBOOK_HOURLY.get(nb["InstanceType"])
        found.append(
            Resource(
                kind="sagemaker-notebook",
                region=region,
                id=nb["NotebookInstanceName"],
                name=nb["NotebookInstanceName"],
                monthly_cost=p.monthly(hourly) if hourly else None,
                note=f"{nb['InstanceType']}, {nb['NotebookInstanceStatus']}",
                delete_command=(
                    f"aws sagemaker stop-notebook-instance --notebook-instance-name {nb['NotebookInstanceName']} --region {region}"
                ),
            )
        )
    for ep in _paginate(sm, "list_endpoints", "Endpoints"):
        found.append(
            Resource(
                kind="sagemaker-endpoint",
                region=region,
                id=ep["EndpointName"],
                name=ep["EndpointName"],
                monthly_cost=None,
                note=f"{ep['EndpointStatus']}; billed per instance-hour while it exists",
                delete_command=f"aws sagemaker delete-endpoint --endpoint-name {ep['EndpointName']} --region {region}",
            )
        )
    return found


ALL = [
    ec2_instances,
    ebs_volumes,
    ebs_snapshots,
    elastic_ips,
    nat_gateways,
    vpc_interface_endpoints,
    load_balancers,
    rds_instances,
    eks_clusters,
    secrets,
    kms_keys,
    sagemaker,
]
