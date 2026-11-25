---
name: sample-json-extractor
description: Extract name, email and amount from a short invoice or contact note and return them as a strict JSON object. Use when the user pastes text containing a person, an email address and a money amount.
---

# Sample JSON extractor

Read the text and return ONLY a JSON object, with no code fences and no commentary:

```json
{"name": "<string>", "email": "<string>", "amount": <number>}
```

Rules:
- `email` must appear verbatim in the text.
- `amount` is a plain number (no currency symbol, no thousands separator).
- Use exactly these three keys.

Output is checked by `scripts/validate.py`.
