import json

import boto3
import pytest
from moto import mock_aws

from aws_teardown.cli import EXIT_OVER_BUDGET, main

from .conftest import REGION


@pytest.fixture
def aws():
    with mock_aws():
        yield boto3.client("ec2", region_name=REGION)


def test_scan_prints_table_and_commands(aws, capsys):
    vol = aws.create_volume(AvailabilityZone=f"{REGION}a", Size=50, VolumeType="gp2")

    assert main(["scan", "--region", REGION]) == 0

    out = capsys.readouterr().out
    assert vol["VolumeId"] in out
    assert "$5.00" in out
    assert f"aws ec2 delete-volume --volume-id {vol['VolumeId']}" in out


def test_scan_json(aws, capsys):
    aws.allocate_address(Domain="vpc")

    assert main(["scan", "--region", REGION, "--json"]) == 0

    data = json.loads(capsys.readouterr().out)
    assert data["total_monthly_cost"] == 3.65
    assert data["resources"][0]["kind"] == "elastic-ip"


def test_fail_over_budget(aws, capsys):
    aws.allocate_address(Domain="vpc")

    assert main(["scan", "--region", REGION, "--fail-over", "1"]) == EXIT_OVER_BUDGET
    assert main(["scan", "--region", REGION, "--fail-over", "10"]) == 0


def test_snapshot_then_diff_shows_only_new(aws, tmp_path, capsys):
    snap = tmp_path / "snap.json"
    old = aws.create_volume(AvailabilityZone=f"{REGION}a", Size=10, VolumeType="gp3")

    assert main(["snapshot", "--region", REGION, "-o", str(snap)]) == 0
    saved = json.loads(snap.read_text())
    assert saved["keys"] == [f"ebs-volume:{REGION}:{old['VolumeId']}"]
    assert saved["regions"] == [REGION]

    new = aws.create_volume(AvailabilityZone=f"{REGION}a", Size=10, VolumeType="gp3")
    capsys.readouterr()

    assert main(["diff", "-i", str(snap), "--json"]) == 0

    data = json.loads(capsys.readouterr().out)
    assert [r["id"] for r in data["resources"]] == [new["VolumeId"]]


def test_diff_without_snapshot(aws, tmp_path, capsys):
    assert main(["diff", "-i", str(tmp_path / "missing.json")]) == 1
    assert "Run `aws-teardown snapshot` first" in capsys.readouterr().err


def test_diff_refuses_other_account(aws, tmp_path, capsys):
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps({"account": "999999999999", "regions": [REGION], "keys": []}))

    assert main(["diff", "-i", str(snap)]) == 1
    assert "999999999999" in capsys.readouterr().err
