"""Turn classification for live voice.

The ASR is accurate but conversationally naive: it happily transcribes a
listener's "mm-hmm" or "ok" as a user turn. Handing those to the agent makes it
answer acknowledgements, which is the main reason voice agents feel wrong.

End-of-turn detection therefore ends in a *policy* decision, not in the
transcript alone:

    empty         noise or nothing intelligible -> drop the turn
    continuation  a backchannel/acknowledgement -> do not reply
    turn          an actual request             -> hand it to the agent

The client's VAD decides *when* a turn ended (see ``webapp/turn-taking.js``);
this module decides *what* it was. Keeping it pure makes it testable and easy
to tune.
"""

from __future__ import annotations

# Pure listener noise: never an answer, even to a direct question.
BACKCHANNELS = frozenset(
    {
        "hmm",
        "hm",
        "hmmm",
        "mm",
        "mmm",
        "mhm",
        "mmhmm",
        "mm-hmm",
        "mm hmm",
        "uh-huh",
        "uh huh",
        "uhhuh",
        "right",
        "wow",
        "oh",
        "ah",
        "aha",
        "interesting",
        "go on",
        "go ahead",
        "continue",
    }
)

# Acknowledgements. Usually continuers, but they *can* answer a yes/no question,
# so they only count as continuers when the agent is not waiting for an answer.
ACKNOWLEDGEMENTS = frozenset(
    {
        "ok",
        "okay",
        "okey",
        "yeah",
        "yea",
        "yep",
        "yup",
        "sure",
        "alright",
        "all right",
        "got it",
        "i see",
        "i hear you",
        "cool",
        "nice",
        "exactly",
        "true",
        "indeed",
        "of course",
        "makes sense",
        "fair enough",
    }
)

_WORDS = frozenset(word for phrase in BACKCHANNELS | ACKNOWLEDGEMENTS for word in phrase.split())
_PUNCTUATION = str.maketrans("", "", ".,!?;:\"'`()[]{}…")


def normalize(text: str) -> str:
    """Lowercase, strip punctuation and collapse whitespace."""
    cleaned = (text or "").strip().lower().replace("’", "'").replace("‘", "'")
    cleaned = cleaned.translate(_PUNCTUATION)
    return " ".join(cleaned.split())


def classify_turn(
    text: str,
    *,
    expecting_answer: bool = False,
) -> str:
    """Return ``"empty"``, ``"continuation"`` or ``"turn"``.

    ``expecting_answer`` should be true when the agent's previous reply ended
    with a question, so a bare "ok"/"yeah" is treated as an answer rather than
    as a continuer.
    """
    raw = (text or "").strip()
    norm = normalize(raw)
    if not norm:
        return "empty"

    # A question is always a real turn.
    if "?" in raw:
        return "turn"

    if norm in BACKCHANNELS:
        return "continuation"

    if norm in ACKNOWLEDGEMENTS:
        return "turn" if expecting_answer else "continuation"

    words = norm.split()
    if len(words) <= 4 and all(word in _WORDS for word in words):
        # e.g. "ok ok", "yeah right", "mm hmm"
        if expecting_answer and any(word not in BACKCHANNELS for word in words):
            return "turn"
        return "continuation"

    return "turn"


def is_continuation(text: str, **kwargs: object) -> bool:
    return classify_turn(text, **kwargs) == "continuation"
