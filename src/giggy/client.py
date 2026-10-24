from collections.abc import Iterator
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import json
import math
from pathlib import Path
import time
from typing import BinaryIO, Literal, TypeAlias, TypedDict, cast
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen
from uuid import RFC_4122, UUID, uuid4

from websockets.sync.client import connect

from .errors import GiggyAPIError

class MicrophoneReady(TypedDict, total=False):
    generation_uuid: str
    attempt: int
    input_token: str

class TranscriptChunk(TypedDict):
    generation_uuid: str
    attempt: int
    text: str
    sequence: int
    replace: Literal[True]
    complete: bool

class GenerationUpdated(TypedDict, total=False):
    generation_uuid: str
    status: str
    attempt: int
    generation: dict

class TranscriptionAdmission(TypedDict):
    generation: dict

TranscriptionEvent: TypeAlias = (
    tuple[Literal["generation_created"], TranscriptionAdmission]
    | tuple[Literal["microphone_ready"], MicrophoneReady]
    | tuple[Literal["transcript_chunk"], TranscriptChunk]
    | tuple[Literal["generation_updated"], GenerationUpdated]
)

class Speech:
    def __init__(self, *, api_key: str, base_url: str) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    def _request(
        self,
        *,
        text: str,
        voice_id: str,
        mode: str,
        output_format: str,
        speed: float | None,
        idempotency_key: str | None = None,
    ):
        if not text.strip():
            raise ValueError("text must not be empty")
        if not voice_id.strip():
            raise ValueError("voice_id must not be empty")

        payload: dict[str, object] = {
            "text": text,
            "voice_id": voice_id,
            "model_id": "giggyspeech",
            "mode": mode,
            "output_format": output_format,
        }
        if speed is not None:
            payload["voice_settings"] = {"speed": speed}

        headers = {
            "xi-api-key": self._api_key,
            "content-type": "application/json",
        }
        if idempotency_key is not None:
            headers["idempotency-key"] = idempotency_key

        request = Request(
            self._base_url + "/text-to-speech",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            return urlopen(request, timeout=120)
        except HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise GiggyAPIError(error.code, body) from error

    def create(
        self,
        *,
        text: str,
        voice_id: str,
        mode: Literal["batch", "fast"] = "batch",
        output_format: str = "mp3_24000_160",
        speed: float | None = None,
        idempotency_key: str | None = None,
    ) -> bytes:
        response = self._request(
            text=text,
            voice_id=voice_id,
            mode=mode,
            output_format=output_format,
            speed=speed,
            idempotency_key=(idempotency_key if idempotency_key is not None else str(uuid4())),
        )
        with response:
            return response.read()

    @contextmanager
    def stream(
        self,
        *,
        text: str,
        voice_id: str,
        output_format: str = "pcm_24000",
        speed: float | None = None,
    ) -> Iterator[BinaryIO]:
        response = self._request(
            text=text,
            voice_id=voice_id,
            mode="streaming",
            output_format=output_format,
            speed=speed,
        )
        try:
            yield response
        finally:
            response.close()


class Transcriptions:
    def __init__(self, *, api_key: str, base_url: str) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._live_sessions = {}

    def _upload(self, audio_path: str | Path, *, mode: str, language: str | None,
                client_request_id: str | None, timeout: float = 1800):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        if mode not in ("batch", "fast", "streaming"):
            raise ValueError("mode must be batch, fast, or streaming")
        if language is not None and not language.strip():
            raise ValueError("language must not be blank")
        if client_request_id is not None:
            parsed_id = UUID(client_request_id)
            if parsed_id.variant != RFC_4122 or parsed_id.version not in (1, 2, 3, 4, 5):
                raise ValueError("client_request_id must be an RFC 4122 UUID v1–v5")
            client_request_id = str(parsed_id)
        query = {"mode": mode}
        if language is not None:
            query["language"] = language
        if client_request_id is not None:
            query["client_request_id"] = client_request_id
        path = Path(audio_path)
        with path.open("rb") as audio:
            request = Request(
                self._base_url + "/audio/transcriptions?" + urlencode(query),
                data=iter(lambda: audio.read(1024 * 1024), b""),
                headers={"xi-api-key": self._api_key, "Content-Type": "audio/wav",
                         "Content-Length": str(path.stat().st_size)},
                method="POST",
            )
            try:
                return urlopen(request, timeout=timeout)
            except HTTPError as error:
                raise GiggyAPIError(error.code, error.read().decode("utf-8", errors="replace")) from error

    def submit(self, audio_path: str | Path, *, mode: Literal["batch", "fast"] = "batch",
               language: str | None = None, client_request_id: str | None = None,
               timeout: float = 1800) -> dict:
        """Admit a WAV recording; retrieve its transcript after durable completion."""
        with self._upload(audio_path, mode=mode, language=language,
                          client_request_id=client_request_id, timeout=timeout) as response:
            if response.status != 202:
                raise RuntimeError(f"Expected HTTP 202 admission, got {response.status}")
            admission = json.load(response)
        if not isinstance(admission, dict) or not isinstance(admission.get("generation"), dict) or not isinstance(admission["generation"].get("generation_uuid"), str) or not isinstance(admission["generation"].get("status"), str):
            raise RuntimeError("Giggy returned an invalid transcription admission")
        return admission

    def transcribe(self, audio_path: str | Path, *, mode: Literal["batch", "fast"] = "batch",
                   language: str | None = None, client_request_id: str | None = None,
                   poll_interval: float = 2.5, timeout: float = 1800) -> dict:
        """Submit once, wait for the durable generation, and read its transcript."""
        if not math.isfinite(poll_interval) or not math.isfinite(timeout) or poll_interval <= 0 or timeout <= 0:
            raise ValueError("poll_interval and timeout must be finite and positive")
        deadline = time.monotonic() + timeout
        generation_uuid = None
        executor = ThreadPoolExecutor(max_workers=1)
        try:
            admission = executor.submit(self.submit, audio_path, mode=mode, language=language,
                                        client_request_id=client_request_id, timeout=timeout).result(timeout=timeout)
            generation_uuid = admission["generation"]["generation_uuid"]
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError()
                generation = executor.submit(self.get, generation_uuid, timeout=min(30, remaining)).result(timeout=remaining)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError()
                if generation["status"] == "completed":
                    return executor.submit(self.result, generation_uuid, timeout=remaining).result(timeout=remaining)
                if generation["status"] in ("failed", "cancelled", "admission_failed"):
                    raise RuntimeError(f"Transcription {generation_uuid} is {generation['status']}")
                time.sleep(min(poll_interval, remaining))
        except (TimeoutError, FutureTimeoutError) as error:
            identity = generation_uuid or "admission (generation UUID not received)"
            if time.monotonic() < deadline:
                raise TimeoutError(f"Transcription {identity} network I/O timed out; reconcile the existing operation before resubmitting") from error
            raise TimeoutError(f"Transcription {identity} did not complete within {timeout} seconds; do not resubmit an ambiguous upload") from error
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def get(self, generation_uuid: str, *, timeout: float = 30) -> dict:
        """Read the canonical generation status without polling or retrying."""
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        generation_uuid = str(UUID(generation_uuid))
        request = Request(self._base_url + "/generations/" + generation_uuid,
                          headers={"xi-api-key": self._api_key})
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = json.load(response)
        except HTTPError as error:
            raise GiggyAPIError(error.code, error.read().decode("utf-8", errors="replace")) from error
        generation = payload.get("generation") if isinstance(payload, dict) else None
        if not isinstance(generation, dict) or generation.get("generation_uuid") != generation_uuid or not isinstance(generation.get("status"), str):
            raise RuntimeError("Giggy returned an invalid generation")
        return generation

    def result(self, generation_uuid: str, *, timeout: float = 30) -> dict:
        """Fetch the signed transcript only after the generation completed."""
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        deadline = time.monotonic() + timeout
        generation = self.get(generation_uuid, timeout=timeout)
        if generation.get("status") != "completed":
            raise RuntimeError(f"Transcription is {generation.get('status')}")
        url = (generation.get("result") or {}).get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise RuntimeError("Transcript is no longer available")
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Transcript retrieval timed out")
            with urlopen(Request(url), timeout=remaining) as response:
                transcript = json.load(response)
        except HTTPError as error:
            raise GiggyAPIError(error.code, error.read().decode("utf-8", errors="replace")) from error
        if not isinstance(transcript, dict) or not isinstance(transcript.get("text"), str):
            raise RuntimeError("Giggy returned an invalid transcript")
        return transcript

    @contextmanager
    def stream(self, audio_path: str | Path, *, language: str | None = None,
               client_request_id: str | None = None) -> Iterator[Iterator[TranscriptionEvent]]:
        """Stream parsed SSE events while processing an uploaded WAV recording."""
        response = self._upload(audio_path, mode="streaming", language=language,
                                client_request_id=client_request_id)
        try:
            if response.status != 200 or "text/event-stream" not in response.headers.get("content-type", ""):
                raise RuntimeError("Giggy did not return a transcript event stream")
            yield self._events(response)
        finally:
            response.close()

    @contextmanager
    def live(self, *, client_request_id: str | None = None, language: str | None = None,
             timeout: float = 150) -> Iterator[Iterator[TranscriptionEvent]]:
        """One authenticated WebSocket; closing unfinished input cancels its operation."""
        if client_request_id is not None:
            raise ValueError("Live WebSocket identities are assigned by the server; omit client_request_id")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        base = urlsplit(self._base_url)
        if base.path.rstrip("/") != "/v1" or base.scheme not in {"https", "http"} or base.username or base.password or base.query or base.fragment:
            raise ValueError("base_url must be an explicit API origin ending with /v1")
        if base.scheme == "http" and base.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Live STT requires HTTPS except for loopback fixtures")
        url = urlunsplit(("wss" if base.scheme == "https" else "ws", base.netloc, "/v1/audio/transcriptions", "", ""))
        with connect(url, additional_headers={"xi-api-key": self._api_key}, compression=None,
                     open_timeout=min(10, timeout), close_timeout=1, max_size=16 * 1024 * 1024, proxy=None) as socket:
            state = {"socket": socket, "generation_uuid": None, "attempt": None, "sequence": 0,
                     "bytes": 0, "ended": False, "acknowledged": False, "sending": False,
                     "deadline": time.monotonic() + timeout, "last_sequence": -1, "final_text": None}
            try:
                socket.send(json.dumps({"type": "start", "format": "raw", "encoding": "LINEAR16",
                    "sampleRateHz": 16000, "interimResults": True, "options": {"endpointing": "external"},
                    **({"language": language} if language else {})}))
                yield self._events(socket, live_state=state)
            finally:
                self._live_sessions.pop(state["generation_uuid"], None)

    def send_live_frame(self, *, generation_uuid: str, sequence: int, pcm: bytes,
                        complete: bool = False, input_token: str | None = None) -> None:
        """Send ordered binary PCM; final input acceptance is read by the live iterator."""
        state = self._live_sessions.get(generation_uuid)
        if not state or state["ended"] or state["sending"] or type(sequence) is not int or sequence != state["sequence"]:
            raise ValueError("Frame must match the ready session and next sequence")
        if not isinstance(pcm, bytes) or len(pcm) > 32000 or len(pcm) % 2 or (not pcm and not complete) or state["bytes"] + len(pcm) > 43200000 * 32 or type(complete) is not bool:
            raise ValueError("Invalid PCM16 frame or admitted duration")
        if time.monotonic() >= state["deadline"]:
            raise TimeoutError("Live STT deadline exceeded; no audio was replayed")
        state["sending"] = True
        try:
            if pcm:
                state["socket"].send(pcm)
                state["bytes"] += len(pcm)
                state["sequence"] += 1
            if complete:
                state["ended"] = True
                state["socket"].send(json.dumps({"type": "stop"}))
        finally:
            state["sending"] = False

    def _events(self, response, *, live_state=None) -> Iterator[TranscriptionEvent]:
        if live_state is not None:
            state = live_state
            while True:
                remaining = state["deadline"] - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Live STT timed out; reconcile its generation")
                raw = response.recv(timeout=remaining)
                if not isinstance(raw, str):
                    raise RuntimeError("Expected a live STT control message")
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise RuntimeError("Invalid live STT event")
                kind = payload.get("type")
                gid = state["generation_uuid"]
                if kind == "generation_created":
                    admitted = payload.get("generation_uuid") or (payload.get("generation") or {}).get("generation_uuid")
                    if gid is not None or not isinstance(admitted, str) or str(UUID(admitted)) != admitted:
                        raise RuntimeError("Invalid live admission identity")
                    state["generation_uuid"] = admitted
                    yield cast(TranscriptionEvent, ("generation_created", {**payload, "generation": payload.get("generation") or {"generation_uuid": admitted, "status": "processing"}}))
                elif kind == "ready":
                    if not gid or payload.get("generation_uuid") != gid or state["attempt"] is not None or type(payload.get("attempt")) is not int or payload["attempt"] < 1 or payload.get("sampleRateHz") != 16000:
                        raise RuntimeError("Invalid live STT readiness")
                    state["attempt"] = payload["attempt"]
                    self._live_sessions[gid] = state
                    yield ("microphone_ready", {"generation_uuid": gid, "attempt": state["attempt"]})
                elif kind == "input_complete":
                    if not gid or payload.get("generation_uuid") != gid or not state["ended"] or state["acknowledged"] or type(payload.get("sequence")) is not int or payload["sequence"] != state["sequence"]:
                        raise RuntimeError("Invalid final input acknowledgement")
                    state["acknowledged"] = True
                elif kind == "transcription":
                    sequence = payload.get("sequence")
                    alternatives = payload.get("alternatives")
                    text = alternatives[0].get("transcript") if isinstance(alternatives, list) and alternatives and isinstance(alternatives[0], dict) else None
                    if not gid or payload.get("generation_uuid") != gid or type(payload.get("attempt")) is not int or payload["attempt"] != state["attempt"] or type(sequence) is not int or sequence <= state["last_sequence"] or payload.get("replace") is not True or type(payload.get("is_final")) is not bool or not isinstance(text, str) or state["final_text"] is not None:
                        raise RuntimeError("Invalid live transcript fence")
                    state["last_sequence"] = sequence
                    if payload["is_final"]:
                        if not state["ended"] or not state["acknowledged"]:
                            raise RuntimeError("Final transcript preceded acknowledged input completion")
                        state["final_text"] = text
                    yield ("transcript_chunk", {"generation_uuid": gid, "attempt": state["attempt"], "sequence": sequence, "replace": True, "complete": payload["is_final"], "text": text})
                elif kind == "generation_updated":
                    if not gid or payload.get("generation_uuid") != gid:
                        raise RuntimeError("Live generation identity changed")
                    status = payload.get("status") or (payload.get("generation") or {}).get("status")
                    if status in {"failed", "cancelled", "admission_failed"}:
                        raise RuntimeError("Live STT failed; no audio was replayed")
                    if status == "completed":
                        if not state["acknowledged"] or state["final_text"] is None:
                            raise RuntimeError("Live completion lacks a fenced final")
                        result = self.result(gid, timeout=max(.001, min(30, state["deadline"] - time.monotonic())))
                        if result.get("text") != state["final_text"] or result.get("audio_duration_ms") != (state["bytes"] + 31) // 32:
                            raise RuntimeError("Saved transcript or duration differs from live STT")
                    yield cast(TranscriptionEvent, ("generation_updated", payload))
                    if status == "completed":
                        return
                else:
                    raise RuntimeError("Unexpected live STT event; reconcile its generation")
            return
        event = ""
        data: list[str] = []
        for raw in response:
            line = raw.decode("utf-8").rstrip("\r\n")
            if line == "":
                if data:
                    payload = json.loads("\n".join(data))
                    if not isinstance(payload, dict):
                        raise RuntimeError("Giggy returned an invalid transcript event")
                    if event not in ("generation_created", "microphone_ready", "transcript_chunk", "generation_updated"):
                        raise RuntimeError(f"Unexpected transcript event: {event or '(unnamed)'}; reconcile the generation through get()")
                    yield cast(TranscriptionEvent, (event, payload))
                event, data = "", []
            elif line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip(" "))
        if data:
            raise RuntimeError("Transcript event stream ended mid-event")


class Giggy:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.giggy.ai/v1",
    ) -> None:
        if not api_key.strip():
            raise ValueError("api_key must not be empty")
        self.speech = Speech(api_key=api_key, base_url=base_url)
        self.transcriptions = Transcriptions(api_key=api_key, base_url=base_url)
