#!/usr/bin/env python3
"""Tests for the agent Skill library (skill/*.md + skills.py).

Run:
    .venv/bin/python test_skills.py
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("NEBIUS_API_KEY", "test-key")

from skills import SKILL_CATALOG, SKILLS_DIR, SKILLS_PROMPT, load_skills


def test_skills_loaded_from_folder() -> None:
    # Every skill is an individual Markdown file in skill/.
    files = sorted(path.name for path in SKILLS_DIR.glob("*-skill.md"))
    assert files, f"no *-skill.md files found in {SKILLS_DIR}"
    assert len(files) == len(SKILL_CATALOG), (files, [s.name for s in SKILL_CATALOG])
    assert len(load_skills()) == len(SKILL_CATALOG)


def test_expected_skills_present() -> None:
    names = {skill.name for skill in SKILL_CATALOG}
    for expected in (
        "diagrams",
        "tavily",
        "memory",
        "subagents",
        "voice-notes",
        "file-writing",
    ):
        assert expected in names, f"missing skill: {expected} (have {names})"


def test_diagram_skill_documented() -> None:
    assert any(s.name == "diagrams" for s in SKILL_CATALOG)


def test_prompt_has_when_and_how() -> None:
    text = SKILLS_PROMPT
    assert "SKILL diagrams" in text
    assert "USE WHEN" in text and "DO NOT USE WHEN" in text
    assert "language-mermaid" in text, "the HOW section must carry the exact format"
    # The HOW explicitly forbids the two alias forms the model tends to use.
    assert "NOT a ``` fence" in text and "NOT an invented <mermaid> tag" in text


def test_tavily_skill_is_served() -> None:
    text = SKILLS_PROMPT
    assert "SKILL tavily" in text
    assert "tavily_search" in text
    assert "tavily_research" in text
    assert "USE WHEN" in text


def test_agent_prompt_embeds_skill_library() -> None:
    import agent_service

    assert "SKILL diagrams" in agent_service.SYSTEM_PROMPT
    assert "Skill library" in agent_service.SYSTEM_PROMPT


def main() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"\033[92m✓ {test.__name__}\033[0m")
        except AssertionError as exc:
            failures += 1
            print(f"\033[91m✗ {test.__name__}: {exc}\033[0m")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"\033[91m✗ {test.__name__}: {type(exc).__name__}: {exc}\033[0m")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
