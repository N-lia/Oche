"""pi-style file tools, confined to one workspace folder: ls, read, write, edit."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from oche.core import Context

name = "workspace"

PROMPT = """Workspace files: `ls`, `read`, `write`, `edit` (paths are relative to your workspace).
- Only create or change files when the user explicitly asks for a file, note, document, export, script or report.
- Answer ordinary questions, summaries and explanations in chat, never as a file.
- If it is unclear whether a file is wanted, ask one short question first.
- When you write or edit a file, mention its path in your reply."""

MAX_READ = 60_000


def default_root() -> str:
    return os.environ.get(
        "WORKSPACE_DIR", "/data/oyi-memory/workspace" if os.path.isdir("/data/oyi-memory") else "./workspace"
    )


def apply(ctx: Context, config: dict[str, Any]) -> None:
    root = Path(config.get("root") or default_root()).resolve()
    root.mkdir(parents=True, exist_ok=True)

    def resolve(path: str) -> Path:
        target = (root / path.lstrip("/")).resolve()
        if target != root and root not in target.parents:
            raise ValueError(f"{path!r} is outside the workspace")
        return target

    ctx.prompt(PROMPT)

    @ctx.tool(label="Listing files")
    def ls(path: str = ".") -> str:
        """List the files and folders at a workspace path."""
        target = resolve(path)
        if not target.is_dir():
            return f"{path} is not a folder."
        entries = sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir())
        return "\n".join(entries) or "(empty)"

    @ctx.tool(label="Reading a file")
    def read(path: str) -> str:
        """Read a text file from the workspace."""
        text = resolve(path).read_text(encoding="utf-8", errors="replace")
        return text if len(text) <= MAX_READ else text[:MAX_READ] + "\n…[truncated]"

    @ctx.tool(label="Writing a file")
    def write(path: str, content: str) -> str:
        """Create or overwrite a text file in the workspace."""
        target = resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} characters to {path}."

    @ctx.tool(label="Editing a file")
    def edit(path: str, old: str, new: str) -> str:
        """Replace one exact occurrence of `old` with `new` in a workspace file."""
        target = resolve(path)
        text = target.read_text(encoding="utf-8")
        count = text.count(old)
        if count != 1:
            return f"Edit failed: `old` occurs {count} times in {path}; it must occur exactly once."
        target.write_text(text.replace(old, new, 1), encoding="utf-8")
        return f"Edited {path}."
