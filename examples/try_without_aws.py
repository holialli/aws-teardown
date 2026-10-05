"""Try aws-teardown with no AWS account, against moto's local fake AWS.

    pip install aws-teardown "moto[server]"
    python examples/try_without_aws.py

It starts a moto server on localhost, creates one "existing" resource, takes a
snapshot, then creates what a typical VPC + EC2 tutorial leaves behind, and
runs `diff` and `scan` so you can see the output. Nothing touches real AWS.
"""

import logging
import os
import subprocess
import sys
import tempfile

import boto3
from moto.server import ThreadedMotoServer

PORT = 5123
REGION = "us-east-1"


def run(*args):
    print(f"\n$ aws-teardown {' '.join(args)}", flush=True)
    subprocess.run([sys.executable, "-m", "aws_teardown", *args], check=False)


def main():
    workdir = tempfile.mkdtemp()  # the snapshot file goes here, not in your folder
    # moto ships sample AMIs whose snapshots it files under your fake account; real AWS
    # wouldn't, so start with none. Any AMI ID is accepted when moto isn't validating them.
    empty_amis = os.path.join(workdir, "amis.json")
    with open(empty_amis, "w") as fh:
        fh.write("[]")
    os.environ["MOTO_AMIS_PATH"] = empty_amis

    logging.getLogger("werkzeug").setLevel(logging.ERROR)  # hide moto's request log
    server = ThreadedMotoServer(port=PORT, verbose=False)
    server.start()

    # Every boto3 client, including the ones inside aws-teardown, now talks to moto.
    os.environ.update(
        AWS_ENDPOINT_URL=f"http://127.0.0.1:{PORT}",
        AWS_ACCESS_KEY_ID="testing",
        AWS_SECRET_ACCESS_KEY="testing",
        AWS_DEFAULT_REGION=REGION,
    )
    os.chdir(workdir)

    ec2 = boto3.client("ec2", region_name=REGION)
    ami = "ami-0123456789abcdef0"

    # Something you already had before the tutorial. `diff` should leave it out.
    ec2.run_instances(
        ImageId=ami,
        InstanceType="t3.micro",
        MinCount=1,
        MaxCount=1,
        TagSpecifications=[{"ResourceType": "instance", "Tags": [{"Key": "Name", "Value": "my-existing-server"}]}],
    )

    run("snapshot", "--region", REGION)

    # What a "VPC with a private subnet" tutorial typically leaves running.
    vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")["Vpc"]["VpcId"]
    subnet = ec2.create_subnet(VpcId=vpc, CidrBlock="10.0.1.0/24")["Subnet"]["SubnetId"]
    eip = ec2.allocate_address(Domain="vpc")["AllocationId"]
    ec2.create_nat_gateway(
        SubnetId=subnet,
        AllocationId=eip,
        TagSpecifications=[{"ResourceType": "natgateway", "Tags": [{"Key": "Name", "Value": "tutorial-nat"}]}],
    )
    ec2.run_instances(
        ImageId=ami,
        InstanceType="t3.medium",
        MinCount=1,
        MaxCount=1,
        SubnetId=subnet,
        TagSpecifications=[{"ResourceType": "instance", "Tags": [{"Key": "Name", "Value": "tutorial-web"}]}],
    )
    ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=20, VolumeType="gp3")
    boto3.client("secretsmanager", region_name=REGION).create_secret(
        Name="tutorial-db-password", SecretString="not-a-real-password"
    )

    run("diff", "--region", REGION)
    run("scan", "--region", REGION, "--no-commands")

    server.stop()


if __name__ == "__main__":
    main()
