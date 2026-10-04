from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class Resource:
    """Something in the account that is (or may be) costing money."""

    kind: str
    region: str
    id: str
    name: str = ""
    monthly_cost: Optional[float] = None
    note: str = ""
    delete_command: str = ""
    tags: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        """Stable identity used to compare a scan against a snapshot."""
        return f"{self.kind}:{self.region}:{self.id}"

    def to_dict(self) -> dict:
        data = asdict(self)
        data["key"] = self.key
        return data


@dataclass
class ScanResult:
    account: str
    regions: list
    resources: list
    warnings: list

    @property
    def total_monthly_cost(self) -> float:
        return round(sum(r.monthly_cost or 0 for r in self.resources), 2)

    def to_dict(self) -> dict:
        return {
            "account": self.account,
            "regions": self.regions,
            "total_monthly_cost": self.total_monthly_cost,
            "resources": [r.to_dict() for r in self.resources],
            "warnings": self.warnings,
        }
