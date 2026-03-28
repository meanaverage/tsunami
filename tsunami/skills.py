"""Skills system — the extensibility mechanism.

Skills are modular capability extensions stored as directories
with a SKILL.md instruction file. They allow the agent to learn
new capabilities without retraining.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("tsunami.skills")


class SkillsManager:
    """Discovers and loads skills from the skills directory."""

    def __init__(self, skills_dir: str | Path):
        self.skills_dir = Path(skills_dir)

    def list_skills(self) -> list[dict]:
        """List all available skills with their descriptions."""
        skills = []
        if not self.skills_dir.exists():
            return skills

        for skill_dir in sorted(self.skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue

            content = skill_md.read_text()
            # Extract first line as title, second as description
            lines = content.strip().splitlines()
            title = lines[0].lstrip("# ").strip() if lines else skill_dir.name
            desc = ""
            for line in lines[1:]:
                line = line.strip()
                if line and not line.startswith("#"):
                    desc = line
                    break

            skills.append({
                "name": skill_dir.name,
                "title": title,
                "description": desc,
                "path": str(skill_dir),
            })

        return skills

    def load_skill(self, name: str) -> str | None:
        """Load a skill's full instructions."""
        skill_dir = self.skills_dir / name
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            return None
        return skill_md.read_text()

    def skills_summary(self) -> str:
        """Generate a summary for injection into the system prompt."""
        skills = self.list_skills()
        if not skills:
            return "No skills installed."

        lines = ["Available skills:"]
        for s in skills:
            lines.append(f"  - {s['name']}: {s['description']}")
        lines.append(f"\nSkills directory: {self.skills_dir}")
        lines.append("Read a skill's SKILL.md before using it.")
        return "\n".join(lines)
