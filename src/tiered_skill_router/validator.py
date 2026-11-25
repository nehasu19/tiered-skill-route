"""Deterministic output validation. Never relies on model self-grading."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass

from .skill_loader import Skill

SCRIPT_TIMEOUT_S = 10
_TYPES = {"str": str, "int": int, "float": (int, float), "bool": bool, "list": list, "dict": dict}


@dataclass(frozen=True)
class ValidationResult:
    passed: bool
    reason: str = ""

    def __str__(self) -> str:
        return "PASS" if self.passed else f"FAIL: {self.reason}"


PASS = ValidationResult(True)


def fail(reason: str) -> ValidationResult:
    return ValidationResult(False, reason)


def validate_json_schema(text: str, schema: dict[str, str]) -> ValidationResult:
    """Output must be a JSON object with exactly the keys in ``schema`` ({key: "str"|"int"|...})."""
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as e:
        return fail(f"not valid JSON ({e.msg})")
    if not isinstance(obj, dict):
        return fail("top-level JSON value must be an object")
    if missing := sorted(set(schema) - set(obj)):
        return fail(f"missing keys: {', '.join(missing)}")
    if extra := sorted(set(obj) - set(schema)):
        return fail(f"unexpected keys: {', '.join(extra)}")
    for key, tname in schema.items():
        expected = _TYPES[tname]
        v = obj[key]
        # bool is an int subclass in Python; don't let True pass as a number
        if not isinstance(v, expected) or (isinstance(v, bool) and tname != "bool"):
            return fail(f"key '{key}' must be {tname}")
    return PASS


def run_skill_script(skill: Skill, output: str, task_input: str = "") -> ValidationResult:
    """Run ``scripts/validate.py``: output on stdin, input in $SKILL_INPUT, prints PASS or FAIL: <reason>."""
    # Minimal env: the script never sees provider API keys.
    env = {"PATH": os.environ.get("PATH", ""), "SKILL_INPUT": task_input}
    try:
        p = subprocess.run(
            [sys.executable, "-I", str(skill.validate_script)],
            input=output, capture_output=True, text=True, timeout=SCRIPT_TIMEOUT_S, env=env,
        )
    except subprocess.TimeoutExpired:
        return fail(f"validate.py timed out after {SCRIPT_TIMEOUT_S}s")
    lines = p.stdout.strip().splitlines()
    first = lines[0].strip() if lines else ""
    if p.returncode == 0 and first == "PASS":
        return PASS
    if first.startswith("FAIL:"):
        return fail(first[5:].strip() or "validator reported failure")
    return fail(f"validate.py error (exit {p.returncode}): {(p.stderr.strip() or first or 'no output')[:200]}")


def validate(output: str, skill: Skill | None = None, task_input: str = "",
             schema: dict[str, str] | None = None) -> ValidationResult:
    """Generic checks, then the optional schema, then the skill's bundled script."""
    if not output or not output.strip():
        return fail("empty output")
    if schema and not (r := validate_json_schema(output, schema)).passed:
        return r
    if skill is not None and skill.validate_script is not None:
        return run_skill_script(skill, output, task_input)
    return PASS
