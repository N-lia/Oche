"""Conversational backchannels and thinking fillers for the live Voice Lab.

Human listeners constantly send tiny signals while someone is talking
("mm-hmm", "right", "go on"), and they stall politely when they need a moment
to think ("hmm...", "let me see..."). This module reproduces both so the agent
feels present instead of mute until the user finishes.

Design:
- :class:`BackchannelController` is pure turn-taking policy. It observes speech
  activity and partial transcripts and decides *whether* and *which* phrase to
  emit. It holds no audio or I/O, so it is fully unit-testable.
- Clip synthesis/caching lives in the second half. Short phrases are rendered
  once with Chatterbox and cached on disk so live playback never waits on a
  Modal round-trip.
"""

from __future__ import annotations

import hashlib
import logging
import os
import random
from dataclasses import dataclass
from pathlib import Path

LOGGER = logging.getLogger("oyi.backchannel")

BACKCHANNEL_MOOD = "backchannel"
# Clips must live in an absolute, writable location: the container may run as
# a non-root user where relative paths like "data/backchannels" (=/app/data)
# are read-only. HOME is a mounted, writable volume in the deployed image.
DEFAULT_CACHE_DIR = os.getenv("BACKCHANNEL_CACHE_DIR") or str(Path.home() / "backchannels")

# Short, natural listener signals. Kept terse so clips stay snappy.
ACK_PHRASES = ["mm-hmm", "uh-huh", "right", "yeah", "okay", "I see", "sure", "mhm", "got it"]
CONTINUE_PHRASES = ["go on", "mm-hmm", "right", "okay", "and then"]
FILLER_PHRASES = ["hmm", "let me see", "well", "okay so", "so"]

CATEGORIES = {
    "ack": ACK_PHRASES,
    "continue": CONTINUE_PHRASES,
    "filler": FILLER_PHRASES,
}


def all_phrases() -> list[str]:
    seen: list[str] = []
    for phrases in CATEGORIES.values():
        for phrase in phrases:
            if phrase not in seen:
                seen.append(phrase)
    return seen


@dataclass(frozen=True)
class BackchannelDecision:
    phrase: str
    category: str
    reason: str


class BackchannelController:
    """Turn-taking policy for live backchannels.

    Fed by the session loop with audio activity (per chunk) and partial
    transcripts. All timing uses a monotonic ``now`` supplied by the caller so
    behaviour is deterministic in tests.
    """

    def __init__(
        self,
        *,
        min_gap: float = 3.5,
        first_after: float = 1.0,
        long_turn: float = 9.0,
        pause: float = 0.30,
        max_per_turn: int = 4,
        rng: random.Random | None = None,
    ) -> None:
        self._min_gap = min_gap
        self._first_after = first_after
        self._long_turn = long_turn
        self._pause = pause
        self._max_per_turn = max_per_turn
        self._rng = rng or random.Random()
        self._now = 0.0
        self._started_at = 0.0
        self._last_at = float("-inf")
        self._count = 0
        self._last_phrase = ""
        self._silence_started_at: float | None = None
        self._pause_ready = False
        self._pending_continue = False
        self._active = False

    def start_turn(self, now: float) -> None:
        """Begin a new user utterance."""
        self._started_at = now
        self._last_at = float("-inf")  # first_after gates the first emission
        self._count = 0
        self._last_phrase = ""
        self._silence_started_at = None
        self._pause_ready = False
        self._pending_continue = False
        self._active = True

    def end_turn(self, now: float) -> None:
        """User stopped speaking; stop considering backchannels."""
        self._active = False
        self._pause_ready = False
        self._pending_continue = False
        self._silence_started_at = None

    def update_activity(self, now: float, is_speech: bool) -> None:
        """Feed the latest speech/silence state (e.g. from RMS or Silero VAD)."""
        if not self._active:
            return
        if is_speech:
            # Speaking again after a quiet stretch: mark a natural pause point.
            if self._pause_ready:
                self._pending_continue = True
            self._silence_started_at = None
            self._pause_ready = False
        else:
            # Silence inside an active turn: after `pause` seconds this becomes a
            # natural place to slip in an acknowledgement.
            if self._silence_started_at is None:
                self._silence_started_at = now
            elif now - self._silence_started_at >= self._pause:
                self._pause_ready = True

    @property
    def spoken_for(self) -> float:
        return max(0.0, self._now - self._started_at)

    def tick(
        self,
        now: float,
        *,
        transcript: str = "",
        turn_active: bool = True,
        bot_speaking: bool = False,
    ) -> BackchannelDecision | None:
        """Return a phrase to speak now, or ``None`` to stay quiet."""
        self._now = now
        if not self._active or not turn_active or bot_speaking:
            return None
        if self._count >= self._max_per_turn:
            return None
        if now - self._last_at < self._min_gap:
            return None
        if now - self._started_at < self._first_after:
            return None
        # Only fill natural pause points, or nudge a long monologue to continue.
        long_turn = (now - self._started_at) >= self._long_turn
        if not self._pause_ready and not long_turn:
            return None

        category = "continue" if (long_turn or self._pending_continue) else "ack"
        phrase = self._choose(category)
        if phrase is None:
            return None

        self._last_at = now
        self._count += 1
        self._last_phrase = phrase
        self._pause_ready = False
        self._pending_continue = False
        reason = "long turn" if long_turn else ("after pause" if category == "continue" else "pause acknowledgement")
        return BackchannelDecision(phrase=phrase, category=category, reason=reason)

    def thinking_filler(self, now: float) -> str | None:
        """A short stall to cover assistant latency right after the user stops."""
        return self._choose("filler")

    def _choose(self, category: str) -> str | None:
        options = CATEGORIES.get(category) or []
        choices = [p for p in options if p != self._last_phrase] or options
        return self._rng.choice(choices) if choices else None


# ---------------------------------------------------------------------------
# Clip synthesis and caching
# ---------------------------------------------------------------------------


def _cache_dir() -> Path:
    """Resolve the clip cache dir, falling back to HOME when not writable."""
    path = Path(os.getenv("BACKCHANNEL_CACHE_DIR", DEFAULT_CACHE_DIR))
    try:
        path.mkdir(parents=True, exist_ok=True)
        Path.touch(path / ".probe")
    except OSError as exc:  # noqa: PERF203 - readability
        fallback = Path.home() / "backchannels"
        LOGGER.warning("Backchannel cache %s is not writable (%s); using %s", path, exc, fallback)
        path = fallback
        path.mkdir(parents=True, exist_ok=True)
    return path


def _voice_signature() -> str:
    """Identity of the voice that owns the clip cache.

    Clips are audio, not text: a provider or voice swap must never keep serving
    the old timbre. This is what retired the cloned-voice "hmm" clips when the
    WebApp moved to Phonon's built-in default voice.
    """
    provider = (os.getenv("TTS_PROVIDER") or "chatterbox").strip().lower()
    if provider == "phonon":
        voice = Path(os.getenv("PHONON_MODEL_DIR", "/opt/phonon")) / "default-voice.safetensors"
        try:
            stat = voice.stat()
        except OSError:
            return "phonon:default"
        return f"phonon:default:{stat.st_size}:{int(stat.st_mtime)}"
    if provider == "cosyvoice":
        return "cosyvoice"
    # Chatterbox backchannels use its built-in voice (see modal_chatterbox.py),
    # never the cloned reference used for full replies.
    return "chatterbox:default"


def clip_key(phrase: str, mood: str = BACKCHANNEL_MOOD) -> str:
    voice = _voice_signature()
    digest = hashlib.sha1(f"{voice}\x00{mood}\x00{phrase}".encode("utf-8")).hexdigest()[:16]
    return digest


def clip_path(phrase: str, mood: str = BACKCHANNEL_MOOD) -> Path:
    return _cache_dir() / f"{clip_key(phrase, mood)}.mp3"


def cached_clips() -> dict[str, Path]:
    """Map phrase -> cached clip path for every phrase already synthesized."""
    result: dict[str, Path] = {}
    for phrase in all_phrases():
        path = clip_path(phrase)
        if path.exists() and path.stat().st_size > 0:
            result[phrase] = path
    return result


async def ensure_clip(phrase: str, mood: str = BACKCHANNEL_MOOD) -> bytes:
    """Return clip bytes for ``phrase``, synthesizing and caching on first use."""
    path = clip_path(phrase, mood)
    if path.exists() and path.stat().st_size > 0:
        return path.read_bytes()

    from voice import synthesize_speech

    audio = await synthesize_speech(phrase, mood=mood, event="chat")
    if not audio:
        raise RuntimeError(f"Empty audio returned for backchannel clip {phrase!r}")
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(audio)
    tmp.replace(path)
    LOGGER.info("Cached backchannel clip %r (%d bytes) at %s", phrase, len(audio), path)
    return audio


async def prewarm(phrases: list[str] | None = None) -> dict[str, str]:
    """Synthesize any missing clips. Returns phrase -> "cached"|"ready"|error."""
    status: dict[str, str] = {}
    for phrase in phrases or all_phrases():
        try:
            await ensure_clip(phrase)
            status[phrase] = "cached" if clip_path(phrase).exists() else "ready"
        except Exception as exc:  # noqa: BLE001 - reported per phrase
            LOGGER.exception("Failed to synthesize backchannel clip %r", phrase)
            status[phrase] = f"error: {exc}"
    return status
