"""Per-run metrics and aggregate reporting (pass rate, escalation rate, cost)."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass


@dataclass
class RunMetrics:
    skill: str
    case_id: str
    expected_tier: int | None
    start_tier: int          # tier chosen by the pre-call router
    final_tier: int          # tier that produced the final output
    attempts: int
    retries: int             # attempts beyond the first on a tier
    escalations: int         # upward tier moves
    input_tokens: int
    output_tokens: int
    cost_usd: float
    passed: bool

    @property
    def routed_as_expected(self) -> bool | None:
        return None if self.expected_tier is None else self.start_tier == self.expected_tier


def _rate(n: int, d: int) -> float:
    return n / d if d else 0.0


class MetricsTracker:
    def __init__(self, escalation_rate_warn: float = 0.30) -> None:
        self.runs: list[RunMetrics] = []
        self.warn_at = escalation_rate_warn

    def add(self, m: RunMetrics) -> None:
        self.runs.append(m)

    def escalation_rate(self, skill: str | None = None) -> float:
        runs = [r for r in self.runs if skill in (None, r.skill)]
        return _rate(sum(r.escalations > 0 for r in runs), len(runs))

    def summary(self) -> dict:
        runs = self.runs
        expected = [r for r in runs if r.expected_tier is not None]
        by_skill: dict[str, list[RunMetrics]] = defaultdict(list)
        for r in runs:
            by_skill[r.skill].append(r)
        per_skill = {s: self.escalation_rate(s) for s in by_skill}
        return {
            "runs": len(runs),
            "pass_rate": _rate(sum(r.passed for r in runs), len(runs)),
            "escalation_rate": self.escalation_rate(),
            "routing_accuracy": _rate(sum(bool(r.routed_as_expected) for r in expected), len(expected)),
            "retries": sum(r.retries for r in runs),
            "escalations": sum(r.escalations for r in runs),
            "input_tokens": sum(r.input_tokens for r in runs),
            "output_tokens": sum(r.output_tokens for r in runs),
            "cost_usd": round(sum(r.cost_usd for r in runs), 6),
            "escalation_rate_by_skill": per_skill,
            "skills_over_escalation_threshold": sorted(s for s, v in per_skill.items() if v > self.warn_at),
        }

    def to_dicts(self) -> list[dict]:
        return [asdict(r) for r in self.runs]
