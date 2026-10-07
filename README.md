# Giggy Python SDK

Official Python SDK for the Giggy text-to-speech API.

> The package is not yet published to PyPI. Install from a local checkout for now.

## Install from source

```bash
git clone https://github.com/giggy-ai/giggy-python.git
cd giggy-python
python -m pip install .
```

Requires Python 3.10 or newer. Create a Giggy API key in your Giggy account and obtain a voice UUID from `GET https://giggy.ai/v1/voices` (`voices[].voice_id`). Keep API keys server-side.

```bash
export GIGGY_API_KEY="giggy_sk_..."
export GIGGY_VOICE_ID="your-voice-uuid"
```

## Generate speech

```python
import os
from pathlib import Path
from giggy import Giggy

giggy = Giggy(api_key=os.environ["GIGGY_API_KEY"])
audio = giggy.speech.create(
    text="Hello from Giggy.",
    voice_id=os.environ["GIGGY_VOICE_ID"],
)
Path("speech.mp3").write_bytes(audio)
```

## Stream speech

```python
with giggy.speech.stream(
    text="Streaming from Giggy.",
    voice_id=os.environ["GIGGY_VOICE_ID"],
) as response:
    with open("speech.pcm", "wb") as output:
        while chunk := response.read(65536):
            output.write(chunk)
```

Streaming requests do not send an idempotency header. The SDK has no automatic retries.

## Related resources

- [JavaScript/TypeScript SDK](https://github.com/giggy-ai/giggy-js)
- [Runnable integration examples](https://github.com/GRQDigitalCapital/giggy-examples)
- [Giggy MCP](https://github.com/GRQDigitalCapital/giggy-mcp)
