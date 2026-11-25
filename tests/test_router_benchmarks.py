"""Offline benchmarks: parameterized eval cases, escalation behaviour, cost / pass-rate gates.

Run:   pytest
Live:  RUN_LIVE=1 ANTHROPIC_API_KEY=... pytest -m live
"""
import json
import os
from pathlib import Path

import pytest

from tiered_skill_router import MetricsTracker, SkillError, Task, TieredRouter, load_config, load_skill, load_skills, validate
from tiered_skill_router.validator import validate_json_schema

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "tests" / "eval_cases.json").read_text())
SKILLS = load_skills(ROOT / "skills")
SKILL = "sample-json-extractor"


def to_task(c: dict) -> Task:
    return Task(
        skill=SKILL, input=c["input"] + "x" * c.get("pad_chars", 0), case_id=c.get("case_id_override", c["id"]),
        complexity=c.get("complexity", "simple"), step=c.get("step"),
        expected_tier=c["expect"]["start_tier"], mock=c["mock"],
    )


@pytest.fixture(scope="module")
def results():
    router = TieredRouter(SKILLS)  # MockBackend: no network, no keys
    return router, {c["id"]: router.run(to_task(c)) for c in CASES}


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c['kind']}:{c['id']}")
def test_case(case, results):
    router, runs = results
    run, exp = runs[case["id"]], case["expect"]
    m = run.metrics
    assert (run.start_tier, m.final_tier, m.attempts, run.passed, m.retries, m.escalations) == (
        exp["start_tier"], exp["final_tier"], exp["attempts"], exp["passed"], exp["retries"], exp["escalations"])
    tiers = [a.tier for a in run.attempts]
    assert tiers == sorted(tiers), "escalation must be monotonic"
    assert m.attempts <= router.cfg.limits.max_total_attempts
    assert str(run.validation) == "PASS" if run.passed else str(run.validation).startswith("FAIL: ")


def test_effort_bumps_on_retry(results):
    _, runs = results
    assert [a.effort for a in runs["escalate-1-to-2"].attempts] == ["low", "medium", "medium"]
    assert [a.effort for a in runs["escalate-1-to-3"].attempts] == ["low", "medium", "medium", "high", "high"]


def test_aggregate_metrics(results):
    router, runs = results
    s = router.tracker.summary()
    positives = [runs[c["id"]] for c in CASES if c["kind"] == "positive"]
    assert all(r.passed for r in positives), "pass rate on positive cases must be 100%"
    assert not any(runs[c["id"]].passed for c in CASES if c["kind"] == "negative")
    assert s["runs"] == len(CASES) and s["routing_accuracy"] == 1.0
    assert s["escalation_rate"] == sum(c["expect"]["escalations"] > 0 for c in CASES) / len(CASES)
    assert s["cost_usd"] > 0
    # Escalating costs more than succeeding on tier 1 for the same input.
    assert runs["escalate-1-to-3"].metrics.cost_usd > runs["simple-passes-tier1"].metrics.cost_usd
    assert SKILL in s["skills_over_escalation_threshold"]  # eval set is deliberately escalation-heavy


def test_escalation_guard_quiet_when_tier1_suffices():
    r = TieredRouter(SKILLS)
    for c in CASES[:1] * 5:
        r.run(to_task(c))
    assert r.tracker.summary()["escalation_rate"] == 0.0
    assert r.tracker.summary()["skills_over_escalation_threshold"] == []


def test_total_attempt_cap_is_hard():
    cfg = load_config()
    cfg.limits.max_total_attempts = 3
    run = TieredRouter(SKILLS, cfg).run(to_task(next(c for c in CASES if c["id"] == "never-passes-hits-attempt-cap")))
    assert run.metrics.attempts == 3 and not run.passed


def test_config_rejects_downgrades():
    from tiered_skill_router.router import Escalation
    with pytest.raises(ValueError):
        Escalation(allow_downgrade=True)
    with pytest.raises(ValueError):
        Escalation(order=[3, 2, 1])


# ---- validator and loader
def test_validator_verdict_strings():
    skill = SKILLS[SKILL]
    ok = '{"name": "A", "email": "a@b.co", "amount": 1}'
    assert str(validate(ok, skill, "mail a@b.co")) == "PASS"
    assert str(validate("nope", skill, "")).startswith("FAIL: not valid JSON")
    assert str(validate(ok, skill, "no email here")) .startswith("FAIL: email does not appear")
    assert str(validate("  ", skill)) == "FAIL: empty output"


def test_schema_validator():
    assert validate_json_schema('{"a": 1}', {"a": "int"}).passed
    assert not validate_json_schema('{"a": true}', {"a": "int"}).passed
    assert str(validate_json_schema('{"a": 1, "b": 2}', {"a": "int"})) == "FAIL: unexpected keys: b"


def test_skill_loader_errors(tmp_path):
    d = tmp_path / "bad-skill"
    d.mkdir()
    with pytest.raises(SkillError, match="SKILL.md not found"):
        load_skill(d)
    (d / "SKILL.md").write_text("---\nname: other\ndescription: x\n---\nbody")
    with pytest.raises(SkillError, match="must match directory"):
        load_skill(d)
    (d / "SKILL.md").write_text("---\nname: bad-skill\ndescription: ''\n---\nbody")
    with pytest.raises(SkillError, match="description"):
        load_skill(d)
    (d / "SKILL.md").write_text("---\nname: bad-skill\ndescription: ok\n---\nbody")
    assert load_skill(d).validate_script is None


def test_sample_skill_discovered():
    s = SKILLS[SKILL]
    assert s.validate_script and s.validate_script.name == "validate.py"


# ---- live mode (skipped by default)
@pytest.mark.live
@pytest.mark.skipif(os.environ.get("RUN_LIVE") != "1", reason="set RUN_LIVE=1 and provider keys to run")
def test_live_smoke():
    from tiered_skill_router import LiteLLMBackend
    r = TieredRouter(SKILLS, backend=LiteLLMBackend())
    run = r.run(Task(skill=SKILL, input=CASES[0]["input"]))
    print(run.output, run.metrics)
    assert run.passed
