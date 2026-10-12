# Giggy Python SDK

Official Giggy-maintained Python SDK for speech generation. Python 3.10+, standard-library runtime, no automatic synthesis retries.

**Not published to PyPI yet.** Use the source install below; `pip install giggy` is not a supported installation path until the approved release is published.

[Create an account](https://giggy.ai/?login=1&utm_source=github&utm_medium=referral&utm_campaign=developer_onboarding&utm_content=giggy-python_readme) · [API guide](https://giggy.ai/docs/speech-api?utm_source=github&utm_medium=referral&utm_campaign=developer_onboarding&utm_content=giggy-python_docs) · [Try the browser voice agent](https://github.com/giggy-ai/giggy-voice-agent)

## Install and get your first MP3

```sh
git clone https://github.com/giggy-ai/giggy-python.git
cd giggy-python
python -m venv .venv
# macOS/Linux: source .venv/bin/activate
# PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install .
```

Create your own API key in [Giggy Speech API](https://giggy.ai/app/speech-api). Keep it server-side.

```sh
export GIGGY_API_KEY="your-api-key"
python examples/first_speech.py
```

PowerShell: `$env:GIGGY_API_KEY="your-api-key"; python examples/first_speech.py`.
The example reads the account voice catalog, chooses its first available UUID unless you set `GIGGY_VOICE_ID`, submits one Batch request and writes `speech.mp3`. A request can consume credits; check [current pricing](https://giggy.ai/pricing) and the mode contract before running it. No API calls are made by offline tests.

## Use the SDK

```python
import os
from pathlib import Path
from giggy import Giggy

client = Giggy(api_key=os.environ["GIGGY_API_KEY"])
audio = client.speech.create(
    text="Hello from Giggy.",
    voice_id=os.environ["GIGGY_VOICE_ID"],
    mode="batch",  # or "fast"
)
Path("speech.mp3").write_bytes(audio)
```

Obtain `GIGGY_VOICE_ID` from `GET https://giggy.ai/v1/voices` with `Authorization: Bearer $GIGGY_API_KEY`; use `voices[].voice_id`. The SDK sends native REST `xi-api-key` authentication to `/v1/text-to-speech`.

## Stream PCM

```python
with client.speech.stream(text="Streaming from Giggy.", voice_id=os.environ["GIGGY_VOICE_ID"]) as response:
    with open("speech.pcm", "wb") as output:
        while chunk := response.read(65536):
            output.write(chunk)
```

Default output is raw PCM16 little-endian, mono, 24 kHz, without a WAV header. Optional playback: `ffplay -f s16le -ar 24000 -ac 1 speech.pcm`. The response closes on normal completion and caller exceptions. PCM streams must have an audio/pcm content type. HTTP redirects are rejected so credentials and paid requests are not forwarded or replayed. Speed must be 0.25–4 inclusive.

Batch/Fast calls generate an idempotency key automatically. Reuse a caller-provided `idempotency_key` only for an intentional replay of the same operation; streaming sends no idempotency header. The SDK never retries synthesis automatically. `GiggyAPIError.status_code` and `.body` contain provider errors; sanitize them before sharing.

## Check and release

```sh
python -m unittest discover -s tests -v
python -m pip install build twine
python -m build
python -m twine check dist/*
```

See [release readiness and approval steps](docs/RELEASING.md). No package publication is performed by a normal push or tag.

For voice-agent frameworks use [Pipecat's community integration](https://github.com/giggy-ai/pipecat-giggy) or the [LiveKit/Vapi/OpenAI-compatible examples](https://github.com/giggy-ai/giggy-examples). These adapters do not use this Python SDK internally. For AI client tools see [Giggy MCP](https://github.com/giggy-ai/giggy-mcp).
