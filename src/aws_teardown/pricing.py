"""Rough monthly prices, us-east-1 on-demand, 730 hours a month.

These are estimates to rank what matters, not a bill. Other regions are
usually within 10-30% of these numbers. Data transfer, requests and LCUs
are not included.
"""

HOURS_PER_MONTH = 730

PUBLIC_IPV4_HOURLY = 0.005
NAT_GATEWAY_HOURLY = 0.045
ALB_HOURLY = 0.0225
NLB_HOURLY = 0.0225
GWLB_HOURLY = 0.0125
CLASSIC_ELB_HOURLY = 0.025
EKS_CLUSTER_HOURLY = 0.10
INTERFACE_ENDPOINT_HOURLY_PER_AZ = 0.01
SECRET_MONTHLY = 0.40
KMS_KEY_MONTHLY = 1.00
EBS_SNAPSHOT_GB_MONTH = 0.05
RDS_STORAGE_GB_MONTH = 0.115

EBS_GB_MONTH = {
    "gp2": 0.10,
    "gp3": 0.08,
    "io1": 0.125,
    "io2": 0.125,
    "st1": 0.045,
    "sc1": 0.015,
    "standard": 0.05,
}

EC2_HOURLY = {
    "t2.nano": 0.0058,
    "t2.micro": 0.0116,
    "t2.small": 0.023,
    "t2.medium": 0.0464,
    "t2.large": 0.0928,
    "t3.nano": 0.0052,
    "t3.micro": 0.0104,
    "t3.small": 0.0208,
    "t3.medium": 0.0416,
    "t3.large": 0.0832,
    "t3a.micro": 0.0094,
    "t3a.small": 0.0188,
    "t3a.medium": 0.0376,
    "t4g.nano": 0.0042,
    "t4g.micro": 0.0084,
    "t4g.small": 0.0168,
    "t4g.medium": 0.0336,
    "m5.large": 0.096,
    "m6i.large": 0.096,
    "c5.large": 0.085,
}

RDS_HOURLY = {
    "db.t3.micro": 0.017,
    "db.t3.small": 0.034,
    "db.t3.medium": 0.068,
    "db.t4g.micro": 0.016,
    "db.t4g.small": 0.032,
    "db.t4g.medium": 0.065,
}

SAGEMAKER_NOTEBOOK_HOURLY = {
    "ml.t2.medium": 0.0464,
    "ml.t3.medium": 0.05,
}

FREE_TIER_EC2 = {"t2.micro", "t3.micro"}
FREE_TIER_RDS = {"db.t2.micro", "db.t3.micro", "db.t4g.micro"}


def monthly(hourly: float) -> float:
    return round(hourly * HOURS_PER_MONTH, 2)
