"""3-tier router: pre-call routing, deterministic gate, retry, then strictly-upward escalation."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml
from pydantic import BaseModel, Field, model_validator

from .metrics import MetricsTracker, RunMetrics
from .skill_loader import Skill
from .validator import ValidationResult, validate

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "routing.yaml"


# ---------------------------------------------------------------- config
class Price(BaseModel):
    input: float
    output: float


class TierConfig(BaseModel):
    name: str
    model: str
    effort: str
    price_per_mtok: Price


class Limits(BaseModel):
    max_retries_per_tier: int = Field(1, ge=0)
    max_total_attempts: int = Field(6, ge=1)
    bump_effort_on_retry: bool = True
    max_output_tokens: int = 2048


class Escalation(BaseModel):
    order: list[int] = [1, 2, 3]
    allow_downgrade: bool = False
    escalation_rate_warn: float = 0.30

    @model_validator(mode="after")
    def _monotonic(self) -> "Escalation":
        if self.allow_downgrade:
            raise ValueError("escalation.allow_downgrade must be false (monotonic escalation)")
        if self.order != sorted(set(self.order)):
            raise ValueError("escalation.order must be strictly increasing")
        return self


class Routing(BaseModel):
    token_threshold_tier2: int = 2000
    token_threshold_tier3: int = 8000
    complexity_tier: dict[str, int] = {"simple": 1, "multi_step": 2, "hard": 3}
    step_rules: dict[str, int] = {}
    skill_rules: dict[str, dict[str, int]] = {}
    case_rules: list[dict[str, Any]] = []


class RouterConfig(BaseModel):
    tiers: dict[int, TierConfig]
    effort_ladder: list[str] = ["low", "medium", "high"]
    limits: Limits = Limits()
    escalation: Escalation = Escalation()
    routing: Routing = Routing()

    @model_validator(mode="after")
    def _tiers_match_order(self) -> "RouterConfig":
        if set(self.tiers) != set(self.escalation.order):
            raise ValueError("tiers keys must equal escalation.order")
        return self


def load_config(path: str | Path = DEFAULT_CONFIG) -> RouterConfig:
    return RouterConfig.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


# ---------------------------------------------------------------- tasks and backends
@dataclass
class Task:
    skill: str
    input: str
    case_id: str = ""
    complexity: str = "simple"          # simple | multi_step | hard
    step: str | None = None             # e.g. plan | orchestrate | validate
    expected_tier: int | None = None    # for metrics only
    schema: dict[str, str] | None = None
    mock: dict[str, Any] = field(default_factory=dict)  # scripted behaviour for MockBackend


@dataclass(frozen=True)
class CallSpec:
    tier: int
    model: str
    effort: str
    call_index: int   # 0-based count of model calls so far in this run


@dataclass(frozen=True)
class Completion:
    text: str
    input_tokens: int
    output_tokens: int


class Backend(Protocol):
    def complete(self, spec: CallSpec, messages: list[dict], task: Task) -> Completion: ...


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class MockBackend:
    """Offline backend. task.mock: good, bad, min_tier (default 1), fail_first_calls (default 0)."""

    def complete(self, spec: CallSpec, messages: list[dict], task: Task) -> Completion:
        m = task.mock
        ok = spec.tier >= m.get("min_tier", 1) and spec.call_index >= m.get("fail_first_calls", 0)
        text = m.get("good", "") if ok else m.get("bad", "not json")
        return Completion(text, estimate_tokens(" ".join(x["content"] for x in messages)), estimate_tokens(text))


class LiteLLMBackend:
    """Live backend. Credentials come from the provider env vars litellm reads (ANTHROPIC_API_KEY, ...)."""

    def __init__(self, max_output_tokens: int = 2048) -> None:
        self.max_output_tokens = max_output_tokens

    def complete(self, spec: CallSpec, messages: list[dict], task: Task) -> Completion:
        import litellm  # lazy: offline runs don't need it

        r = litellm.completion(
            model=spec.model, messages=messages, reasoning_effort=spec.effort,
            max_tokens=self.max_output_tokens, drop_params=True,  # drop params a provider doesn't support
        )
        return Completion(r.choices[0].message.content or "", r.usage.prompt_tokens, r.usage.completion_tokens)


# ---------------------------------------------------------------- router
@dataclass
class Attempt:
    tier: int
    model: str
    effort: str
    validation: ValidationResult
    input_tokens: int
    output_tokens: int


@dataclass
class RunResult:
    output: str
    validation: ValidationResult
    start_tier: int
    route_reason: str
    attempts: list[Attempt]
    metrics: RunMetrics

    @property
    def passed(self) -> bool:
        return self.validation.passed


class TieredRouter:
    def __init__(self, skills: dict[str, Skill], config: RouterConfig | None = None,
                 backend: Backend | None = None, tracker: MetricsTracker | None = None) -> None:
        self.skills = skills
        self.cfg = config or load_config()
        self.backend = backend or MockBackend()
        self.tracker = tracker or MetricsTracker(self.cfg.escalation.escalation_rate_warn)

    # -- pre-call routing: highest tier any rule asks for wins
    def select_tier(self, task: Task) -> tuple[int, str]:
        r = self.cfg.routing
        votes: list[tuple[int, str]] = [(self.cfg.escalation.order[0], "default")]
        if task.complexity in r.complexity_tier:
            votes.append((r.complexity_tier[task.complexity], f"complexity={task.complexity}"))
        if task.step in r.step_rules:
            votes.append((r.step_rules[task.step], f"step={task.step}"))
        if (min_tier := r.skill_rules.get(task.skill, {}).get("min_tier")):
            votes.append((min_tier, f"skill rule {task.skill}"))
        for rule in r.case_rules:
            if all(getattr(task, k, None) == v for k, v in rule["when"].items()):
                votes.append((rule["tier"], f"case rule {rule['when']}"))
        tokens = estimate_tokens(task.input)
        if tokens >= r.token_threshold_tier3:
            votes.append((3, f"~{tokens} tokens >= {r.token_threshold_tier3}"))
        elif tokens >= r.token_threshold_tier2:
            votes.append((2, f"~{tokens} tokens >= {r.token_threshold_tier2}"))
        tier, why = max(votes, key=lambda v: v[0])
        return tier, why

    def _effort(self, tier: int, retry_n: int) -> str:
        ladder = self.cfg.effort_ladder
        base = ladder.index(self.cfg.tiers[tier].effort)
        step = retry_n if self.cfg.limits.bump_effort_on_retry else 0
        return ladder[min(base + step, len(ladder) - 1)]

    def run(self, task: Task) -> RunResult:
        skill = self.skills[task.skill]
        lim = self.cfg.limits
        start, why = self.select_tier(task)
        messages = [{"role": "system", "content": skill.body}, {"role": "user", "content": task.input}]
        attempts: list[Attempt] = []
        output, verdict = "", ValidationResult(False, "no attempt made")

        # Tiers are visited in config order and never revisited: strictly upward.
        for tier in (t for t in self.cfg.escalation.order if t >= start):
            tc = self.cfg.tiers[tier]
            for retry_n in range(lim.max_retries_per_tier + 1):
                if len(attempts) >= lim.max_total_attempts:
                    break
                effort = self._effort(tier, retry_n)
                spec = CallSpec(tier, tc.model, effort, call_index=len(attempts))
                c = self.backend.complete(spec, messages, task)
                output = c.text
                verdict = validate(output, skill, task.input, task.schema)
                attempts.append(Attempt(tier, tc.model, effort, verdict, c.input_tokens, c.output_tokens))
                if verdict.passed:
                    return self._finish(task, output, verdict, start, why, attempts)
                messages = messages[:2] + [{
                    "role": "user",
                    "content": f"{task.input}\n\nYour previous answer failed validation ({verdict}). Return a corrected answer only.",
                }]
            if len(attempts) >= lim.max_total_attempts:
                break
        return self._finish(task, output, verdict, start, why, attempts)

    def _finish(self, task: Task, output: str, verdict: ValidationResult, start: int, why: str,
                attempts: list[Attempt]) -> RunResult:
        tiers_used = len({a.tier for a in attempts})
        cost = sum(
            a.input_tokens * self.cfg.tiers[a.tier].price_per_mtok.input
            + a.output_tokens * self.cfg.tiers[a.tier].price_per_mtok.output
            for a in attempts
        ) / 1_000_000
        m = RunMetrics(
            skill=task.skill, case_id=task.case_id, expected_tier=task.expected_tier,
            start_tier=start, final_tier=attempts[-1].tier, attempts=len(attempts),
            retries=len(attempts) - tiers_used, escalations=tiers_used - 1,
            input_tokens=sum(a.input_tokens for a in attempts),
            output_tokens=sum(a.output_tokens for a in attempts),
            cost_usd=cost, passed=verdict.passed,
        )
        self.tracker.add(m)
        return RunResult(output, verdict, start, why, attempts, m)
