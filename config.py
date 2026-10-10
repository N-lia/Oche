"""Shared configuration and presentation helpers for OYI."""

from __future__ import annotations

import re


_FENCED_BLOCK_RE = re.compile(r"```(?:[a-zA-Z0-9_+.-]+)?\n?(.*?)```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_RAW_URL_RE = re.compile(r"https?://[^\s)>\]]+")
_REPEATED_NO_FILES_RE = re.compile(r"(?:No files found[.\s]*){2,}", re.IGNORECASE)
_NO_FILES_BLOCK_RE = re.compile(
    r"No files found\.?\s*(.*?)(?=(?:\n\s*)?No files found\b|\Z)",
    re.IGNORECASE | re.DOTALL,
)
_FILE_NOT_FOUND_REASON_RE = re.compile(
    r"((?:I can'?t find|I'?m unable to find|I could not find|Could not find|File .+? not found|"
    r"The directory appears to be empty|No matching files were found)[^.?!]*(?:[.?!]|$))",
    re.IGNORECASE,
)
_FILE_SEARCH_NARRATION_RE = re.compile(
    r"\b(?:Let me search more broadly|I'?ll search more broadly|I will search more broadly|"
    r"Would you like me to help you create.*|Would you like me to search.*)\b.*",
    re.IGNORECASE | re.DOTALL,
)
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
_ORDERED_LIST_RE = re.compile(r"^(\s*)\d+[.)]\s+", re.MULTILINE)
_UNORDERED_LIST_RE = re.compile(r"^(\s*)[-*+]\s+", re.MULTILINE)
_BLOCKQUOTE_RE = re.compile(r"^\s{0,3}>\s?", re.MULTILINE)
_HORIZONTAL_RULE_RE = re.compile(r"^\s{0,3}([-*_])(?:\s*\1){2,}\s*$", re.MULTILINE)
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$", re.MULTILINE)
_BOLD_ITALIC_RE = re.compile(r"(\*\*\*|___)(.*?)\1", re.DOTALL)
_BOLD_RE = re.compile(r"(\*\*|__)(.*?)\1", re.DOTALL)
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)([^*\n]+)(?<!\*)\*(?!\*)|(?<!_)_(?!_)([^_\n]+)(?<!_)_(?!_)", re.DOTALL)
_STRIKETHROUGH_RE = re.compile(r"~~(.*?)~~", re.DOTALL)
_MULTIPLE_BLANK_LINES_RE = re.compile(r"\n{3,}")


def format_agent_response(response: str) -> str:
    """Convert agent Markdown-ish output into cleaner chat text.

    Telegram will render plain text unless parse modes are enabled, so raw
    Markdown markers look noisy. This keeps the message readable without
    deleting useful content such as code, links, and lists.
    """
    text = response.strip()
    if not text:
        return ""

    text = _collapse_repeated_file_errors(text)
    text = _FENCED_BLOCK_RE.sub(_format_code_block, text)
    text = _INLINE_CODE_RE.sub(r"\1", text)
    text = _MARKDOWN_LINK_RE.sub(_format_markdown_link, text)
    text = _RAW_URL_RE.sub(_format_raw_url, text)
    text = _HEADING_RE.sub("", text)
    text = _BLOCKQUOTE_RE.sub("", text)
    text = _HORIZONTAL_RULE_RE.sub("", text)
    text = _TABLE_SEPARATOR_RE.sub("", text)
    text = _ORDERED_LIST_RE.sub(r"\1", text)
    text = _UNORDERED_LIST_RE.sub(r"\1- ", text)
    text = _BOLD_ITALIC_RE.sub(r"\2", text)
    text = _BOLD_RE.sub(r"\2", text)
    text = _ITALIC_RE.sub(lambda match: match.group(1) or match.group(2) or "", text)
    text = _STRIKETHROUGH_RE.sub(r"\1", text)
    text = _clean_table_rows(text)
    text = _normalize_lines(text)
    return text


def _collapse_repeated_file_errors(text: str) -> str:
    if "No files found" not in text:
        return text

    text = _REPEATED_NO_FILES_RE.sub("No files found. ", text)
    blocks = _NO_FILES_BLOCK_RE.findall(text)
    if not blocks:
        return text

    reason = _first_file_not_found_reason(blocks)
    collapsed = f"No files found. {reason}"
    return _NO_FILES_BLOCK_RE.sub(collapsed, text, count=1).split("No files found", 1)[0] + collapsed


def _first_file_not_found_reason(blocks: list[str]) -> str:
    for block in blocks:
        cleaned = _FILE_SEARCH_NARRATION_RE.sub("", block).strip()
        match = _FILE_NOT_FOUND_REASON_RE.search(cleaned)
        if match:
            return match.group(1).strip()
    return "I searched the available workspace paths but did not find a matching file."


def _format_code_block(match: re.Match[str]) -> str:
    code = match.group(1).strip("\n")
    if not code:
        return ""
    return f"\n{code}\n"


def _format_markdown_link(match: re.Match[str]) -> str:
    label = match.group(1).strip()
    url = match.group(2).strip()
    if not label or label == url:
        return url
    return f"{label}: {url}"


def _format_raw_url(match: re.Match[str]) -> str:
    return match.group(0).rstrip(".,;:")


def _clean_table_rows(text: str) -> str:
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2:
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            lines.append(" | ".join(cell for cell in cells if cell))
        else:
            lines.append(line)
    return "\n".join(lines)


def _normalize_lines(text: str) -> str:
    lines = [line.rstrip() for line in text.splitlines()]
    text = "\n".join(lines).strip()
    text = _MULTIPLE_BLANK_LINES_RE.sub("\n\n", text)
    return text
