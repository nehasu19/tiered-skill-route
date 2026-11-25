"""Deterministic validator. stdin = model output, $SKILL_INPUT = original text. Prints PASS or FAIL: <reason>."""
import json
import os
import sys


def check(output: str, source: str) -> str | None:
    try:
        obj = json.loads(output)
    except json.JSONDecodeError as e:
        return f"not valid JSON ({e.msg})"
    if not isinstance(obj, dict):
        return "top-level value must be an object"
    if set(obj) != {"name", "email", "amount"}:
        return f"keys must be exactly name, email, amount (got {sorted(obj)})"
    if not isinstance(obj["name"], str) or not obj["name"].strip():
        return "name must be a non-empty string"
    email = obj["email"]
    if not isinstance(email, str) or email.count("@") != 1 or "." not in email.split("@")[1]:
        return "email must look like user@host.tld"
    if isinstance(obj["amount"], bool) or not isinstance(obj["amount"], (int, float)):
        return "amount must be a number"
    if source and email not in source:
        return "email does not appear in the source text (hallucinated)"
    return None


if __name__ == "__main__":
    err = check(sys.stdin.read(), os.environ.get("SKILL_INPUT", ""))
    print("PASS" if err is None else f"FAIL: {err}")
    sys.exit(0 if err is None else 1)
