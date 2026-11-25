"""Compare models head-to-head on one skill, live. One attempt per input, no retries or escalation.

    python compare.py claude-opus-5-5 openai/gpt-5 gemini/gemini-2.5-pro
    python compare.py --skill my-skill modelA modelB

Test inputs come from skills/<skill>/evals.json (a JSON list of input strings).
Spends real money. Models whose provider key is not set are skipped.
Cost uses litellm's built-in price table; models missing from it show cost 0.
"""
import argparse
import json
import time
from pathlib import Path

import litellm

from tiered_skill_router import LiteLLMBackend, Task, TieredRouter, load_config, load_skills
from tiered_skill_router.router import Price

ROOT = Path(__file__).parent
ap = argparse.ArgumentParser()
ap.add_argument("models", nargs="+")
ap.add_argument("--skill", default="sample-json-extractor")
args = ap.parse_args()

skills = load_skills(ROOT / "skills")
inputs = json.loads((ROOT / "skills" / args.skill / "evals.json").read_text())
rows = []
for model in args.models:
    if not litellm.validate_environment(model).get("keys_in_environment"):
        print(f"skip {model}: provider API key not set")
        continue
    info = litellm.model_cost.get(model.split("/", 1)[-1], {})
    cfg = load_config()
    cfg.limits.max_retries_per_tier, cfg.limits.max_total_attempts = 0, 1
    for tier in cfg.tiers.values():  # every tier = the model under test
        tier.model, tier.effort = model, "medium"
        tier.price_per_mtok = Price(input=info.get("input_cost_per_token", 0) * 1e6,
                                    output=info.get("output_cost_per_token", 0) * 1e6)
    router = TieredRouter(skills, cfg, LiteLLMBackend(cfg.limits.max_output_tokens))
    t0, errors = time.time(), 0
    for i, text in enumerate(inputs):
        try:
            router.run(Task(skill=args.skill, input=text, case_id=f"live-{i}"))
        except Exception as e:  # provider/auth/model-ID errors count as failures, run continues
            errors += 1
            print(f"  {model}: {type(e).__name__}: {str(e)[:120]}")
    rows.append((model, router.tracker.summary(), (time.time() - t0) / len(inputs), errors))

print(f"\nskill: {args.skill}, {len(inputs)} inputs, 1 attempt each")
print(f"{'model':<28}{'pass':>6}{'errors':>7}{'tok in/out':>14}{'cost $':>10}{'sec/run':>9}")
for model, s, sec, err in rows:
    ok = s["pass_rate"] * s["runs"]
    print(f"{model:<28}{ok:>3.0f}/{len(inputs):<2}{err:>7}{s['input_tokens']:>8}/{s['output_tokens']:<5}{s['cost_usd']:>10.5f}{sec:>9.1f}")
