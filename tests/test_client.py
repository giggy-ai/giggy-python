import json
import unittest
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError

from giggy import Giggy, GiggyAPIError


class Response(BytesIO):
    headers = {"Content-Type": "audio/pcm"}

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
                text="hello",
                voice_id="voice",
                mode=mode,
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
        self.assertTrue(error.fp.closed)

    def test_invalid_mode_and_speed_never_send(self):
        with patch("giggy.client.urlopen") as mocked:
            with self.assertRaises(ValueError):
                self.client.speech.create(
                    text="hello", voice_id="voice", mode="streaming"
                )
            for speed in [0, 5, float("nan"), float("inf")]:
                with self.assertRaises(ValueError):
                    self.client.speech.create(
                        text="hello", voice_id="voice", speed=speed
                    )
            mocked.assert_not_called()

    def test_stream_type_and_exception_close_response(self):
        response = Response(b"private upstream data")
        response.headers = {"Content-Type": "application/json"}
        with patch("giggy.client.urlopen", return_value=response):
            with self.assertRaisesRegex(ValueError, "PCM"):
                with self.client.speech.stream(text="hello", voice_id="voice"):
                    self.fail("Bad content type must not be returned")
        self.assertTrue(response.closed)
        response = Response(b"pcm")
        with patch("giggy.client.urlopen", return_value=response):
            with self.assertRaises(RuntimeError):
                with self.client.speech.stream(text="hello", voice_id="voice"):
                    raise RuntimeError("caller stopped")
        self.assertTrue(response.closed)

    def test_redirect_does_not_replay_credentials(self):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                requests.append(self.path)
                self.send_response(302)
                self.send_header("Location", "/credential-target")
                self.end_headers()

            def do_GET(self):
                requests.append(self.path)
                self.send_response(200)
                self.end_headers()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with self.assertRaises(GiggyAPIError):
                Giggy(
                    api_key="test-key",
                    base_url=f"http://127.0.0.1:{server.server_port}",
                ).speech.create(text="hi", voice_id="voice")
            self.assertEqual(requests, ["/text-to-speech"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
