"""Skill library for the OYI agent.

Each skill lives as its own Markdown file under ``skill/``. A small
frontmatter block declares the skill's name and purpose; the sections carry the
trigger conditions (USE WHEN / DO NOT USE WHEN), the exact output shape (HOW),
and an example. :func:`load_skills` parses every ``*-skill.md`` file and
:data:`SKILLS_PROMPT` renders the catalog into the system prompt, so every
surface (Telegram, miniapp voice, text) teaches the same rules from one place.

Adding a skill is a file drop: create ``skill/<name>-skill.md`` with the same
structure and it is picked up on the next import. No code change is needed.

Only skills the model itself must execute live (tool selection, authoring
rules) belong here; behavior enforced in code (HTML sanitizing, TTS stripping)
is documented here but also guarded in the pipeline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parent / "skill"

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)
_FIELD_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*)$")
_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.M)
_BULLET_RE = re.compile(r"^\s*[-*]\s+(.*)$")


@dataclass(frozen=True)
class Skill:
    name: str
    purpose: str
    when: tuple[str, ...]
    when_not: tuple[str, ...] = ()
    how: str = ""
    example: str = ""


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split ``---`` frontmatter (name/purpose) from the Markdown body."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        field = _FIELD_RE.match(line.strip())
        if field:
            meta[field.group(1).lower()] = field.group(2).strip()
    return meta, text[match.end():]


def _split_sections(body: str) -> dict[str, str]:
    """Map lowercase ``## Heading`` titles to their trimmed bodies."""
    sections: dict[str, str] = {}
    matches = list(_SECTION_RE.finditer(body))
    for index, match in enumerate(matches):
        title = match.group(1).strip().lower()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        sections[title] = body[start:end].strip()
    return sections


def _bullets(block: str) -> tuple[str, ...]:
    """Extract bullet items, preserving their authored order."""
    return tuple(
        bullet.group(1).strip()
        for bullet in (_BULLET_RE.match(line) for line in block.splitlines())
        if bullet
    )


def _section(sections: dict[str, str], *names: str) -> str:
    for name in names:
        value = sections.get(name)
        if value:
            return value
    return ""


def load_skill(path: Path) -> Skill:
    """Parse one ``*-skill.md`` file into a :class:`Skill`."""
    text = path.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(text)
    sections = _split_sections(body)
    name = meta.get("name") or path.name.removesuffix("-skill.md")
    purpose = meta.get("purpose") or _section(sections, "purpose")
    if not purpose:
        raise ValueError(
            f"Skill {path.name} has no purpose (frontmatter 'purpose:' or '## Purpose')"
        )
    return Skill(
        name=name,
        purpose=purpose,
        when=_bullets(_section(sections, "use when", "when")),
        when_not=_bullets(
            _section(sections, "do not use when", "when not", "when_not")
        ),
        how=_section(sections, "how"),
        example=_section(sections, "example"),
    )


def load_skills(directory: Path | None = None) -> tuple[Skill, ...]:
    """Load every ``*-skill.md`` from ``skill/`` in deterministic order."""
    folder = directory or SKILLS_DIR
    return tuple(load_skill(path) for path in sorted(folder.glob("*-skill.md")))


SKILL_CATALOG: tuple[Skill, ...] = load_skills()


def skills_prompt(catalog: tuple[Skill, ...] = SKILL_CATALOG) -> str:
    """Render the catalog as the system-prompt Skill Library section."""
    lines = ["Skill library — reach for a skill only when its WHEN-clause matches:"]
    for skill in catalog:
        lines.append(f"- SKILL {skill.name}: {skill.purpose}")
        lines.append("  USE WHEN: " + "; ".join(skill.when))
        if skill.when_not:
            lines.append("  DO NOT USE WHEN: " + "; ".join(skill.when_not))
        if skill.how:
            lines.append("  HOW: " + skill.how)
        if skill.example:
            lines.append("  EXAMPLE: " + skill.example)
    return "\n".join(lines)


SKILLS_PROMPT = skills_prompt()
