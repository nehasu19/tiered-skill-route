"""Load and validate portable SKILL.md packages."""
from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class SkillError(ValueError):
    """Raised when a skill package is malformed."""


class Skill(BaseModel):
    name: str
    description: str
    body: str = Field(description="Markdown instructions after the frontmatter.")
    root: Path
    validate_script: Path | None = None
    references: list[Path] = Field(default_factory=list)


_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", re.DOTALL)


def load_skill(skill_dir: str | Path) -> Skill:
    """Load ``<skill_dir>/SKILL.md``; optional ``scripts/validate.py`` and ``references/*``."""
    root = Path(skill_dir)
    md = root / "SKILL.md"
    if not md.is_file():
        raise SkillError(f"{root}: SKILL.md not found")
    m = _FRONTMATTER_RE.match(md.read_text(encoding="utf-8"))
    if not m:
        raise SkillError(f"{md}: missing YAML frontmatter between '---' lines")
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        raise SkillError(f"{md}: invalid frontmatter YAML: {e}") from e
    if not isinstance(meta, dict):
        raise SkillError(f"{md}: frontmatter must be a mapping")

    name, desc = meta.get("name"), meta.get("description")
    if not isinstance(name, str) or not _NAME_RE.match(name) or len(name) > 64:
        raise SkillError(f"{md}: 'name' must be lowercase-hyphen, <= 64 chars")
    if name != root.name:
        raise SkillError(f"{md}: name '{name}' must match directory '{root.name}'")
    if not isinstance(desc, str) or not desc.strip() or len(desc) > 1024:
        raise SkillError(f"{md}: 'description' must be a non-empty string <= 1024 chars")

    script = root / "scripts" / "validate.py"
    refs_dir = root / "references"
    return Skill(
        name=name,
        description=desc.strip(),
        body=m.group(2).strip(),
        root=root,
        validate_script=script if script.is_file() else None,
        references=sorted(p for p in refs_dir.rglob("*") if p.is_file()) if refs_dir.is_dir() else [],
    )


def load_skills(skills_dir: str | Path) -> dict[str, Skill]:
    """Load every skill directory under ``skills_dir``, keyed by name."""
    return {
        s.name: s
        for s in (load_skill(p) for p in sorted(Path(skills_dir).iterdir()) if (p / "SKILL.md").exists())
    }
