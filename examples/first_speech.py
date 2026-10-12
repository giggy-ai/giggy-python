"""One intended speech generation; obtains a voice UUID without hardcoding one."""

import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

from giggy import Giggy


def main():
    key = os.environ.get("GIGGY_API_KEY")
    if not key:
        raise SystemExit("Set GIGGY_API_KEY using your own Giggy account.")
    voice_id = os.environ.get("GIGGY_VOICE_ID")
    if not voice_id:
        request = Request(
            "https://giggy.ai/v1/voices",
            headers={
                "Authorization": "Bearer " + key,
                "User-Agent": "Giggy-Python-Example/0.1.0",
            },
        )
        with urlopen(request, timeout=30) as response:
            voices = json.load(response).get("voices", [])
        if not voices or not voices[0].get("voice_id"):
            raise SystemExit(
                "No voices returned; select a voice in your Giggy account."
            )
        voice_id = voices[0]["voice_id"]
    audio = Giggy(api_key=key).speech.create(
        text="Hello from Giggy.", voice_id=voice_id
    )
    Path("speech.mp3").write_bytes(audio)
    print("Wrote speech.mp3")


if __name__ == "__main__":
    main()
