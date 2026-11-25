# tiered-skill-router

Runs portable `SKILL.md` workflows across three model tiers with deterministic validation and strictly-upward fail-over, via [`litellm`](https://github.com/BerriAI/litellm).

```
tiered-skill-router/
├── config/routing.yaml                 tiers, routing rules, effort ladder, limits, escalation order
├── src/tiered_skill_router/
│   ├── skill_loader.py                 SKILL.md frontmatter + scripts/ + references/
│   ├── validator.py                    PASS / "FAIL: <reason>", runs scripts/validate.py
│   ├── router.py                       routing, retries, escalation, Mock + LiteLLM backends
│   └── metrics.py                      tier, retries, escalations, tokens, cost, rates
├── skills/sample-json-extractor/       working sample skill + validator
└── tests/                              eval_cases.json + parameterized benchmarks
```

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
pytest            # offline, mock backend, no keys needed
```

## Configuration (`config/routing.yaml`)

| Tier | Role | Default model | Effort |
|---|---|---|---|
| 1 | Fast worker | `claude-haiku-5-5` | low |
| 2 | Balanced orchestrator / validator | `claude-sonnet-5-5` | medium |
| 3 | Flagship escalation | `claude-opus-5-5` | high |

`model` is any litellm ID, so you can mix providers (`openai/...`, `gemini/...`, `anthropic/...`). Prices in the file only feed the cost estimate. API keys are read from the provider env vars litellm uses (e.g. `ANTHROPIC_API_KEY`); nothing is stored in the repo.

## Routing and fail-over

1. **Pre-call routing:** the highest tier requested by any rule wins: task `complexity` (`simple`/`multi_step`/`hard`), `step` rules, per-skill `min_tier`, `case_rules`, and estimated input tokens (chars/4) against the two thresholds.
2. **Deterministic gate:** every response goes through `validator.validate` (non-empty, optional schema, then the skill's `scripts/validate.py`). Model self-grading is never used.
3. **Retry:** on failure, retry the same tier up to `max_retries_per_tier`, bumping effort one step on the ladder (`low`→`medium`→`high`), with the failure reason fed back.
4. **Escalate:** then move to the next tier in `escalation.order`. Tiers are never revisited and downgrades are rejected by config validation.
5. **Hard cap:** `max_total_attempts` bounds all calls per run.

`MetricsTracker.summary()` reports `pass_rate`, `escalation_rate` (share of runs that moved up a tier, overall and per skill), `routing_accuracy` (start tier vs `expected_tier`), retries, tokens and `cost_usd`. Skills above `escalation_rate_warn` are listed in `skills_over_escalation_threshold`; consider raising that skill's `min_tier` or fixing its prompt.

## Tests

```bash
pytest                                              # offline benchmarks (mock backend)
RUN_LIVE=1 ANTHROPIC_API_KEY=sk-... pytest -m live  # real models via litellm; spends money
```

Offline cases in `tests/eval_cases.json` script the mock per case: `good`/`bad` output, `min_tier` (lowest tier that succeeds), `fail_first_calls`. Each case states expected start tier, final tier, attempts, retries and escalations. Mock mode proves the routing and gating logic, not model quality.

Use it in code:

```python
from tiered_skill_router import LiteLLMBackend, Task, TieredRouter, load_skills
router = TieredRouter(load_skills("skills"), backend=LiteLLMBackend())
run = router.run(Task(skill="sample-json-extractor", input="Ada <ada@example.com> owes 12"))
print(run.validation, run.metrics, router.tracker.summary())
```

## Adding a skill

1. Create `skills/<name>/SKILL.md` with frontmatter `name` (lowercase-hyphen, equals the folder name) and `description`, then the instructions.
2. Optional: `scripts/validate.py` (reads the output on stdin and the source text from `$SKILL_INPUT`, prints `PASS` or `FAIL: <reason>`, exit 0 on pass; runs with a minimal env, 10s timeout) and `references/`.
3. Optional routing: `routing.skill_rules.<name>.min_tier` in the config.
4. Add cases to `tests/eval_cases.json`.
