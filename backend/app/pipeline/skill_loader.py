from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from app.core.config import settings


@dataclass
class SkillUnit:
    name: str
    version: str
    description: str
    applies_to: List[str]
    schema_version: str
    body: str
    path: str


class SkillLoader:
    """Loads versioned Agent Skills (SKILL.md) and composes them by query intent/level."""

    def __init__(self, skills_dir: Path | None = None) -> None:
        self.skills_dir = skills_dir or settings.resolve_path(settings.skills_dir)
        self._skills: Dict[str, SkillUnit] = {}
        self.reload()

    def reload(self) -> None:
        self._skills.clear()
        if not self.skills_dir.exists():
            return
        for skill_file in sorted(self.skills_dir.glob("*/SKILL.md")):
            unit = self._parse_skill(skill_file)
            if unit:
                self._skills[unit.name] = unit

    def _parse_skill(self, path: Path) -> SkillUnit | None:
        raw = path.read_text(encoding="utf-8")
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", raw, re.DOTALL)
        if not m:
            return None
        fm_raw, body = m.group(1), m.group(2).strip()
        meta: Dict[str, str] = {}
        applies: List[str] = []
        for line in fm_raw.splitlines():
            line = line.strip()
            if not line or ":" not in line:
                continue
            k, v = line.split(":", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k == "applies_to":
                applies = [
                    item.strip().strip('"').strip("'")
                    for item in v.strip("[]").split(",")
                    if item.strip()
                ]
            else:
                meta[k] = v
        name = meta.get("name") or path.parent.name
        return SkillUnit(
            name=name,
            version=meta.get("version", "1.0.0"),
            description=meta.get("description", ""),
            applies_to=applies or ["all"],
            schema_version=meta.get("schema_version", ""),
            body=body,
            path=str(path),
        )

    def list_skills(self) -> List[SkillUnit]:
        return list(self._skills.values())

    def get_versions(self) -> Dict[str, str]:
        return {s.name: s.version for s in self._skills.values()}

    def select_skills(self, level: str, category: str) -> List[SkillUnit]:
        selected: List[SkillUnit] = []
        if "code-graph-schema" in self._skills:
            selected.append(self._skills["code-graph-schema"])

        tags = {level.lower(), category.lower()}
        for name, unit in self._skills.items():
            if name == "code-graph-schema":
                continue
            if any(t in unit.applies_to for t in tags):
                selected.append(unit)
        return selected

    def compose_skill_prompt(self, skills: List[SkillUnit]) -> str:
        sections = []
        for s in skills:
            sections.append(f"## [Skill: {s.name}@{s.version}]\n{s.body}")
        return "\n\n".join(sections)


skill_loader = SkillLoader()
