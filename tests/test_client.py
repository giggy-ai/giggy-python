import json
import unittest
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError

from giggy import Giggy, GiggyAPIError


class Response(BytesIO):
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


if __name__ == "__main__":
    unittest.main()
