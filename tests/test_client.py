import json
import time
import unittest
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError

from giggy import Giggy, GiggyAPIError


class Response(BytesIO):
    status = 202

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class GiggyClientTests(unittest.TestCase):
    def setUp(self):
        self.client = Giggy(api_key="test-key")

    def test_missing_api_key(self):
        with self.assertRaisesRegex(ValueError, "api_key"):
            Giggy(api_key=" ")

    def _check_create(self, mode="batch", idempotency_key=None):
        response = Response(b"audio")
        with patch("giggy.client.urlopen", return_value=response) as mocked:
            result = self.client.speech.create(
                text="hello", voice_id="voice", mode=mode,
                idempotency_key=idempotency_key,
            )
        req = mocked.call_args.args[0]
        payload = json.loads(req.data)
        return result, req, payload

    def test_batch_request_mapping_and_automatic_idempotency(self):
        result, req, payload = self._check_create()
        self.assertEqual(result, b"audio")
        self.assertEqual(req.full_url, "https://giggy.ai/v1/text-to-speech")
        self.assertEqual(payload["mode"], "batch")
        self.assertEqual(payload["voice_id"], "voice")
        self.assertTrue(req.get_header("Idempotency-key"))

    def test_fast_request_mapping(self):
        _, _, payload = self._check_create(mode="fast")
        self.assertEqual(payload["mode"], "fast")

    def test_supplied_idempotency_key(self):
        _, req, _ = self._check_create(idempotency_key="my-key")
        self.assertEqual(req.get_header("Idempotency-key"), "my-key")

    def test_streaming_request_has_no_idempotency_header_and_closes(self):
        response = Response(b"pcm")
        with patch("giggy.client.urlopen", return_value=response) as mocked:
            with self.client.speech.stream(text="hello", voice_id="voice") as body:
                self.assertEqual(body.read(), b"pcm")
            req = mocked.call_args.args[0]
        self.assertEqual(json.loads(req.data)["mode"], "streaming")
        self.assertIsNone(req.get_header("Idempotency-key"))
        self.assertTrue(response.closed)

    def test_http_400_raises_structured_error(self):
        error = HTTPError("url", 400, "bad request", {}, BytesIO(b"invalid"))
        with patch("giggy.client.urlopen", side_effect=error):
            with self.assertRaises(GiggyAPIError) as raised:
                self.client.speech.create(text="hello", voice_id="voice")
        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("invalid", raised.exception.body)

    def test_transcription_submission_streams_wav_and_maps_options(self):
        from pathlib import Path
        path = Path("pyproject.toml")
        generation_id = "00000000-0000-4000-8000-000000000001"
        captured = {}

        def respond(request, timeout):
            captured["url"] = request.full_url
            captured["headers"] = request.headers
            captured["body"] = b"".join(request.data)
            return Response(json.dumps({"generation": {"generation_uuid": generation_id, "status": "queued"}}).encode())

        with patch("giggy.client.urlopen", side_effect=respond):
            admission = self.client.transcriptions.submit(
                path, mode="fast", language="en", client_request_id=generation_id,
            )
        self.assertEqual(admission["generation"]["generation_uuid"], generation_id)
        self.assertIn("/audio/transcriptions?mode=fast&language=en&client_request_id=", captured["url"])
        self.assertEqual(captured["headers"]["Xi-api-key"], "test-key")
        self.assertEqual(captured["body"], path.read_bytes())

    def test_transcription_result_requires_durable_success_and_unsigned_artifact_request(self):
        generation_id = "00000000-0000-4000-8000-000000000001"
        artifact_url = "https://example.test/transcript.json"
        requests = []

        def respond(request, timeout):
            requests.append(request)
            if len(requests) == 1:
                return Response(json.dumps({"generation": {
                    "generation_uuid": generation_id, "status": "completed", "result": {"url": artifact_url}
                }}).encode())
            return Response(b'{"text":"hello","languages":["English"]}')

        with patch("giggy.client.urlopen", side_effect=respond):
            result = self.client.transcriptions.result(generation_id)
        self.assertEqual(result["text"], "hello")
        self.assertEqual(requests[0].get_header("Xi-api-key"), "test-key")
        self.assertIsNone(requests[1].get_header("Xi-api-key"))

    def test_transcribe_submits_once_and_waits_for_durable_result(self):
        generation_id = "00000000-0000-4000-8000-000000000001"
        responses = iter([
            Response(json.dumps({"generation": {"generation_uuid": generation_id, "status": "queued"}}).encode()),
            Response(json.dumps({"generation": {"generation_uuid": generation_id, "status": "processing"}}).encode()),
            Response(json.dumps({"generation": {"generation_uuid": generation_id, "status": "completed"}}).encode()),
            Response(json.dumps({"generation": {"generation_uuid": generation_id, "status": "completed", "result": {"url": "https://example.test/transcript.json"}}}).encode()),
            Response(b'{"text":"ready"}'),
        ])
        with patch("giggy.client.urlopen", side_effect=lambda request, timeout: next(responses)) as opened, patch("giggy.client.time.sleep") as slept:
            result = self.client.transcriptions.transcribe("pyproject.toml", poll_interval=0.1)
        self.assertEqual(result["text"], "ready")
        self.assertEqual(opened.call_count, 5)
        self.assertEqual(slept.call_count, 1)
        started = time.monotonic()
        with patch.object(self.client.transcriptions, "submit", side_effect=lambda *args, **kwargs: time.sleep(0.15)) as submitted:
            with self.assertRaisesRegex(TimeoutError, "generation UUID not received"):
                self.client.transcriptions.transcribe("pyproject.toml", timeout=0.02)
        self.assertLess(time.monotonic() - started, 0.12)
        self.assertEqual(submitted.call_count, 1)
        for invalid in (float("nan"), float("inf"), 0, -1):
            with self.assertRaises(ValueError):
                self.client.transcriptions.transcribe("pyproject.toml", timeout=invalid)

    def test_transcription_stream_parses_split_events_and_revisions(self):
        path = "pyproject.toml"
        response = Response(b'event: transcript_chunk\r\ndata: {"text":"one","replace":true}\r\n\r\nevent: transcript_chunk\r\ndata: {"text":"one two","replace":true}\r\n\r\n')
        response.status = 200
        response.headers = {"content-type": "text/event-stream"}
        with patch("giggy.client.urlopen", return_value=response):
            with self.client.transcriptions.stream(path) as events:
                items = list(events)
        self.assertEqual([item[1]["text"] for item in items], ["one", "one two"])
        self.assertTrue(response.closed)

    def test_live_transcription_admission_and_ordered_frame_request(self):
        generation_id = "00000000-0000-4000-8000-000000000001"
        token = "a" * 64
        calls = []
        event_body = (f'event: microphone_ready\ndata: {{"generation_uuid":"{generation_id}","attempt":1,"input_token":"{token}"}}\n\n').encode()
        live_response = Response(event_body)
        live_response.status = 200
        live_response.headers = {"content-type": "text/event-stream"}

        def respond(request, timeout):
            calls.append(request)
            return live_response if len(calls) == 1 else Response(b'{"ok":true}')

        with patch("giggy.client.urlopen", side_effect=respond):
            with self.client.transcriptions.live(client_request_id=generation_id) as events:
                event, data = next(events)
                self.assertEqual(event, "microphone_ready")
                self.client.transcriptions.send_live_frame(
                    generation_uuid=data["generation_uuid"], input_token=data["input_token"],
                    sequence=0, pcm=b"\x01\x00" * 8000, complete=True,
                )
        self.assertEqual(json.loads(calls[0].data)["microphone"], True)
        self.assertEqual(calls[1].full_url, "https://giggy.ai/api/generations/" + generation_id + "/stt-input")
        self.assertIsNone(calls[1].get_header("Xi-api-key"))
        self.assertEqual(json.loads(calls[1].data)["sequence"], 0)
        self.assertTrue(live_response.closed)


if __name__ == "__main__":
    unittest.main()
