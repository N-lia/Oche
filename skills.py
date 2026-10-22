"""Skill library for the OYI agent.

A skill is a capability the model may reach for. The catalog documents WHEN
to reach for it (trigger conditions, with explicit anti-triggers) and HOW
(the exact output shape). Skills render into the system prompt automatically
via :data:`SKILLS_PROMPT`, so every surface (Telegram, miniapp voice, text)
teaches the same rules from one place.

Only skills the model itself must execute live (formatting/authoring rules)
belong here; behavior enforced in code (HTML sanitizing, TTS stripping) is
documented here but also guarded in the pipeline.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Skill:
    name: str
    purpose: str
    when: tuple[str, ...]
    when_not: tuple[str, ...] = ()
    how: str = ""
    example: str = ""


_DIAGRAM_SKILL = Skill(
    name="diagrams",
    purpose="Draw a live Mermaid diagram inside the reply when structure matters more than prose.",
    when=(
        "the reply explains a pipeline, architecture, flow or state machine the user needs to SEE",
        "the user explicitly asks to draw, map out, visualize or diagram something",
        "a branching decision tree (if/else paths) is the core subject",
        "you reference 3+ components that act on each other in a sequence or graph",
    ),
    when_not=(
        "simple factual or conversational answers (greetings, jokes, facts)",
        "the flow is linear with 2-3 steps — bullet words say it faster",
        "the user asked to keep it short or spoken-only",
    ),
    how=(
        'Encode the diagram as a <pre><code class="language-mermaid">...</code></pre> block '
        "(NOT a ``` fence, NOT an invented <mermaid> tag — the pipeline canonicalizes those but "
        "do not rely on it). Place it AFTER your prose explanation, introduce it with one line "
        "of 'why this diagram', never repeat the diagram's content in words. Keep to core syntax: "
        "flowchart / sequence / state / class. 6-14 named nodes, labels under ~4 words, short edge "
        "labels, no styling directives, no subgraphs unless essential. One diagram per reply; "
        "never more, and never a diagram AND a duplicate yaml/codeblock of the same thing."
    ),
    example=(
        'The turn lifecycle has three phases.\n'
        '<pre><code class="language-mermaid">flowchart LR\n'
        '  A([Voice starts]) --> B{EOU verdict}\n'
        '  B -- real turn --> C([Agent answers])\n'
        '  B -- "mm-hmm" --> D([Reply resumes])</code></pre>'
    ),
)


SKILL_CATALOG: tuple[Skill, ...] = (_DIAGRAM_SKILL,)


def skills_prompt() -> str:
    """Render the catalog as the system-prompt Skill Library section."""
    lines = ["Skill library — reach for a skill only when its WHEN-clause matches:"]
    for skill in SKILL_CATALOG:
        lines.append(f"- SKILL {skill.name}: {skill.purpose}")
        lines.append("  USE WHEN: " + "; ".join(skill.when))
        if skill.when_not:
            lines.append("  DO NOT USE WHEN: " + "; ".join(skill.when_not))
        lines.append("  HOW: " + skill.how)
        if skill.example:
            lines.append("  EXAMPLE: " + skill.example)
    return "\n".join(lines)


SKILLS_PROMPT = skills_prompt()
