import json
import math
from collections.abc import Iterator
from contextlib import contextmanager
from typing import BinaryIO, Literal
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from .errors import GiggyAPIError


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def urlopen(request, *, timeout):
    """Keep credentials on the configured origin; never replay a paid POST."""
    return build_opener(_NoRedirect()).open(request, timeout=timeout)


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
        if mode not in {"batch", "fast", "streaming"}:
            raise ValueError("mode must be batch, fast or streaming")
        if speed is not None and (not math.isfinite(speed) or not 0.25 <= speed <= 4):
            raise ValueError("speed must be finite and between 0.25 and 4")

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
            "User-Agent": "Giggy-Python-SDK/0.1.0",
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
            try:
                body = error.read(65536).decode("utf-8", errors="replace")
            finally:
                error.close()
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
        if mode not in {"batch", "fast"}:
            raise ValueError(
                "create mode must be batch or fast; use stream for streaming"
            )
        response = self._request(
            text=text,
            voice_id=voice_id,
            mode=mode,
            output_format=output_format,
            speed=speed,
            idempotency_key=(
                idempotency_key if idempotency_key is not None else str(uuid4())
            ),
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
            if output_format.startswith("pcm_") and not response.headers.get(
                "Content-Type", ""
            ).lower().startswith("audio/pcm"):
                raise ValueError("Expected progressive PCM audio from Giggy")
            yield response
        finally:
            response.close()


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
