"""Parakeet-EOU gRPC speech-to-text server (runs on the Oracle Cloud VM).

This replaces the Modal-hosted ``modal_parakeet.py`` endpoint. It loads the
ONNX INT8 160ms Parakeet-EOU model once and serves transcription over gRPC so
the Telegram bot and Voice Lab WebApp can reach it at a host address.

Run locally:
    python asr_server.py

Run on the VM (compose):
    docker compose up -d parakeet-asr

Configuration (environment):
    PARAKEET_GRPC_BIND   Address to bind. Default ``0.0.0.0:50051``.
    PARAKEET_MODEL_DIR   Directory holding the ONNX model files.
                         Default ``models/parakeet-eou-160ms``.
    PARAKEET_MAX_WORKERS Concurrent request threads. Default ``8``. Each live
                         streaming turn occupies one, so this bounds concurrency.
    PARAKEET_STREAM_IDLE_TIMEOUT
                         Seconds of silence after which a streaming turn is
                         finalized and its worker released. Default ``90``.
    SMART_TURN_MODEL_DIR Directory holding ``smart-turn-v3.2-cpu.onnx``.
                         Default ``models/smart-turn``; empty disables semantic
                         end-of-turn scoring.
    SMART_TURN_AUTO_DOWNLOAD
                         Download the Smart Turn model on first start.
                         Default ``1``.
    SMART_TURN_THRESHOLD Probability above which a turn counts as complete.
                         Default ``0.5``.
"""

from __future__ import annotations

import argparse
import logging
import os
import queue
import signal
import subprocess
import tempfile
import threading
import time
from concurrent import futures

import grpc
import numpy as np

import oyi_asr_pb2 as pb
import oyi_asr_pb2_grpc as pb_grpc
from parakeet_eou_onnx import ParakeetEOU, StreamingSession
from smart_turn import SmartTurnAnalyzer, score_pcm16, threshold_from_env

LOGGER = logging.getLogger("oyi.asr.server")

MODEL_ID = "soniqo/Parakeet-EOU-120M-ONNX-INT8-160ms"
DEFAULT_BIND = "0.0.0.0:50051"
DEFAULT_MODEL_DIR = "models/parakeet-eou-160ms"
DEFAULT_MAX_WORKERS = 8
DEFAULT_MAX_MESSAGE_BYTES = 32 * 1024 * 1024
DEFAULT_STREAM_INTERVAL = 0.6
DEFAULT_SMART_TURN_DIR = "models/smart-turn"
# A streaming turn holds one worker thread for its whole lifetime. Reap a
# stream that has gone silent so an abandoned client cannot exhaust the pool.
DEFAULT_STREAM_IDLE_TIMEOUT = 90.0

_SERVER_OPTIONS = (
    ("grpc.max_send_message_length", DEFAULT_MAX_MESSAGE_BYTES),
    ("grpc.max_receive_message_length", DEFAULT_MAX_MESSAGE_BYTES),
    ("grpc.so_reuseport", 0),
    # Allow the idle-connection keepalive pings our clients send (defaults are
    # strict enough to trigger ENHANCE_YOUR_CALM GOAWAYs on an idle channel).
    ("grpc.keepalive_permit_without_calls", 1),
    ("grpc.http2.min_ping_interval_without_data_ms", 15_000),
    ("grpc.http2.max_pings_without_data", 0),
)


def _decode_audio(data: bytes, fmt: str = "") -> tuple[np.ndarray, int]:
    """Decode arbitrary audio bytes into float32 mono audio plus its sample rate.

    Uses ffmpeg (with a format-aware temp suffix) and falls back to soundfile
    for containers it can read directly, mirroring the old Modal endpoint.
    """
    import soundfile as sf

    suffix = f".{fmt.lstrip('.')}" if fmt else ".audio"
    tmp_in = ""
    tmp_wav = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            tmp_in = tmp.name
        tmp_wav = f"{tmp_in}.wav"

        cmd = ["ffmpeg", "-y", "-i", tmp_in, "-ar", "16000", "-ac", "1", "-f", "wav", tmp_wav]
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if result.returncode == 0 and os.path.exists(tmp_wav):
            audio, rate = sf.read(tmp_wav, dtype="float32")
        else:
            audio, rate = sf.read(tmp_in, dtype="float32")
    finally:
        for path in (tmp_in, tmp_wav):
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim == 2:
        audio = audio.mean(axis=1)
    if audio.ndim != 1 or not len(audio) or not np.isfinite(audio).all():
        raise ValueError("expected finite, non-empty mono audio")
    return audio, int(rate)


def _iter_requests(request_iterator, idle_timeout: float):
    """Yield client requests, ending the stream after ``idle_timeout`` of silence.

    gRPC's sync request iterator blocks on a condition variable and only wakes
    when the *RPC* terminates — so a client that stops sending audio without
    closing the stream would otherwise pin this worker thread indefinitely.
    Pumping through a queue lets the handler give up on a dead stream. The pump
    thread unblocks on its own once the handler returns and gRPC ends the RPC.
    """
    if idle_timeout <= 0:
        yield from request_iterator
        return

    items: "queue.Queue[object]" = queue.Queue()
    sentinel = object()

    def _pump() -> None:
        try:
            for request in request_iterator:
                items.put(request)
        except Exception:  # noqa: BLE001 - iterator ends on RPC termination
            pass
        finally:
            items.put(sentinel)

    threading.Thread(target=_pump, name="oyi-asr-recv", daemon=True).start()
    while True:
        try:
            item = items.get(timeout=idle_timeout)
        except queue.Empty:
            LOGGER.warning("Streaming request idle for %.0fs; ending stream", idle_timeout)
            return
        if item is sentinel:
            return
        yield item


class ParakeetASRServicer(pb_grpc.ParakeetASRServicer):
    """gRPC servicer backed by a single shared :class:`ParakeetEOU` model."""

    def __init__(
        self,
        model: ParakeetEOU,
        model_dir: str,
        smart_turn: SmartTurnAnalyzer | None = None,
    ) -> None:
        self._model = model
        self._model_dir = model_dir
        self._smart_turn = smart_turn

    def Health(self, request: pb.HealthRequest, context: grpc.ServicerContext) -> pb.HealthResponse:
        return pb.HealthResponse(
            ready=True,
            model=MODEL_ID,
            detail=self._model_dir,
            smart_turn_ready=self._smart_turn is not None,
            smart_turn_model=self._smart_turn.model_path if self._smart_turn else "",
        )

    def Transcribe(
        self, request: pb.TranscribeRequest, context: grpc.ServicerContext
    ) -> pb.TranscribeResponse:
        if not request.audio:
            return pb.TranscribeResponse(error="empty audio payload")

        try:
            audio, rate = _decode_audio(request.audio, request.format)
        except Exception as exc:  # noqa: BLE001 - surfaced to the client
            LOGGER.warning("Audio decode failed: %s", exc)
            return pb.TranscribeResponse(error=f"decode failed: {exc}")

        try:
            result = self._model.transcribe(audio, sample_rate=request.sample_rate or rate)
        except Exception as exc:  # noqa: BLE001 - surfaced to the client
            LOGGER.exception("Parakeet inference failed")
            return pb.TranscribeResponse(error=f"inference failed: {exc}")

        return pb.TranscribeResponse(
            text=result.get("text", ""),
            eou_detected=bool(result.get("eou_detected", False)),
            raw=result.get("raw", ""),
            duration_s=float(result.get("duration_s", 0.0)),
        )

    def ScoreTurn(
        self, request: pb.TurnScoreRequest, context: grpc.ServicerContext
    ) -> pb.TurnScoreResponse:
        """Score whether the utterance that just paused is semantically done."""
        if self._smart_turn is None:
            return pb.TurnScoreResponse(error="smart turn model not loaded")
        if not request.audio:
            return pb.TurnScoreResponse(error="empty audio payload")

        started = time.time()
        try:
            if request.format in ("", "pcm16"):
                probability = score_pcm16(
                    self._smart_turn, request.audio, sample_rate=request.sample_rate or 16000
                )
            else:
                audio, rate = _decode_audio(request.audio, request.format)
                probability = self._smart_turn.score(
                    audio, sample_rate=request.sample_rate or rate
                )
        except Exception as exc:  # noqa: BLE001 - surfaced to the client
            LOGGER.exception("Smart Turn inference failed")
            return pb.TurnScoreResponse(error=f"smart turn failed: {exc}")

        threshold = float(request.threshold) or threshold_from_env()
        return pb.TurnScoreResponse(
            probability=probability,
            complete=probability >= threshold,
            duration_s=time.time() - started,
        )

    def StreamTranscribe(self, request_iterator, context: grpc.ServicerContext):
        """Consume streaming PCM and emit partials plus the closing <EOU>."""
        interval = float(os.getenv("PARAKEET_STREAM_INTERVAL", DEFAULT_STREAM_INTERVAL))
        idle_timeout = float(os.getenv("PARAKEET_STREAM_IDLE_TIMEOUT", DEFAULT_STREAM_IDLE_TIMEOUT))
        session = StreamingSession(self._model, decode_interval=interval)
        try:
            for request in _iter_requests(request_iterator, idle_timeout):
                payload = request.WhichOneof("payload")
                if payload == "config":
                    sample_rate = request.config.sample_rate or 16000
                    session = StreamingSession(
                        self._model, sample_rate=sample_rate, decode_interval=interval
                    )
                elif payload == "audio":
                    for event in session.push_pcm16(request.audio.pcm):
                        yield _to_stream_response(event)
                    if request.audio.last:
                        for event in session.finalize():
                            yield _to_stream_response(event)
                        return
            # Client closed without an explicit final chunk: flush anyway.
            for event in session.finalize():
                yield _to_stream_response(event)
        except Exception as exc:  # noqa: BLE001 - surfaced to the client
            LOGGER.exception("Streaming transcription failed")
            yield pb.StreamResponse(error=pb.StreamError(message=str(exc)))


def _to_stream_response(event: dict) -> pb.StreamResponse:
    if event["type"] == "partial":
        return pb.StreamResponse(
            partial=pb.PartialTranscript(
                text=event["text"], delta=event["delta"], stable=event["stable"]
            )
        )
    return pb.StreamResponse(
        eou=pb.EouEvent(
            text=event["text"], raw=event["raw"], model_eou=event["model_eou"]
        )
    )


def build_server(
    model: ParakeetEOU,
    bind: str,
    model_dir: str,
    max_workers: int,
    smart_turn: SmartTurnAnalyzer | None = None,
) -> grpc.Server:
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=max_workers),
        options=_SERVER_OPTIONS,
    )
    pb_grpc.add_ParakeetASRServicer_to_server(
        ParakeetASRServicer(model, model_dir, smart_turn), server
    )
    bound_port = server.add_insecure_port(bind)
    if bound_port == 0:
        raise RuntimeError(f"Could not bind Parakeet ASR server to {bind!r}")
    LOGGER.info("Parakeet ASR gRPC server bound to %s (port %d)", bind, bound_port)
    return server


def _install_signal_handlers(server: grpc.Server) -> None:
    def _shutdown(signum: int, _frame: object) -> None:
        LOGGER.info("Received signal %s; shutting down Parakeet ASR server", signum)
        server.stop(grace=5)

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _shutdown)


def serve(bind: str, model_dir: str, max_workers: int, smart_turn_dir: str) -> None:
    LOGGER.info("Loading Parakeet-EOU model from %s", model_dir)
    model = ParakeetEOU(model_dir)

    smart_turn = None
    if smart_turn_dir:
        auto = os.getenv("SMART_TURN_AUTO_DOWNLOAD", "1").strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }
        try:
            smart_turn = SmartTurnAnalyzer.load(smart_turn_dir, auto_download=auto)
        except Exception:  # noqa: BLE001 - turn-taking degrades to fixed silence
            LOGGER.exception("Could not load the Smart Turn model from %s", smart_turn_dir)
    if smart_turn is None:
        LOGGER.warning("Smart Turn unavailable; endpointing falls back to client VAD silence")
    else:
        LOGGER.info("Smart Turn ready (%s)", smart_turn.model_path)

    server = build_server(model, bind, model_dir, max_workers, smart_turn)
    _install_signal_handlers(server)
    server.start()
    LOGGER.info("Parakeet ASR ready and listening on %s", bind)
    server.wait_for_termination()


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Parakeet-EOU gRPC ASR server")
    parser.add_argument("--bind", default=os.getenv("PARAKEET_GRPC_BIND", DEFAULT_BIND))
    parser.add_argument("--model-dir", default=os.getenv("PARAKEET_MODEL_DIR", DEFAULT_MODEL_DIR))
    parser.add_argument("--max-workers", type=int, default=int(os.getenv("PARAKEET_MAX_WORKERS", DEFAULT_MAX_WORKERS)))
    parser.add_argument(
        "--smart-turn-dir",
        default=os.getenv("SMART_TURN_MODEL_DIR", DEFAULT_SMART_TURN_DIR),
        help="Directory holding smart-turn-v3.2-cpu.onnx (empty disables).",
    )
    args = parser.parse_args()

    serve(
        bind=args.bind,
        model_dir=args.model_dir,
        max_workers=max(1, args.max_workers),
        smart_turn_dir=args.smart_turn_dir,
    )


if __name__ == "__main__":
    main()
