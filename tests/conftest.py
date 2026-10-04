import boto3
import pytest
from moto import mock_aws

REGION = "us-east-1"


@pytest.fixture(autouse=True)
def aws_credentials(monkeypatch):
    for var, value in {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SESSION_TOKEN": "testing",
        "AWS_DEFAULT_REGION": REGION,
        # moto registers ~1,000 public AMI snapshots under the test account by default,
        # which would all show up as "your" snapshots.
        "MOTO_EC2_LOAD_DEFAULT_AMIS": "false",
    }.items():
        monkeypatch.setenv(var, value)
    monkeypatch.delenv("AWS_PROFILE", raising=False)


@pytest.fixture
def session():
    with mock_aws():
        yield boto3.Session(region_name=REGION)


@pytest.fixture
def ec2(session):
    return session.client("ec2", region_name=REGION)


@pytest.fixture
def ami():
    # moto doesn't validate AMI IDs unless MOTO_ENABLE_AMI_VALIDATION is set.
    return "ami-12c6146b"


@pytest.fixture
def subnets(ec2):
    return [s["SubnetId"] for s in ec2.describe_subnets()["Subnets"]]
