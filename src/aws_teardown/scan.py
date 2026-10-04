from concurrent.futures import ThreadPoolExecutor

from botocore.exceptions import BotoCoreError, ClientError, EndpointConnectionError

from . import scanners
from .models import ScanResult

# Errors that just mean "this service/region isn't available to you", not a problem worth showing.
QUIET_ERRORS = {"OptInRequired", "AuthFailure", "UnrecognizedClientException", "InvalidClientTokenId"}


def enabled_regions(session):
    ec2 = session.client("ec2", region_name=session.region_name or "us-east-1")
    return sorted(r["RegionName"] for r in ec2.describe_regions()["Regions"])


def scan(session, regions=None, max_workers=16):
    """Run every scanner in every region and collect the results."""
    sts = session.client("sts", region_name=session.region_name or "us-east-1")
    account = sts.get_caller_identity()["Account"]
    regions = regions or enabled_regions(session)

    jobs = [(fn, region) for region in regions for fn in scanners.ALL]
    resources, warnings = [], []

    def run(job):
        fn, region = job
        try:
            return fn(session, region), None
        except EndpointConnectionError:
            return [], None
        except ClientError as err:
            code = err.response.get("Error", {}).get("Code", "ClientError")
            if code in QUIET_ERRORS:
                return [], None
            return [], f"{fn.__name__} in {region}: {code}"
        except BotoCoreError as err:
            return [], f"{fn.__name__} in {region}: {err.__class__.__name__}"

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for found, warning in pool.map(run, jobs):
            resources.extend(found)
            if warning:
                warnings.append(warning)

    resources.sort(key=sort_key)
    return ScanResult(account=account, regions=regions, resources=resources, warnings=sorted(warnings))


def sort_key(resource):
    # Most expensive first. Unknown prices go just below anything that costs money,
    # so they are still near the top where people will look.
    cost = resource.monthly_cost
    return (-(cost if cost is not None else 0.001), resource.kind, resource.region, resource.id)
