import argparse
import json
import sys
from datetime import datetime, timezone

import boto3
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

from . import __version__
from .models import ScanResult
from .report import render_json, render_text
from .scan import scan

DEFAULT_SNAPSHOT = ".aws-teardown-snapshot.json"
EXIT_OVER_BUDGET = 2


def build_parser():
    parser = argparse.ArgumentParser(
        prog="aws-teardown",
        description="Find AWS resources that are still costing you money. Read-only: it never deletes anything.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--profile", help="AWS CLI profile to use")
    common.add_argument(
        "--region",
        action="append",
        dest="regions",
        metavar="REGION",
        help="only scan this region (repeatable). Default: every enabled region",
    )

    output = argparse.ArgumentParser(add_help=False)
    output.add_argument("--json", action="store_true", help="print JSON instead of a table")
    output.add_argument("--no-commands", action="store_true", help="don't print delete commands")
    output.add_argument(
        "--fail-over",
        type=float,
        metavar="USD",
        help=f"exit with code {EXIT_OVER_BUDGET} if the estimated monthly cost is above this (for cron/CI)",
    )

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("scan", parents=[common, output], help="list everything billable, most expensive first")
    snap = sub.add_parser("snapshot", parents=[common], help="record what exists now, before you start a tutorial")
    snap.add_argument("-o", "--output", default=DEFAULT_SNAPSHOT, help=f"snapshot file (default {DEFAULT_SNAPSHOT})")
    diff = sub.add_parser("diff", parents=[common, output], help="show only what was created since the snapshot")
    diff.add_argument("-i", "--input", default=DEFAULT_SNAPSHOT, help=f"snapshot file (default {DEFAULT_SNAPSHOT})")
    return parser


def _session(args):
    return boto3.Session(profile_name=args.profile) if args.profile else boto3.Session()


def _emit(result, args, title=None):
    if args.json:
        print(render_json(result))
    else:
        print(render_text(result, show_commands=not args.no_commands, title=title))
    if args.fail_over is not None and result.total_monthly_cost > args.fail_over:
        print(
            f"\nEstimated ${result.total_monthly_cost:,.2f}/month is over the ${args.fail_over:,.2f} limit.",
            file=sys.stderr,
        )
        return EXIT_OVER_BUDGET
    return 0


def cmd_scan(args):
    return _emit(scan(_session(args), args.regions), args)


def cmd_snapshot(args):
    result = scan(_session(args), args.regions)
    data = {
        "taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "account": result.account,
        "regions": result.regions,
        "keys": sorted(r.key for r in result.resources),
    }
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    print(
        f"Saved {len(data['keys'])} existing resource(s) across {len(result.regions)} region(s) to {args.output}.\n"
        "Go do your tutorial, then run: aws-teardown diff"
    )
    return 0


def cmd_diff(args):
    try:
        with open(args.input, encoding="utf-8") as fh:
            snapshot = json.load(fh)
    except FileNotFoundError:
        print(f"No snapshot at {args.input}. Run `aws-teardown snapshot` first.", file=sys.stderr)
        return 1

    # Scan the same regions the snapshot covered, unless told otherwise.
    result = scan(_session(args), args.regions or snapshot.get("regions"))
    if result.account != snapshot.get("account"):
        print(
            f"Snapshot is for account {snapshot.get('account')}, but these credentials are for {result.account}.",
            file=sys.stderr,
        )
        return 1

    before = set(snapshot.get("keys", []))
    new = ScanResult(
        account=result.account,
        regions=result.regions,
        resources=[r for r in result.resources if r.key not in before],
        warnings=result.warnings,
    )
    return _emit(new, args, title=f"Created since snapshot of {snapshot.get('taken_at', '?')}:\n")


def main(argv=None):
    args = build_parser().parse_args(argv)
    handlers = {"scan": cmd_scan, "snapshot": cmd_snapshot, "diff": cmd_diff}
    try:
        return handlers[args.command](args)
    except NoCredentialsError:
        print("No AWS credentials found. Run `aws configure` or pass --profile.", file=sys.stderr)
    except (ClientError, BotoCoreError) as err:
        print(f"AWS error: {err}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
