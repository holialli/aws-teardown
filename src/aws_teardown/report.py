import json

# Things that hold on to other things go first: instances before their volumes,
# NAT gateways before their Elastic IPs, node instances before the EKS cluster.
DELETE_ORDER = [
    "ec2-instance",
    "sagemaker-endpoint",
    "sagemaker-notebook",
    "load-balancer",
    "nat-gateway",
    "vpc-endpoint",
    "rds-instance",
    "eks-cluster",
    "elastic-ip",
    "ebs-volume",
    "ebs-snapshot",
    "secret",
    "kms-key",
]


def delete_order(resource):
    rank = DELETE_ORDER.index(resource.kind) if resource.kind in DELETE_ORDER else len(DELETE_ORDER)
    return (rank, resource.region, resource.id)


def _cost(value):
    return "?" if value is None else f"${value:,.2f}"


def _clip(text, width):
    return text if len(text) <= width else text[: width - 1] + "…"


def _display_name(resource):
    # ARNs are long and unreadable in a table; the name is enough to find it.
    if resource.id.startswith("arn:"):
        return resource.name
    if resource.name and resource.name != resource.id:
        return f"{resource.id} ({resource.name})"
    return resource.id


def render_text(result, show_commands=True, title=None):
    lines = []
    if title:
        lines.append(title)
    if not result.resources:
        lines.append(f"Nothing billable found in {len(result.regions)} region(s). You're clean.")
    else:
        rows = []
        for r in result.resources:
            rows.append((_cost(r.monthly_cost), r.kind, r.region, _clip(_display_name(r), 48), r.note))
        headers = ("~$/MONTH", "TYPE", "REGION", "RESOURCE", "NOTES")
        widths = [max(len(h), *(len(row[i]) for row in rows)) for i, h in enumerate(headers[:4])]
        fmt = "  ".join(f"{{:<{w}}}" for w in widths) + "  {}"
        lines.append(fmt.format(*headers))
        lines.append(fmt.format(*("-" * w for w in widths), "-----"))
        for row in rows:
            lines.append(fmt.format(*row))
        unknown = sum(1 for r in result.resources if r.monthly_cost is None)
        lines.append("")
        lines.append(
            f"{len(result.resources)} resource(s) in account {result.account}, "
            f"roughly ${result.total_monthly_cost:,.2f}/month"
            + (f" plus {unknown} with unknown price" if unknown else "")
            + "."
        )
        lines.append("Prices are us-east-1 on-demand estimates and leave out data transfer.")

        if show_commands:
            lines.append("")
            lines.append("To delete, in this order (read each one first, these are not reversible):")
            for r in sorted(result.resources, key=delete_order):
                if r.delete_command:
                    lines.append(f"  {r.delete_command}")

    if result.warnings:
        lines.append("")
        lines.append("Could not check (missing permissions?):")
        for w in result.warnings:
            lines.append(f"  {w}")
    return "\n".join(lines)


def render_json(result):
    return json.dumps(result.to_dict(), indent=2, default=str)
