"""Live voice session orchestration: audio -> ASR -> backchannels -> agent -> TTS.

Transport-agnostic (no web-framework imports) so the turn-taking and
interruption logic can be unit-tested directly. ``webapp_server.py`` wires the
defaults to the real ASR client, agent and Chatterbox TTS.

Interruption model
------------------
Every user turn gets a monotonically increasing ``turn_id`` and carries it on
outgoing messages. When the user talks over the assistant, the client sends a
``barge_in`` control frame; the session cancels the in-flight reply task, tears
down the previous ASR stream and opens a new turn seeded with a short preroll of
audio the client buffered, so the first word is not clipped. Stragglers from the
abandoned turn are dropped by comparing ``turn_id``.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable

import numpy as np

from asr_client import AsyncTranscribeStream, ascore_turn
from backchannel import BackchannelController, clip_path
from phonon_tts import plan_chunks, stream_phonon_wav
from telegram_format import normalize_agent_html, voice_text_for_speech
from turn_policy import classify_turn

LOGGER = logging.getLogger("oyi.voice.session")

SPEECH_RMS_THRESHOLD = 0.012

# Voice session states.
SESSION_IDLE = "idle"            # waiting for the user to start a turn
SESSION_LISTENING = "listening"  # transcribing the current user turn
SESSION_EVALUATING = "evaluating"  # silence heard; Smart Turn is scoring it
SESSION_PAUSED = "paused"        # semantically incomplete; waiting for more
SESSION_FINALIZING = "finalizing"  # flush sent; waiting for <EOU>
SESSION_RESPONDING = "responding"  # agent/TTS running for the last turn

# How much trailing audio to keep for Smart Turn (it scores the last 8 s).
_TAIL_SECONDS = 12
_TURN_AUDIO_MAX_BYTES = 16000 * 2 * _TAIL_SECONDS

AgentStreamer = Callable[[str, int], AsyncIterator[str]]
EventStreamer = Callable[[str, int], AsyncIterator[dict]]
Tts = Callable[[str, str], Awaitable[bytes]]
StreamFactory = Callable[[int], AsyncTranscribeStream]
# Returns P(complete) in [0, 1], or None when Smart Turn is unavailable.
TurnScorer = Callable[[bytes, int], Awaitable[float | None]]


def audio_rms(pcm16: bytes) -> float:
    """Root-mean-square level of little-endian 16-bit mono PCM, in [0, 1]."""
    if not pcm16:
        return 0.0
    samples = np.frombuffer(pcm16, dtype="<i2").astype(np.float32) / 32768.0
    if not len(samples):
        return 0.0
    return float(np.sqrt(np.mean(samples * samples)))


async def _default_agent_streamer(text: str, turn_id: int) -> AsyncIterator[str]:
    from main import astream_chat_agent

    async for chunk in astream_chat_agent(text, thread_id="webapp", voice=True):
        yield chunk


async def _default_tts(text: str, mood: str) -> bytes:
    from voice import synthesize_speech

    return await synthesize_speech(text, mood=mood, event="chat")


def _audio_mime(payload: bytes) -> str:
    """Mime of a TTS payload: Phonon produces WAV, Modal providers produce MP3."""
    from phonon_tts import is_wav

    return "audio/wav" if is_wav(payload) else "audio/mpeg"


def _tts_provider() -> str:
    """Which TTS backend is active (mirrors voice.get_tts_provider).

    Kept local so the turn-state unit tests never import the heavyweight
    voice module; the value must stay in sync with it by construction.
    """
    return (os.environ.get("TTS_PROVIDER") or "chatterbox").strip().lower()


def _default_stream_factory(sample_rate: int) -> AsyncTranscribeStream:
    loop = asyncio.get_running_loop()
    return AsyncTranscribeStream(loop, sample_rate=sample_rate).start()


def _default_clip_exists(phrase: str) -> bool:
    return clip_path(phrase).exists()


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _smart_turn_enabled() -> bool:
    return os.getenv("SMART_TURN_ENABLED", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


async def _default_turn_scorer(pcm: bytes, sample_rate: int) -> float | None:
    """Score end-of-turn over the VM's gRPC service; None means "unavailable"."""
    if not _smart_turn_enabled():
        return None
    result = await ascore_turn(pcm, sample_rate=sample_rate)
    if result.error:
        LOGGER.info("Smart Turn unavailable: %s", result.error)
        return None
    return result.probability


class VoiceSession:
    """Orchestrates one live conversation over a message queue."""

    def __init__(
        self,
        out: "asyncio.Queue[dict | None]",
        *,
        agent_streamer: AgentStreamer = _default_agent_streamer,
        event_streamer: EventStreamer | None = None,
        tts: Tts = _default_tts,
        stream_factory: StreamFactory = _default_stream_factory,
        clip_exists: Callable[[str], bool] = _default_clip_exists,
        clip_url: Callable[[str], str] | None = None,
        turn_scorer: TurnScorer | None = None,
        smart_turn_available: bool = False,
    ) -> None:
        self.out = out
        self.controller = BackchannelController()
        self.sample_rate = 16000
        self.mode = "agent"
        self.mood = "normal"
        self.thread_id = "webapp"
        self.speak_text_replies = True
        self.state = SESSION_IDLE
        self.text = ""
        # Monotonic turn counter. Every message carries the id of the turn it
        # belongs to so a new turn can safely ignore stragglers from an
        # interrupted one.
        self.turn_counter = 0
        self.turn_id = 0
        self.stream: AsyncTranscribeStream | None = None
        self.consumer: asyncio.Task | None = None
        self.response_task: asyncio.Task | None = None
        self.bot_speaking = False
        # Turn-taking (see turn_policy.py): duck a reply on barge-in, then let
        # the transcript decide whether it was a continuer or a real turn.
        self.soft_barge = False
        self.reply_turn_id = 0
        self.expecting_answer = False
        self.pending_frames: list[dict] | None = None
        # Semantic endpointing: the client VAD reports silence, Smart Turn
        # decides whether the thought is finished or deserves more time.
        self.smart_turn_available = smart_turn_available
        self.smart_turn_threshold = _env_float("SMART_TURN_THRESHOLD", 0.5)
        self.smart_turn_grace = _env_float("SMART_TURN_GRACE_MS", 1800.0) / 1000.0
        self._turn_audio = bytearray()
        self._grace_task: asyncio.Task | None = None

        self._agent_streamer = agent_streamer
        self._event_streamer = event_streamer or self._default_event_streamer
        self._tts = tts
        self._stream_factory = stream_factory
        self._clip_exists = clip_exists
        self._clip_url = clip_url or (lambda phrase: "")
        self._turn_scorer = turn_scorer or _default_turn_scorer

    async def send(self, message: dict) -> None:
        await self.out.put(message)

    # ------------------------------------------------------------- control
    async def on_control(self, data: dict) -> None:
        kind = data.get("type")
        now = time.monotonic()
        if kind == "start":
            self.sample_rate = int(data.get("sample_rate") or 16000)
            self.mode = data.get("mode", "agent")
            self.mood = data.get("mood", "normal")
            self.thread_id = str(data.get("thread_id") or self.thread_id)
            self.speak_text_replies = bool(data.get("speak_text_replies", self.speak_text_replies))
            await self.send(
                {
                    "type": "ready",
                    "sample_rate": self.sample_rate,
                    "thread_id": self.thread_id,
                    "mode": self.mode,
                    "mood": self.mood,
                    "smart_turn": self.smart_turn_available,
                }
            )
        elif kind == "set":
            # Live reconfiguration from the client (no turn restart).
            if data.get("mode"):
                self.mode = data["mode"]
            if data.get("mood"):
                self.mood = data["mood"]
            if "speak_text_replies" in data:
                self.speak_text_replies = bool(data["speak_text_replies"])
            if data.get("thread_id"):
                self.thread_id = str(data["thread_id"])
        elif kind in ("speech_start", "vad_start"):
            # Client VAD says speech began. It also carries the preroll, so the
            # first syllable is not lost even though audio outside a turn is
            # ignored below.
            if self.state == SESSION_PAUSED and self.stream is not None:
                # Smart Turn said "not finished" and the user kept going: this
                # is the same turn, not a new one. Keep the ASR stream alive.
                self._cancel_grace()
                self.state = SESSION_LISTENING
                seed = self._decode_preroll(data.get("preroll"))
                if seed:
                    await self._feed(seed, now)
                await self.send({"type": "turn_resumed", "turn_id": self.turn_id})
            elif self.state == SESSION_IDLE and self.stream is None:
                await self._begin_turn(now, seed_pcm=self._decode_preroll(data.get("preroll")))
            elif self.state == SESSION_RESPONDING and self._reply_active():
                # The user started talking while the reply was still being
                # generated: open a soft-barge turn so the transcript decides.
                await self._interrupt(now, data.get("preroll", ""))
        elif kind in ("speech_end", "flush"):
            # Client VAD says the user stopped. Smart Turn decides whether to
            # dispatch the turn now or hold it open for a beat.
            if self.state == SESSION_LISTENING and self.stream is not None:
                await self._end_speech(now)
        elif kind == "text":
            # Typed message from the WebApp: run a text turn through the agent.
            text = (data.get("text") or "").strip()
            if text:
                speak = bool(data.get("speak", self.speak_text_replies))
                await self._start_text_turn(text, speak)
        elif kind == "barge_in":
            # User started talking over the bot: drop the pending reply and
            # begin a fresh turn immediately, seeded with the client's preroll.
            await self._interrupt(now, data.get("preroll", ""))
        elif kind == "playback_start":
            # The client reports actual audio playback, so turn-taking does not
            # have to guess whether the agent is speaking.
            self.bot_speaking = True
        elif kind == "playback_end":
            self.bot_speaking = False
            # Audio just finished. If the reply is already generated, this is
            # what closes the turn (a reply that outlives its generation must
            # not leave the session stuck in RESPONDING).
            if self.response_task is None and self.state in (SESSION_IDLE, SESSION_RESPONDING):
                self.state = SESSION_IDLE
                await self.send({"type": "turn_end", "turn_id": self.turn_id})

    # ------------------------------------------------------------- audio in
    @staticmethod
    def _decode_preroll(data: str) -> bytes:
        if not data:
            return b""
        try:
            return base64.b64decode(data)
        except (ValueError, TypeError):
            return b""

    async def on_audio(self, pcm: bytes) -> None:
        if not pcm:
            return
        # Turn boundaries belong to the client VAD. Audio that arrives outside
        # a turn is pre-speech, noise or speaker echo, and must not open one.
        if self.state == SESSION_LISTENING:
            await self._feed(pcm, time.monotonic())
        elif self.state == SESSION_PAUSED:
            # A few frames can still be in flight while Smart Turn evaluates.
            # Keep them with the same turn so a resumed thought is contiguous.
            self._remember_audio(pcm)
            if self.stream is not None:
                self.stream.feed(pcm)
        # IDLE / EVALUATING / FINALIZING / RESPONDING: ignored until the VAD
        # opens or resumes a turn.

    async def _begin_turn(self, now: float, seed_pcm: bytes = b"") -> None:
        # Never orphan a previous stream: each open gRPC stream pins a worker
        # thread inside the ASR server until it is terminated.
        await self._stop_listening()
        self.turn_counter += 1
        self.turn_id = self.turn_counter
        self.state = SESSION_LISTENING
        self.text = ""
        self._turn_audio = bytearray()
        self.controller.start_turn(now)
        self.stream = self._stream_factory(self.sample_rate)
        self.consumer = asyncio.create_task(self._consume(self.stream, self.turn_id))
        if seed_pcm:
            await self._feed(seed_pcm, now)

    def _remember_audio(self, pcm: bytes) -> None:
        self._turn_audio.extend(pcm)
        excess = len(self._turn_audio) - _TURN_AUDIO_MAX_BYTES
        if excess > 0:
            del self._turn_audio[:excess]

    async def _feed(self, pcm: bytes, now: float) -> None:
        self._remember_audio(pcm)
        is_speech = audio_rms(pcm) >= SPEECH_RMS_THRESHOLD
        self.controller.update_activity(now, is_speech)
        if self.stream is not None:
            self.stream.feed(pcm)

        decision = self.controller.tick(
            now, transcript=self.text, turn_active=True, bot_speaking=self.bot_speaking
        )
        if decision and self._clip_exists(decision.phrase):
            LOGGER.info("Backchannel %r (%s)", decision.phrase, decision.reason)
            await self.send(
                {
                    "type": "backchannel",
                    "phrase": decision.phrase,
                    "category": decision.category,
                    "reason": decision.reason,
                    "url": self._clip_url(decision.phrase),
                }
            )
        elif decision:
            asyncio.create_task(self._warm_clip(decision.phrase))

    async def _warm_clip(self, phrase: str) -> None:
        try:
            from backchannel import ensure_clip

            await ensure_clip(phrase)
        except Exception:
            LOGGER.exception("Backchannel clip warm-up failed for %r", phrase)

    # --------------------------------------------------------- structured text
    async def _default_event_streamer(self, text: str, turn_id: int) -> AsyncIterator[dict]:
        """Structured agent stream (status/tool/token events) for text turns."""
        from agent_service import agent_service

        streamer = getattr(agent_service, "astream_events", None)
        if streamer is not None:
            async for event in streamer(text, thread_id=self.thread_id):
                yield event
            return
        # Fallback: adapt the plain token stream into token events.
        from main import astream_chat_agent

        async for chunk in astream_chat_agent(text, thread_id=self.thread_id):
            if chunk:
                yield {"type": "token", "text": chunk}
        yield {"type": "done"}

    async def _start_text_turn(self, text: str, speak: bool) -> None:
        """Typed turn: supersede any pending reply and stream the agent answer."""
        now = time.monotonic()
        await self._cancel_response()
        # A text turn supersedes any in-flight voice turn too.
        await self._stop_listening()

        self.turn_counter += 1
        self.turn_id = self.turn_counter
        self.state = SESSION_RESPONDING
        self.reply_turn_id = self.turn_id
        self.expecting_answer = False
        self.controller.end_turn(now)
        await self.send({"type": "user_text", "text": text, "turn_id": self.turn_id})
        self.response_task = asyncio.create_task(self._respond_text(text, self.turn_id, speak))

    async def _respond_text(self, text: str, turn_id: int, speak: bool) -> None:
        try:
            chunks: list[str] = []
            async for event in self._event_streamer(text, turn_id):
                if turn_id != self.turn_id:
                    return  # superseded by a newer turn
                etype = event.get("type")
                if etype == "token" and event.get("text"):
                    chunks.append(event["text"])
                    await self.send(
                        {"type": "bot_text_delta", "text": event["text"], "turn_id": turn_id}
                    )
                elif etype in ("status", "tool"):
                    await self.send({**event, "turn_id": turn_id})
                elif etype == "done":
                    break
            if turn_id != self.turn_id:
                return
            reply = "".join(chunks).strip() or "I could not produce a response."
            try:
                final_html = normalize_agent_html(reply)
            except Exception:
                LOGGER.exception("normalize_agent_html failed on reply; sending raw", )
                final_html = reply
            await self.send(
                {"type": "bot_text_final", "text": final_html, "turn_id": turn_id}
            )
            self.expecting_answer = reply.rstrip().endswith("?")
            if speak and self.mode != "echo":
                await self._speak(reply, turn_id)
            if turn_id == self.turn_id:
                self.state = SESSION_IDLE
                await self.send({"type": "turn_end", "turn_id": turn_id})
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - surfaced to the client
            LOGGER.exception("WebApp text turn failed")
            if turn_id == self.turn_id:
                await self.send({"type": "error", "message": f"Agent error: {exc}"})
                self.state = SESSION_IDLE
                await self.send({"type": "turn_end", "turn_id": turn_id})
        finally:
            if self.response_task is asyncio.current_task():
                self.response_task = None

    # ------------------------------------------------------------- ASR out
    async def _consume(self, stream: AsyncTranscribeStream, turn_id: int) -> None:
        try:
            async for event in stream:
                if turn_id != self.turn_id:
                    return  # Superseded by a barge-in; drop stragglers.
                if event["type"] == "partial":
                    self.text = event["text"]
                    await self.send(
                        {
                            "type": "partial",
                            "text": event["text"],
                            "delta": event["delta"],
                            "turn_id": turn_id,
                        }
                    )
                elif event["type"] == "eou":
                    await self._finish_turn(event, turn_id)
                    return
                elif event["type"] == "error":
                    await self.send({"type": "error", "message": event["message"]})
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Voice session consumer failed")
        finally:
            # Always terminate the RPC, whatever ended the turn (EOU, barge-in,
            # client disconnect). A stream left open keeps a server worker busy
            # forever, and two of them take the whole ASR service down.
            stream.close()
            if self.stream is stream:
                self.stream = None

    async def _stop_listening(self) -> None:
        """Close the current ASR stream and stop its consumer task."""
        self._cancel_grace()
        stream, consumer = self.stream, self.consumer
        self.stream = None
        self.consumer = None
        if stream is not None:
            stream.close()
        if consumer is not None and not consumer.done():
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)

    # ------------------------------------------------------ semantic endpoint
    async def _end_speech(self, now: float) -> None:
        """Silence heard: dispatch a finished thought or hold an unfinished one.

        The client VAD can only hear pauses. Smart Turn looks at the waveform
        and answers "did they finish?"; low confidence buys the speaker a
        short grace window in which the same turn can resume.
        """
        if self.state != SESSION_LISTENING or self.stream is None:
            return

        self.state = SESSION_EVALUATING
        probability: float | None = None
        min_bytes = 16000 * 2 // 4  # 250 ms is too little to judge
        if self.smart_turn_available and len(self._turn_audio) >= min_bytes:
            pcm = bytes(self._turn_audio[-(16000 * 2 * 8):])
            try:
                probability = await self._turn_scorer(pcm, self.sample_rate)
            except Exception:  # noqa: BLE001 - endpointing must never break
                LOGGER.exception("Smart Turn scoring failed")

        if probability is None or probability >= self.smart_turn_threshold:
            await self._finalize_turn(now)
            return

        LOGGER.info(
            "Smart Turn %.3f < %.2f: holding turn %d open for %.1fs",
            probability,
            self.smart_turn_threshold,
            self.turn_id,
            self.smart_turn_grace,
        )
        self.state = SESSION_PAUSED
        await self.send(
            {"type": "turn_hold", "turn_id": self.turn_id, "probability": probability}
        )
        self._grace_task = asyncio.create_task(self._grace_timer(self.turn_id))

    async def _finalize_turn(self, now: float) -> None:
        """Flush the ASR stream; the consumer will deliver the final <EOU>."""
        self._cancel_grace()
        stream = self.stream
        if stream is None or self.state == SESSION_FINALIZING:
            return
        if not self._turn_audio:
            # Nothing was ever said (VAD misfire): close without an ASR flush,
            # otherwise the session would wait forever for an <EOU>.
            self.state = SESSION_IDLE
            self.controller.end_turn(now)
            self.stream = None
            stream.close()
            await self.send({"type": "turn_end", "turn_id": self.turn_id})
            return
        self.state = SESSION_FINALIZING
        self.controller.end_turn(now)
        self.stream = None
        stream.finish()

    async def _grace_timer(self, turn_id: int) -> None:
        try:
            await asyncio.sleep(self.smart_turn_grace)
        except asyncio.CancelledError:
            return
        if self.state == SESSION_PAUSED and self.turn_id == turn_id:
            LOGGER.info("Smart Turn grace elapsed; finalizing turn %d", turn_id)
            self._grace_task = None
            await self._finalize_turn(time.monotonic())

    def _cancel_grace(self) -> None:
        task, self._grace_task = self._grace_task, None
        if task is not None and not task.done():
            task.cancel()

    async def aclose(self) -> None:
        """Release every resource; called when the transport disconnects."""
        self._cancel_grace()
        await self._stop_listening()
        await self._cancel_response()

    def _reply_active(self) -> bool:
        """True while a reply is being generated or its audio is still playing."""
        return bool((self.response_task and not self.response_task.done()) or self.bot_speaking)

    async def _finish_turn(self, event: dict, turn_id: int) -> None:
        if turn_id != self.turn_id:
            return
        self.controller.end_turn(time.monotonic())
        user_text = (event.get("text") or self.text).strip()
        verdict = classify_turn(user_text, expecting_answer=self.expecting_answer)

        # A real turn that arrived as a barge-in ends the ducked reply *before*
        # the new bubble appears, so the client never flashes the old turn.
        if verdict == "turn" and self.soft_barge:
            self.soft_barge = False
            self.pending_frames = None
            await self._cancel_response()
            await self.send({"type": "interrupted", "turn_id": self.reply_turn_id})

        await self.send(
            {
                "type": "eou",
                "text": user_text,
                "model_eou": event.get("model_eou", False),
                "turn_id": turn_id,
                "verdict": verdict,
            }
        )

        if verdict == "empty":
            # Noise, not words: if a reply was only ducked, let it carry on.
            if self.soft_barge and self._reply_active():
                self.soft_barge = False
                self.state = SESSION_RESPONDING
                await self.send({"type": "resume", "turn_id": turn_id})
                await self._flush_pending_audio()
                return
            self.soft_barge = False
            self.pending_frames = None
            self.state = SESSION_IDLE
            await self.send({"type": "turn_end", "turn_id": turn_id})
            return

        if verdict == "continuation":
            # "mm-hmm", "ok", "right": acknowledge it, never invoke the agent.
            await self.send({"type": "continuation", "text": user_text, "turn_id": turn_id})
            self.soft_barge = False
            if self._reply_active():
                self.state = SESSION_RESPONDING  # the ducked reply resumes
                await self._flush_pending_audio()
            else:
                self.state = SESSION_IDLE
                await self.send({"type": "turn_end", "turn_id": turn_id})
            return

        # A real turn: hand it to the agent.
        self.expecting_answer = False
        self.state = SESSION_RESPONDING
        self.reply_turn_id = turn_id
        # Cover assistant latency with a brief stall, if we have a clip ready.
        filler = self.controller.thinking_filler(time.monotonic())
        if filler and self._clip_exists(filler):
            await self.send({"type": "filler", "phrase": filler, "url": self._clip_url(filler)})
        elif filler:
            asyncio.create_task(self._warm_clip(filler))

        # The reply runs in its own task so a barge-in can cancel it mid-flight.
        self.response_task = asyncio.create_task(self._respond(user_text, turn_id))

    async def _respond(self, user_text: str, turn_id: int) -> None:
        try:
            await self._run_agent(user_text, turn_id)
            # If the user is already on a new turn, this reply was superseded.
            if self.state in (SESSION_LISTENING, SESSION_FINALIZING):
                return
            self.state = SESSION_IDLE
            await self.send({"type": "turn_end", "turn_id": turn_id})
        except asyncio.CancelledError:
            raise
        finally:
            if self.response_task is asyncio.current_task():
                self.response_task = None

    async def _cancel_response(self) -> None:
        """Cancel an in-flight reply task (best effort)."""
        self.pending_frames = None
        task = self.response_task
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                LOGGER.exception("Response task raised during interruption")
        self.response_task = None
        self.bot_speaking = False

    async def _interrupt(self, now: float, preroll_b64: str) -> None:
        """Barge-in: duck now, let the transcript decide whether to cancel.

        Cancelling here would kill the reply on every "mm-hmm". Instead the
        reply is left running (the client ducks it) and ``_finish_turn``
        resumes it for a continuer or cancels it for a real turn.
        """
        # A reply can finish generating while its audio is still playing:
        # state is IDLE and the task is gone, but bot_speaking is True. That
        # is exactly when a barge-in matters, so never treat it as idle.
        if (
            self.state == SESSION_IDLE
            and not self.response_task
            and self.stream is None
            and not self.bot_speaking
        ):
            return

        self.soft_barge = bool(
            (self.response_task and not self.response_task.done()) or self.bot_speaking
        )

        # Tear down the previous ASR stream (a no-op if it already finished).
        await self._stop_listening()

        await self.send({"type": "barge_pending", "turn_id": self.turn_id})

        seed = self._decode_preroll(preroll_b64)
        await self._begin_turn(now, seed_pcm=seed)

    # ------------------------------------------------------------- reply
    async def _run_agent(self, user_text: str, turn_id: int) -> None:
        if self.mode == "echo":
            await self.send({"type": "bot_text_delta", "text": f"[Echo test] You said: {user_text}", "turn_id": turn_id})
            await self._speak(f"[Echo test] You said: {user_text}", turn_id)
            return
        await self.send(
            {"type": "status", "phase": "thinking", "text": "Thinking…", "turn_id": turn_id}
        )
        try:
            chunks: list[str] = []
            async for chunk in self._agent_streamer(user_text, turn_id):
                if chunk:
                    chunks.append(chunk)
                    await self.send({"type": "bot_text_delta", "text": chunk, "turn_id": turn_id})
            reply = "".join(chunks).strip() or "I could not produce a response."
            # One normalized copy for the miniapp: real Telegram HTML there,
            # with <pre><code class="language-mermaid"> blocks for pele to mount.
            try:
                final_html = normalize_agent_html(reply)
            except Exception:
                final_html = reply
            await self.send(
                {"type": "bot_text_final", "text": final_html, "turn_id": turn_id}
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOGGER.exception("Agent streaming failed")
            reply = f"Agent error: {exc}"
            await self.send({"type": "error", "message": reply})
        # A question invites an answer: a bare "ok"/"yeah" now counts as one.
        self.expecting_answer = reply.rstrip().endswith("?")
        await self._speak(reply, turn_id)

    async def _speak(self, text: str, turn_id: int) -> None:
        # One voice chain for every TTS path: agent HTML converges to spoken
        # text (diagram sources and tags never reach the synthesis engine).
        text = voice_text_for_speech(text)
        if not text:
            return
        if _tts_provider() == "phonon":
            # Sentence streaming: the first sentence goes out while the rest
            # of the reply is still synthesizing (time-to-first-audio no
            # longer scales with reply length). The Chatterbox/CosyVoice
            # providers keep the whole-file path below.
            try:
                await self._speak_stream(text, turn_id)
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOGGER.exception("Phonon streaming TTS failed")
                self.bot_speaking = False
                await self.send({"type": "error", "message": f"TTS failed: {exc}"})
                return
        self.bot_speaking = True
        try:
            audio = await self._tts(text, self.mood)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOGGER.exception("Chatterbox TTS failed")
            self.bot_speaking = False
            await self.send({"type": "error", "message": f"TTS failed: {exc}"})
            return
        if self.soft_barge and turn_id == self.reply_turn_id:
            # The user is mid-utterance. Hold the audio: a continuer resumes it,
            # a real turn discards it (see _finish_turn). Never talk over them.
            frame = self._audio_frame(text, audio, turn_id, 0, 1, True)
            if self.pending_frames is None:
                self.pending_frames = []
            self.pending_frames.append(frame)
            return
        await self.send(self._audio_frame(text, audio, turn_id, 0, 1, True))

    @staticmethod
    def _audio_frame(
        text: str, audio: bytes, turn_id: int, idx: int, total: int, final: bool
    ) -> dict:
        return {
            "type": "bot_audio",
            "text": text,
            "audio": base64.b64encode(audio).decode(),
            "mime": _audio_mime(audio),
            "turn_id": turn_id,
            "frame": idx,
            "frames": total,
            "final": final,
        }

    async def _speak_stream(self, text: str, turn_id: int) -> None:
        """Stream one ``bot_audio`` frame per sentence while the rest synthesize.

        The producer (this generator chain) sends each sentence frame the
        moment it is synthesized; the WebSocket is the queue; the client
        plays them back to back. Soft-barge still applies per frame mid
        stream: while the user talks, frames buffer into pending_frames and
        a continuer flushes them in order.
        """
        chunks = plan_chunks(text)
        total = len(chunks)
        idx = 0
        self.bot_speaking = True
        async for wav in stream_phonon_wav(text, self.mood):
            frame = self._audio_frame(
                chunks[idx] if idx < total else "",
                wav,
                turn_id,
                idx,
                total,
                idx == total - 1,
            )
            if self.soft_barge and turn_id == self.reply_turn_id:
                if self.pending_frames is None:
                    self.pending_frames = []
                self.pending_frames.append(frame)
            else:
                await self.send(frame)
            idx += 1

    async def _flush_pending_audio(self) -> None:
        pending, self.pending_frames = self.pending_frames, None
        if pending is None:
            return
        self.bot_speaking = True
        for frame in pending:
            await self.send(frame)
