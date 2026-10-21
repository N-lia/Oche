"""gRPC client for the on-VM Parakeet-EOU speech-to-text service.

The Telegram bot (``TG.py``) and the Voice Lab WebApp (``webapp_server.py``)
call this module instead of posting base64 audio to a Modal endpoint. The
service itself lives on the Oracle Cloud VM; see ``asr_server.py`` and
``compose.yaml``.

Configuration (environment):
    PARAKEET_GRPC_TARGET   host:port of the ASR service. Default ``localhost:50051``.
                           Inside the OpenShell sandbox point this at the VM host
                           address, e.g. ``10.0.0.10:50051``.
    PARAKEET_GRPC_TLS      set to a truthy value ("1", "true", "yes") to use TLS.
    PARAKEET_GRPC_TIMEOUT  per-request timeout in seconds. Default ``45``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import queue
import threading
from collections.abc import AsyncIterator, Iterable, Iterator
from dataclasses import dataclass

import grpc

import oyi_asr_pb2 as pb
import oyi_asr_pb2_grpc as pb_grpc

LOGGER = logging.getLogger("oyi.asr.client")

DEFAULT_TARGET = "localhost:50051"
DEFAULT_TIMEOUT_SECONDS = 45.0
DEFAULT_MAX_MESSAGE_BYTES = 32 * 1024 * 1024

_CHANNEL_OPTIONS = (
    ("grpc.max_send_message_length", DEFAULT_MAX_MESSAGE_BYTES),
    ("grpc.max_receive_message_length", DEFAULT_MAX_MESSAGE_BYTES),
    ("grpc.keepalive_time_ms", 60_000),
    ("grpc.keepalive_timeout_ms", 10_000),
    ("grpc.keepalive_permit_without_calls", 1),
)

_CHANNEL: grpc.Channel | None = None


@dataclass(frozen=True)
class Transcript:
    """Decoded result from the ASR service."""

    text: str = ""
    eou_detected: bool = False
    raw: str = ""
    duration_s: float = 0.0
    error: str = ""


@dataclass(frozen=True)
class TurnScore:
    """Semantic end-of-turn result from the Smart Turn model."""

    probability: float = 0.0
    complete: bool = False
    duration_s: float = 0.0
    error: str = ""


def get_target() -> str:
    return os.getenv("PARAKEET_GRPC_TARGET", DEFAULT_TARGET).strip() or DEFAULT_TARGET


def get_timeout() -> float:
    raw = os.getenv("PARAKEET_GRPC_TIMEOUT")
    if raw is None:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        return float(raw)
    except ValueError:
        LOGGER.warning("Invalid PARAKEET_GRPC_TIMEOUT=%r; using default %.0fs", raw, DEFAULT_TIMEOUT_SECONDS)
        return DEFAULT_TIMEOUT_SECONDS


def _tls_enabled() -> bool:
    return os.getenv("PARAKEET_GRPC_TLS", "").strip().lower() in {"1", "true", "yes", "on"}


def get_channel() -> grpc.Channel:
    """Return a process-wide channel to the ASR service."""
    global _CHANNEL
    if _CHANNEL is None:
        target = get_target()
        if _tls_enabled():
            _CHANNEL = grpc.secure_channel(target, grpc.ssl_channel_credentials(), options=_CHANNEL_OPTIONS)
        else:
            _CHANNEL = grpc.insecure_channel(target, options=_CHANNEL_OPTIONS)
    return _CHANNEL


def close_channel() -> None:
    """Close the cached channel (used on shutdown)."""
    global _CHANNEL
    if _CHANNEL is not None:
        _CHANNEL.close()
        _CHANNEL = None


def transcribe(
    audio: bytes,
    *,
    format: str = "",
    sample_rate: int = 0,
    timeout: float | None = None,
) -> Transcript:
    """Transcribe one audio segment over gRPC.

    Errors are returned in ``Transcript.error`` (matching the service contract)
    rather than raised, so callers can surface a friendly message.
    """
    if not audio:
        return Transcript(error="empty audio payload")

    stub = pb_grpc.ParakeetASRStub(get_channel())
    request = pb.TranscribeRequest(audio=audio, format=format, sample_rate=sample_rate)
    try:
        response = stub.Transcribe(request, timeout=timeout or get_timeout())
    except grpc.RpcError as exc:
        LOGGER.warning("Parakeet ASR gRPC call failed: %s", exc)
        return Transcript(error=_rpc_error_message(exc))

    return Transcript(
        text=response.text,
        eou_detected=response.eou_detected,
        raw=response.raw,
        duration_s=response.duration_s,
        error=response.error,
    )


async def atranscribe(
    audio: bytes,
    *,
    format: str = "",
    sample_rate: int = 0,
    timeout: float | None = None,
) -> Transcript:
    """Async wrapper around :func:`transcribe` for asyncio callers."""
    return await asyncio.to_thread(
        transcribe, audio, format=format, sample_rate=sample_rate, timeout=timeout
    )


def score_turn(
    pcm: bytes,
    *,
    sample_rate: int = 16000,
    format: str = "pcm16",
    threshold: float = 0.0,
    timeout: float | None = None,
) -> TurnScore:
    """Ask the service whether an utterance is semantically complete.

    ``threshold`` of zero lets the server use its configured default. Errors
    are returned in ``TurnScore.error`` so the caller can fall back to plain
    silence endpointing when Smart Turn is unavailable.
    """
    if not pcm:
        return TurnScore(error="empty audio payload")

    stub = pb_grpc.ParakeetASRStub(get_channel())
    request = pb.TurnScoreRequest(
        audio=pcm, sample_rate=sample_rate, format=format, threshold=threshold
    )
    try:
        response = stub.ScoreTurn(request, timeout=timeout or min(get_timeout(), 15.0))
    except grpc.RpcError as exc:
        LOGGER.warning("Smart Turn gRPC call failed: %s", exc)
        return TurnScore(error=_rpc_error_message(exc))

    return TurnScore(
        probability=response.probability,
        complete=response.complete,
        duration_s=response.duration_s,
        error=response.error,
    )


async def ascore_turn(
    pcm: bytes,
    *,
    sample_rate: int = 16000,
    format: str = "pcm16",
    threshold: float = 0.0,
    timeout: float | None = None,
) -> TurnScore:
    """Async wrapper around :func:`score_turn` for asyncio callers."""
    return await asyncio.to_thread(
        score_turn,
        pcm,
        sample_rate=sample_rate,
        format=format,
        threshold=threshold,
        timeout=timeout,
    )


def health(timeout: float | None = None) -> pb.HealthResponse | None:
    """Probe service readiness. Returns ``None`` when the service is unreachable."""
    stub = pb_grpc.ParakeetASRStub(get_channel())
    try:
        return stub.Health(pb.HealthRequest(), timeout=timeout or min(get_timeout(), 5.0))
    except grpc.RpcError as exc:
        LOGGER.warning("Parakeet ASR health check failed: %s", exc)
        return None


def _rpc_error_message(exc: grpc.RpcError) -> str:
    code = exc.code().name if exc.code() else "UNKNOWN"
    detail = exc.details() or "no details"
    return f"ASR service unreachable ({code}): {detail}. Target={get_target()}."


# ---------------------------------------------------------------------------
# Streaming transcription
# ---------------------------------------------------------------------------


def stream_transcribe(
    requests: Iterable[pb.StreamRequest],
    *,
    timeout: float | None = None,
) -> Iterator[dict]:
    """Synchronously consume a stream of requests and yield event dicts.

    Event dicts are one of:
        {"type": "partial", "text", "delta", "stable"}
        {"type": "eou", "text", "raw", "model_eou"}
        {"type": "error", "message"}
    """
    stub = pb_grpc.ParakeetASRStub(get_channel())
    try:
        for response in stub.StreamTranscribe(iter(requests), timeout=timeout):
            yield _decode_stream_response(response)
    except grpc.RpcError as exc:
        yield {"type": "error", "message": _rpc_error_message(exc)}


def _decode_stream_response(response: pb.StreamResponse) -> dict:
    kind = response.WhichOneof("event")
    if kind == "partial":
        return {
            "type": "partial",
            "text": response.partial.text,
            "delta": response.partial.delta,
            "stable": response.partial.stable,
        }
    if kind == "eou":
        return {
            "type": "eou",
            "text": response.eou.text,
            "raw": response.eou.raw,
            "model_eou": response.eou.model_eou,
        }
    return {"type": "error", "message": response.error.message if kind == "error" else "empty stream event"}


class AsyncTranscribeStream:
    """Async bridge around the blocking gRPC stream for asyncio servers.

    The gRPC stream runs in a worker thread; audio is fed with :meth:`feed` /
    :meth:`finish` and events are consumed with ``async for event in stream``.
    """

    _SENTINEL = object()

    def __init__(self, loop: asyncio.AbstractEventLoop, sample_rate: int = 16000) -> None:
        self._loop = loop
        self._sample_rate = sample_rate or 16000
        self._requests: queue.Queue[pb.AudioChunk | None] = queue.Queue()
        self._events: asyncio.Queue = asyncio.Queue()
        self._thread = threading.Thread(target=self._run, name="oyi-asr-stream", daemon=True)
        self._started = False
        # The live RPC handle, so close() can cancel it even when the server is
        # waiting for audio and would otherwise never notice the stream is dead.
        self._call = None
        self._closed = False

    def _request_iterator(self) -> Iterator[pb.StreamRequest]:
        yield pb.StreamRequest(config=pb.StreamConfig(sample_rate=self._sample_rate, format="pcm16"))
        while True:
            chunk = self._requests.get()
            if chunk is None:
                return
            yield pb.StreamRequest(audio=chunk)

    def _run(self) -> None:
        stub = pb_grpc.ParakeetASRStub(get_channel())
        try:
            self._call = stub.StreamTranscribe(iter(self._request_iterator()))
            for response in self._call:
                event = _decode_stream_response(response)
                self._loop.call_soon_threadsafe(self._events.put_nowait, event)
        except grpc.RpcError as exc:
            # A cancel we initiated is expected; only surface real failures.
            if not self._closed:
                self._loop.call_soon_threadsafe(
                    self._events.put_nowait, {"type": "error", "message": _rpc_error_message(exc)}
                )
        finally:
            # Unblock the gRPC request-consumer thread if it is still waiting.
            self._requests.put(None)
            self._loop.call_soon_threadsafe(self._events.put_nowait, self._SENTINEL)

    def start(self) -> "AsyncTranscribeStream":
        if not self._started:
            self._started = True
            self._thread.start()
        return self

    def feed(self, pcm16: bytes) -> None:
        self._requests.put(pb.AudioChunk(pcm=pcm16))

    def finish(self) -> None:
        """Signal the final chunk; the server flushes and closes the stream."""
        self._requests.put(pb.AudioChunk(pcm=b"", last=True))

    def close(self) -> None:
        """Terminate the stream, cancelling the RPC if it is still live.

        Idempotent and safe to call from any path (EOU, barge-in, disconnect).
        """
        self._closed = True
        self._requests.put(None)
        call = self._call
        if call is not None:
            try:
                call.cancel()
            except Exception:  # noqa: BLE001 - best effort teardown
                LOGGER.debug("Could not cancel ASR stream", exc_info=True)

    async def events(self) -> AsyncIterator[dict]:
        while True:
            event = await self._events.get()
            if event is self._SENTINEL:
                return
            yield event

    def __aiter__(self) -> AsyncIterator[dict]:
        return self.events()

