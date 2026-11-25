"""Run the sample skill live on every provider whose API key is set and print one results table.

    python compare.py                       # all config/routing*.yaml
    python compare.py config/routing.yaml   # chosen configs only
Spends real money (a handful of small requests per provider).
"""
import os
import sys
from pathlib import Path

from tiered_skill_router import LiteLLMBackend, Task, TieredRouter, load_config, load_skills

ROOT = Path(__file__).parent
KEYS = {"openai/": "OPENAI_API_KEY", "gemini/": "GEMINI_API_KEY"}  # anything else: Anthropic
INPUTS = [
    "Invoice from Ada Lovelace <ada@example.com>, total 120.50 due Friday.",
    "Hi, it's Grace Hopper (grace.hopper@navy.mil). Please send the 1,200 USD refund.",
    "Contact: Alan Turing, alan@bletchley.org.uk, balance 0.75",
    "From: Linus T. <linus@kernel.org>. Payment of 99 received.",
]

skills = load_skills(ROOT / "skills")
paths = sys.argv[1:] or sorted(str(p) for p in (ROOT / "config").glob("routing*.yaml"))
rows = []
for p in paths:
    cfg = load_config(p)
    model = cfg.tiers[1].model
    key = next((v for k, v in KEYS.items() if model.startswith(k)), "ANTHROPIC_API_KEY")
    if not os.environ.get(key):
        print(f"skip {p}: {key} not set")
        continue
    router = TieredRouter(skills, cfg, LiteLLMBackend(cfg.limits.max_output_tokens))
    for i, text in enumerate(INPUTS):
        router.run(Task(skill="sample-json-extractor", input=text, case_id=f"live-{i}"))
    rows.append((model.split("/")[0] if "/" in model else "anthropic", router.tracker.summary()))

print(f"{'provider':<11}{'pass':>6}{'escal':>7}{'retries':>8}{'tokens in/out':>16}{'cost $':>10}")
for name, s in rows:
    print(f"{name:<11}{s['pass_rate']:>6.0%}{s['escalation_rate']:>7.0%}{s['retries']:>8}"
          f"{s['input_tokens']:>9}/{s['output_tokens']:<6}{s['cost_usd']:>10.5f}")
