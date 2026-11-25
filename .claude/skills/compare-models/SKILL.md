---
name: compare-models
description: Compare two or more models head-to-head on a skill by running real API calls and validating outputs deterministically. Use only when the user runs /compare-models with model names.
argument-hint: <model1> <model2> [more models] [--skill name]
disable-model-invocation: true
---

Run the comparison from the repo root and show the user the printed table unchanged:

```bash
.venv/bin/python compare.py $ARGUMENTS
```

Models are litellm IDs, e.g. `claude-opus-5-5`, `openai/gpt-5`, `gemini/gemini-2.5-pro`.
This spends real money. If a model is reported as skipped, say which API key to export (ANTHROPIC_API_KEY, OPENAI_API_KEY or GEMINI_API_KEY). If a model errors, report the error text; do not guess a replacement model ID.
After the table, give one line: which model passed most, and the cost difference.
