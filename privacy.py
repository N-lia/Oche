"""Local PII and project-secret protection for external model/API calls."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field

_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+\b", re.IGNORECASE)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"\b(?:api[_-]?key|token|secret|password|passwd|authorization)\s*[:=]\s*[^\s,;]+",
    re.IGNORECASE,
)
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d(). -]{7,}\d)(?!\w)")
_IP_RE = re.compile(r"(?<!\w)(?:\d{1,3}\.){3}\d{1,3}(?!\w)")


def _project_names() -> list[str]:
    raw = os.getenv("OYI_PII_PROJECT_NAMES", "")
    return sorted((item.strip() for item in raw.split(",") if item.strip()), key=len, reverse=True)


@dataclass
class PrivacyVault:
    """Per-request reversible aliases; never persist this object or send it out."""

    aliases: dict[str, str] = field(default_factory=dict)
    _reverse: dict[str, str] = field(default_factory=dict)

    def _alias(self, value: str, category: str) -> str:
        existing = self.aliases.get(value)
        if existing:
            return existing
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
        alias = f"<{category}_{digest}>"
        self.aliases[value] = alias
        self._reverse[alias] = value
        return alias

    def _replace(self, text: str, pattern: re.Pattern[str], category: str) -> str:
        return pattern.sub(lambda match: self._alias(match.group(0), category), text)

    def scrub(self, text: str) -> str:
        """Replace credentials, project names, and common direct identifiers."""
        scrubbed = text
        for project_name in _project_names():
            scrubbed = scrubbed.replace(project_name, self._alias(project_name, "PROJECT"))
        scrubbed = self._replace(scrubbed, _SECRET_ASSIGNMENT_RE, "SECRET")
        scrubbed = self._replace(scrubbed, _BEARER_RE, "SECRET")
        scrubbed = self._replace(scrubbed, _EMAIL_RE, "EMAIL")
        scrubbed = self._replace(scrubbed, _PHONE_RE, "PHONE")
        return self._replace(scrubbed, _IP_RE, "IP")

    def restore(self, text: str) -> str:
        """Restore aliases for a local delivery target such as an email composer."""
        restored = text
        for alias, value in sorted(self._reverse.items(), key=lambda item: len(item[0]), reverse=True):
            restored = restored.replace(alias, value)
        return restored


def scrub_for_external(text: str, vault: PrivacyVault | None = None) -> tuple[str, PrivacyVault]:
    """Scrub text and return the local vault required for later restoration."""
    vault = vault or PrivacyVault()
    return vault.scrub(text), vault


def restore_for_local_delivery(text: str, vault: PrivacyVault) -> str:
    """Restore PII only immediately before a trusted local delivery operation."""
    return vault.restore(text)


def prepare_mail(subject: str, body: str, vault: PrivacyVault) -> tuple[str, str]:
    """Restore a scrubbed subject/body for a caller that owns the mail transport.

    This function does not send mail. Call it only after a recipient and trusted
    local mail transport have been selected.
    """
    return vault.restore(subject), vault.restore(body)