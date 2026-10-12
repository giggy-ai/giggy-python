import importlib.util
import json
import os
import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location(
    "first_speech", Path(__file__).parents[1] / "examples/first_speech.py"
)
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)


class Response(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class ExampleTests(unittest.TestCase):
    def test_missing_key_does_not_send(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(example, "urlopen") as request,
        ):
            with self.assertRaises(SystemExit):
                example.main()
            request.assert_not_called()

    def test_catalog_uuid_drives_one_speech_call(self):
        provider = Mock()
        provider.speech.create.return_value = b"audio"
        with TemporaryDirectory() as tmp:
            previous = os.getcwd()
            try:
                os.chdir(tmp)
                with (
                    patch.dict(os.environ, {"GIGGY_API_KEY": "fake-key"}, clear=True),
                    patch.object(example, "Giggy", return_value=provider),
                    patch.object(
                        example,
                        "urlopen",
                        return_value=Response(
                            json.dumps(
                                {"voices": [{"voice_id": "returned-uuid"}]}
                            ).encode()
                        ),
                    ),
                ):
                    example.main()
                provider.speech.create.assert_called_once_with(
                    text="Hello from Giggy.", voice_id="returned-uuid"
                )
                self.assertEqual(Path("speech.mp3").read_bytes(), b"audio")
            finally:
                os.chdir(previous)
