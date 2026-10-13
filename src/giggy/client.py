from collections.abc import Iterator
from base64 import b64encode
from contextlib import contextmanager
import json
from pathlib import Path
import re
import time
from typing import BinaryIO, Literal
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen
from uuid import RFC_4122, UUID, uuid4

from .errors import GiggyAPIError


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

    def _upload(self, audio_path: str | Path, *, mode: str, language: str | None,
                client_request_id: str | None):
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
                return urlopen(request, timeout=1800)
            except HTTPError as error:
                raise GiggyAPIError(error.code, error.read().decode("utf-8", errors="replace")) from error

    def submit(self, audio_path: str | Path, *, mode: Literal["batch", "fast"] = "batch",
               language: str | None = None, client_request_id: str | None = None) -> dict:
        """Admit a WAV recording; retrieve its transcript after durable completion."""
        with self._upload(audio_path, mode=mode, language=language,
                          client_request_id=client_request_id) as response:
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
        if poll_interval <= 0 or timeout <= 0:
            raise ValueError("poll_interval and timeout must be positive")
        admission = self.submit(audio_path, mode=mode, language=language,
                                client_request_id=client_request_id)
        generation_uuid = admission["generation"]["generation_uuid"]
        deadline = time.monotonic() + timeout
        while True:
            generation = self.get(generation_uuid)
            if generation["status"] == "completed":
                return self.result(generation_uuid)
            if generation["status"] in ("failed", "cancelled", "admission_failed"):
                raise RuntimeError(f"Transcription {generation_uuid} is {generation['status']}")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Transcription {generation_uuid} did not complete within {timeout} seconds")
            time.sleep(min(poll_interval, remaining))

    def get(self, generation_uuid: str) -> dict:
        """Read the canonical generation status without polling or retrying."""
        generation_uuid = str(UUID(generation_uuid))
        request = Request(self._base_url + "/generations/" + generation_uuid,
                          headers={"xi-api-key": self._api_key})
        try:
            with urlopen(request, timeout=30) as response:
                payload = json.load(response)
        except HTTPError as error:
            raise GiggyAPIError(error.code, error.read().decode("utf-8", errors="replace")) from error
        generation = payload.get("generation") if isinstance(payload, dict) else None
        if not isinstance(generation, dict) or generation.get("generation_uuid") != generation_uuid or not isinstance(generation.get("status"), str):
            raise RuntimeError("Giggy returned an invalid generation")
        return generation

    def result(self, generation_uuid: str) -> dict:
        """Fetch the signed transcript only after the generation completed."""
        generation = self.get(generation_uuid)
        if generation.get("status") != "completed":
            raise RuntimeError(f"Transcription is {generation.get('status')}")
        url = (generation.get("result") or {}).get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise RuntimeError("Transcript is no longer available")
        try:
            with urlopen(Request(url), timeout=30) as response:
                transcript = json.load(response)
        except HTTPError as error:
            raise GiggyAPIError(error.code, error.read().decode("utf-8", errors="replace")) from error
        if not isinstance(transcript, dict) or not isinstance(transcript.get("text"), str):
            raise RuntimeError("Giggy returned an invalid transcript")
        return transcript

    @contextmanager
    def stream(self, audio_path: str | Path, *, language: str | None = None,
               client_request_id: str | None = None) -> Iterator[Iterator[tuple[str, dict]]]:
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
    def live(self, *, client_request_id: str | None = None) -> Iterator[Iterator[tuple[str, dict]]]:
        """Open a paid live microphone SSE session; send frames separately after readiness."""
        if client_request_id is not None:
            parsed_id = UUID(client_request_id)
            if parsed_id.variant != RFC_4122 or parsed_id.version not in (1, 2, 3, 4, 5):
                raise ValueError("client_request_id must be an RFC 4122 UUID v1–v5")
            client_request_id = str(parsed_id)
        payload = {"mode": "streaming", "microphone": True}
        if client_request_id is not None:
            payload["client_request_id"] = client_request_id
        request = Request(
            self._base_url + "/audio/transcriptions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"xi-api-key": self._api_key, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            response = urlopen(request, timeout=150)
        except HTTPError as error:
            raise GiggyAPIError(error.code, error.read().decode("utf-8", errors="replace")) from error
        try:
            if response.status != 200 or "text/event-stream" not in response.headers.get("content-type", ""):
                raise RuntimeError("Giggy did not return a live transcript event stream")
            yield self._events(response)
        finally:
            response.close()

    def send_live_frame(self, *, generation_uuid: str, input_token: str, sequence: int,
                        pcm: bytes, complete: bool = False) -> None:
        """Send one ordered PCM16 frame; the caller must await its acceptance before the next."""
        generation_uuid = str(UUID(generation_uuid))
        if not re.fullmatch(r"[0-9a-f]{64}", input_token):
            raise ValueError("input_token must be the microphone_ready capability")
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
            raise ValueError("sequence must be a nonnegative integer")
        if not isinstance(pcm, bytes) or len(pcm) > 32000 or len(pcm) % 2 or (not pcm and not complete):
            raise ValueError("pcm must be an even PCM16 byte frame of at most one second")
        parsed = urlsplit(self._base_url)
        if parsed.path.rstrip("/") != "/v1":
            raise ValueError("base_url must end with /v1 for live input")
        origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
        request = Request(
            origin + "/api/generations/" + generation_uuid + "/stt-input",
            data=json.dumps({"input_token": input_token, "sequence": sequence,
                             "pcm_base64": b64encode(pcm).decode("ascii"), "complete": complete}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:
                accepted = json.load(response)
        except HTTPError as error:
            raise GiggyAPIError(error.code, error.read().decode("utf-8", errors="replace")) from error
        if not isinstance(accepted, dict) or accepted.get("ok") is not True:
            raise RuntimeError("Giggy did not accept the PCM frame")

    def _events(self, response) -> Iterator[tuple[str, dict]]:
        event = ""
        data: list[str] = []
        for raw in response:
            line = raw.decode("utf-8").rstrip("\r\n")
            if line == "":
                if data:
                    payload = json.loads("\n".join(data))
                    if not isinstance(payload, dict):
                        raise RuntimeError("Giggy returned an invalid transcript event")
                    yield event, payload
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
        base_url: str = "https://giggy.ai/v1",
    ) -> None:
        if not api_key.strip():
            raise ValueError("api_key must not be empty")
        self.speech = Speech(api_key=api_key, base_url=base_url)
        self.transcriptions = Transcriptions(api_key=api_key, base_url=base_url)
