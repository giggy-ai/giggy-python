# Giggy Python SDK

Official Python SDK for Giggy text-to-speech, recorded-audio speech-to-text and short live microphone transcription sessions.

Start with [one-call recording transcription](#transcribe-a-recording-in-one-sdk-call), [uploaded-WAV transcript revisions](#stream-transcript-revisions-from-an-uploaded-wav), or [live microphone capture](#live-microphone-pcm-session). Uploaded-recording Streaming and live microphone Streaming use different input contracts. Live input is PCM16 mono 16 kHz, 0.5–30 seconds per session.

## Install

```bash
python -m pip install giggy==0.1.3
```

Version 0.1.3 is published on [PyPI](https://pypi.org/project/giggy/0.1.3/) with the earlier live transport. Install source below for the current WebSocket live example; that change is not yet published to PyPI.

Choose another integration in the [developer tool guide](https://giggy.ai/docs/speech-api#api-developer-tools): [JavaScript/TypeScript](https://github.com/giggy-ai/giggy-js), [Pipecat TTS/live STT](https://github.com/giggy-ai/pipecat-giggy), [n8n TTS](https://github.com/giggy-ai/n8n-nodes-giggy), or [MCP speech/transcription tools](https://github.com/giggy-ai/giggy-mcp). For executable recording and TTS walkthroughs, use [giggy-examples](https://github.com/giggy-ai/giggy-examples).

## Install from source

```bash
git clone https://github.com/giggy-ai/giggy-python.git
cd giggy-python
python -m pip install .
```

Requires Python 3.10 or newer. Create a Giggy API key in your Giggy account. The account voice endpoint `GET https://api.giggy.ai/v1/voices` requires that key as a bearer token; use `voices[].voice_id` as the `voice_id` UUID. Keep API keys server-side.

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

This checkout adds the WebSocket transport; it is not yet a package-registry release. Install this checkout to use this example. Published versions retain their earlier live transport. Recording `stream()` remains explicit HTTPS/SSE. New API requests default to `https://api.giggy.ai/v1`; existing `https://giggy.ai/v1` clients remain supported. Live sessions use one authenticated socket, binary PCM and one JSON `stop`, with no per-frame HTTP requests. The application supplies turn boundaries. The server assigns the generation ID; omit client request IDs. The SDK checks the final input ACK, transcript identity and saved text/duration before successful completion.

`live()` opens the paid live WebSocket session at `wss://api.giggy.ai/v1/audio/transcriptions`. This complete example records ten seconds from a microphone that supports mono 16 kHz PCM16. Install `sounddevice` (`python -m pip install sounddevice`) and allow microphone access first. The API key stays in the server-side Python process. The sender runs concurrently with the event reader, waits for `microphone_ready`, sends 100 ms frames in order, and sends a final empty frame. Run this with `GIGGY_API_KEY` set:

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
                                generation_uuid=ready["generation_uuid"],
                                sequence=sequence, pcm=bytes(pcm))
                        giggy.transcriptions.send_live_frame(
                            generation_uuid=ready["generation_uuid"],
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

Each frame is at most 32,000 bytes. Await each local socket send before incrementing `sequence`. Closing the live stream before completion cancels unfinished work. Do not reconnect or retry an ambiguously accepted frame. See the [full live input contract](https://giggy.ai/docs/speech-api#api-stt-streaming) for readiness, framing, duration, cancellation, and billing. The SDK does not implement microphone capture or turn detection; `sounddevice` is an example-only dependency.

## Related resources

- [JavaScript/TypeScript SDK](https://github.com/giggy-ai/giggy-js)
- [Runnable integration examples](https://github.com/giggy-ai/giggy-examples)
- [Giggy MCP](https://github.com/giggy-ai/giggy-mcp)

## Transcription method reference

All methods belong to `giggy.transcriptions`. SDK defaults select free Batch; the REST endpoint defaults to paid Fast.

| Method | Inputs and defaults | Returns |
| --- | --- | --- |
| `transcribe(audio_path, *, mode="batch", language=None, client_request_id=None, poll_interval=2.5, timeout=1800)` | Local WAV; Batch or Fast; finite positive seconds | Final transcript dictionary after one submission |
| `submit(audio_path, *, mode="batch", language=None, client_request_id=None, timeout=1800)` | Local WAV; one admission; network inactivity timeout in seconds | Admission dictionary containing `generation.generation_uuid` |
| `get(generation_uuid, *, timeout=30)` | UUID; one status request | Generation dictionary with `status` and optional `result.url` |
| `result(generation_uuid, *, timeout=30)` | A completed generation; status and signed-artifact retrieval share the timeout budget | Transcript dictionary; key is not sent to the artifact host |
| `stream(audio_path, *, language=None, client_request_id=None)` | Completed WAV; paid Streaming | Context manager yielding `(event, data)` pairs |
| `live(*, language=None, timeout=150)` | Paid live admission; automatic language detection | Context manager yielding the same event pairs |
| `send_live_frame(*, generation_uuid, sequence, pcm, complete=False)` | Ordered PCM16 bytes; maximum 32,000 bytes, even length; empty only for completion | `None` after local socket send; raises on failure |

`transcribe(timeout=...)` starts its wall-clock deadline before upload and bounds waiting for submission, status and artifact I/O. Python uses one bounded I/O thread for this method; on timeout it stops waiting and cancels pending calls, while an in-flight blocking request may finish later. A timeout does not cancel an admitted recording. The exception includes its UUID when admission was received. Individual `submit/get/result` network timeouts also bound socket inactivity; use `transcribe` when you need a deadline for the complete workflow.

The existing events are `generation_created` (admitted generation), `microphone_ready` (UUID, attempt and private input token), `transcript_chunk` (cumulative text, sequence, replace=true and complete), and `generation_updated` (status and optional generation). An event's `complete` flag is not durable generation completion. After EOF, inspect `get()` and fetch `result()` only after `status=completed`.
These pairs are annotated with the exported `TranscriptionEvent` union and `MicrophoneReady`, `TranscriptChunk`, `GenerationUpdated`, and `TranscriptionAdmission` TypedDict payloads. Unknown named events raise an explicit contract error; heartbeat comments are ignored.

## Troubleshooting and migration

### Migrating OpenAI speech and transcription

OpenAI-compatible **TTS** uses `https://api.giggy.ai/v1/audio/speech`. Keep the OpenAI speech client, point its base URL to `https://api.giggy.ai/v1`, use your Giggy API key, set `model="giggyspeech"`, and use a Giggy voice UUID. Choose a supported response format and disable automatic synthesis retries. See the [existing OpenAI-client example](https://github.com/giggy-ai/giggy-examples/tree/main/node/openai-compatible).

**STT is a separate contract.** Replace an OpenAI `audio.transcriptions.create(...)` call with `giggy.transcriptions.transcribe("recording.wav", mode="batch")` and consume `result["text"]`. A base-URL change alone does not migrate STT: Giggy's REST input is raw WAV with `xi-api-key`, rather than an OpenAI multipart upload, and its Batch/Fast admission returns a generation UUID before the final transcript. The SDK handles that existing lifecycle. SDK transcription defaults to Batch; raw REST defaults to paid Fast. Convert input locally to PCM16 mono 16 kHz WAV, and remove unsupported transcription options rather than inventing equivalent fields.

| Symptom | Action |
| --- | --- |
| HTTP 401/403 | Check the key and its owning Personal space or Workspace. Never substitute another user's key. |
| HTTP 429 | Stop new submissions and honor the server response. An unambiguous rejection is different from an upload whose acceptance is unknown. |
| Timeout before a UUID | Keep the original WAV and request identity; inspect History before another submission. `client_request_id` is not an idempotency guarantee. |
| Timeout or SSE EOF after a UUID | Call `get(uuid)`; the recording may still be queued or processing. Do not upload it again automatically. |
| Completed generation without a result URL, or expired signed URL | Read current status again using the owning key. Use a refreshed result URL if one exists; retention/deletion can make the content permanently unavailable. |
| Live sender fails | Exit the live context to close its WebSocket connection, then reconcile the UUID. Closing unfinished live input invokes canonical cancellation; closing uploaded-WAV SSE does not. |
| Empty final text | The valid recording contained no detected speech. Check the recording; do not invent a transcript. |

HTTP failures raise `GiggyAPIError` with status and raw response body. Invalid parameters raise `ValueError`. Deadline expiry raises `TimeoutError`; malformed responses, unavailable results and durable failed/cancelled/admission_failed operations raise `RuntimeError`. Avoid logging live input tokens or signed URLs.

When migrating from AssemblyAI, replace `transcriber.transcribe(audio)` with `giggy.transcriptions.transcribe(wav_path)` and `transcript.text` with `result["text"]`. Convert MP3/video/URL inputs locally to PCM16 mono 16 kHz WAV before submitting. Speaker labels, word times, confidence, subtitle exports and speech-analysis options have no equivalent in this transcript contract; remove those options and explain the difference to your application users. Map provisional turn text to cumulative `transcript_chunk` revisions, and explicitly finish each live input within Giggy's 30-second audio/35-second readiness limit.

For complete commands and recording-to-text applications, see the [Python recording walkthrough](https://github.com/giggy-ai/giggy-examples/tree/main/python/basic-tts#transcribe-a-recording) and [framework STT guide](https://github.com/giggy-ai/giggy-examples#stt-in-voice-agent-frameworks).
