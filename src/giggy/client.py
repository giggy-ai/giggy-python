from collections.abc import Iterator
from contextlib import contextmanager
import json
from typing import BinaryIO, Literal
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

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
