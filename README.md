# Giggy Python SDK

Official Python SDK for the Giggy speech API: text-to-speech and recorded-audio transcription.

> The package is not yet published to PyPI. Install this checkout until its first release is published.

## Install from source

```bash
git clone https://github.com/giggy-ai/giggy-python.git
cd giggy-python
python -m pip install .
```

Requires Python 3.10 or newer. Create a Giggy API key in your Giggy account. The account voice endpoint `GET https://giggy.ai/v1/voices` requires that key as a bearer token; use `voices[].voice_id` as the `voice_id` UUID. Keep API keys server-side.

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

## Transcribe a recording in one SDK call

Prepare a WAV file with signed PCM16, mono, 16 kHz audio. Batch is free for 0.5–60 seconds; Fast accepts 0.5 seconds–12 hours and uses credits. The SDK reads the WAV in bounded chunks instead of loading the whole recording into memory.

```python
import os
from giggy import Giggy

giggy = Giggy(api_key=os.environ["GIGGY_API_KEY"])
print(giggy.transcriptions.transcribe("recording.wav", mode="batch")["text"])
```

`transcribe()` submits once, polls the durable generation, then fetches its signed transcript URL without forwarding your API key. It stops on failure or after a configurable `timeout` (default 30 minutes); its failure message includes the generation UUID so you can inspect the operation. For submit-now/fetch-later workflows, use `submit()`, `get()`, and `result()` separately. A completed generation can have no downloadable result after retention cleanup. For a paid recording, pass `mode="fast"`. Pass `language="en"` when you know the language; omit it for automatic detection. `client_request_id` accepts a caller-supplied UUID but does not make ambiguous upload retries safe. The SDK does not retry automatically.

### Prepare a compatible WAV

The API accepts signed 16-bit PCM, mono, 16 kHz WAV for this path. For MP3, M4A, stereo, or another sample rate, convert before calling the SDK; this is a local preparation step, not another Giggy endpoint:

```bash
ffmpeg -i input.mp3 -ac 1 -ar 16000 -c:a pcm_s16le recording.wav
ffprobe -v error -show_entries stream=codec_name,sample_rate,channels -of default=noprint_wrappers=1 recording.wav
```

Check that `ffprobe` reports `pcm_s16le`, `16000`, and `1`. Use a 0.5–60 second file for Batch. If the server rejects the upload, check the WAV format and duration first; a file renamed `.wav` is not converted. Keep the input recording and the returned generation UUID until the result has been verified.

## Stream transcript revisions from an uploaded WAV

Uploaded-recording Streaming uses the same WAV requirements and is paid. It sends cumulative provisional text over server-sent events. Replace displayed text with each newer `transcript_chunk`; do not append it. Completion must be checked through the durable generation before using the canonical result.

```python
with giggy.transcriptions.stream("recording.wav") as events:
    generation_uuid = None
    for event, data in events:
        if event == "generation_created":
            generation_uuid = data["generation"]["generation_uuid"]
        elif event == "transcript_chunk":
            print("Replace preview:", data["text"])
    if not generation_uuid:
        raise RuntimeError("No admitted generation")
    print("Final:", giggy.transcriptions.result(generation_uuid)["text"])
```

`stream()` parses SSE frames and closes the response when its context exits. An interrupted or failed stream must be reconciled through `get()`.

## Live microphone PCM session

`live()` opens the existing paid Streaming SSE admission. This complete example records ten seconds from a microphone that supports mono 16 kHz PCM16. Install `sounddevice` (`python -m pip install sounddevice`) and allow microphone access first. The API key stays in the server-side Python process. The sender runs concurrently with the event reader, waits for `microphone_ready`, sends 100 ms frames in order, and sends a final empty frame. Run this with `GIGGY_API_KEY` set:

```python
import os
from threading import Thread
import sounddevice as sd
from giggy import Giggy

giggy = Giggy(api_key=os.environ["GIGGY_API_KEY"])
errors = []
sender = None
generation_uuid = None

with giggy.transcriptions.live() as events:
    for event, data in events:
        if event == "generation_created":
            generation_uuid = data["generation"]["generation_uuid"]
        elif event == "microphone_ready":
            ready = dict(data)

            def send_audio():
                try:
                    with sd.RawInputStream(samplerate=16000, channels=1, dtype="int16", blocksize=1600) as microphone:
                        for sequence in range(100):
                            pcm, overflowed = microphone.read(1600)
                            if overflowed:
                                raise RuntimeError("Microphone capture overflowed")
                            giggy.transcriptions.send_live_frame(
                                generation_uuid=ready["generation_uuid"], input_token=ready["input_token"],
                                sequence=sequence, pcm=bytes(pcm))
                        giggy.transcriptions.send_live_frame(
                            generation_uuid=ready["generation_uuid"], input_token=ready["input_token"],
                            sequence=100, pcm=b"", complete=True)
                except Exception as error:
                    errors.append(error)

            sender = Thread(target=send_audio)
            sender.start()
        elif event == "transcript_chunk":
            print("Replace preview:", data["text"])

if sender:
    sender.join()
if errors:
    raise errors[0]
if not generation_uuid:
    raise RuntimeError("No admitted generation")
print("Final:", giggy.transcriptions.result(generation_uuid)["text"])
```

Each frame is at most 32,000 bytes. Await acceptance before incrementing `sequence`. Closing the live stream before completion cancels unfinished work. Do not reconnect or retry an ambiguously accepted frame. See the [full live input contract](https://giggy.ai/docs/speech-api#api-stt-streaming) for readiness, framing, duration, cancellation, and billing. The SDK does not implement microphone capture or turn detection; `sounddevice` is an example-only dependency.

## Related resources

- [JavaScript/TypeScript SDK](https://github.com/giggy-ai/giggy-js)
- [Runnable integration examples](https://github.com/giggy-ai/giggy-examples)
- [Giggy MCP](https://github.com/giggy-ai/giggy-mcp)
