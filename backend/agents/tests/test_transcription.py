import base64
import io
import json
import wave
from decimal import Decimal
from pathlib import Path
from unittest import mock

import requests

from django.core.cache import cache
from django.test import TestCase, override_settings

from agents import transcription
from agents.models import AIModel, ModelConfig

FIXTURES = Path(__file__).parent / "fixtures"
LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "transcription-tests"}}


def wav_bytes(seconds: float, rate: int = 8000) -> bytes:
	buf = io.BytesIO()
	with wave.open(buf, "wb") as w:
		w.setnchannels(1)
		w.setsampwidth(2)
		w.setframerate(rate)
		w.writeframes(b"\x00\x00" * int(rate * seconds))
	return buf.getvalue()


class AudioFormatTest(TestCase):
	def test_recorded_fixtures(self):
		assert transcription.audio_format((FIXTURES / "voice.ogg").read_bytes()) == "ogg"
		assert transcription.audio_format((FIXTURES / "voice_messenger.mp3").read_bytes()) == "mp3"

	def test_any_mp4_brand_is_m4a(self):
		# synthetic header: any ftyp brand counts (a Messenger clip may say isom/mp42); the real M4A-brand file is tested below
		assert transcription.audio_format(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 20) == "m4a"

	def test_real_m4a_fixture(self):
		assert transcription.audio_format((FIXTURES / "voice.m4a").read_bytes()) == "m4a"

	def test_mp3_and_wav(self):
		assert transcription.audio_format(b"ID3\x04" + b"\x00" * 20) == "mp3"
		assert transcription.audio_format(b"\xff\xfb\x90\x00" + b"\x00" * 20) == "mp3"
		assert transcription.audio_format(wav_bytes(1)) == "wav"

	def test_adts_aac_is_not_mp3(self):
		assert transcription.audio_format(b"\xff\xf1\x50\x80" + b"\x00" * 20) is None

	def test_garbage_empty_and_amr_are_none(self):
		for data in (b"not audio", b"", b"#!AMR\n" + b"\x00" * 40):
			assert transcription.audio_format(data) is None


class AudioSecondsTest(TestCase):
	def test_reads_recorded_fixtures(self):
		for name, fmt in (("voice.ogg", "ogg"), ("voice_messenger.mp3", "mp3")):
			seconds = transcription.audio_seconds((FIXTURES / name).read_bytes(), fmt)
			assert seconds is not None, name
			assert 1 <= seconds <= 10, name

	def test_real_m4a_fixture_is_3_24s_rounded_up(self):
		assert transcription.audio_seconds((FIXTURES / "voice.m4a").read_bytes(), "m4a") == 4

	def test_rounds_up_to_a_whole_second(self):
		assert transcription.audio_seconds(wav_bytes(2.1), "wav") == 3
		assert transcription.audio_seconds(wav_bytes(2.0), "wav") == 2

	def test_unreadable_is_none(self):
		assert transcription.audio_seconds(b"not audio", "ogg") is None
		assert transcription.audio_seconds(b"", "m4a") is None


@override_settings(CACHES=LOCMEM)
class ResolveModelTest(TestCase):
	def setUp(self):
		cache.clear()

	def test_default_is_scribe(self):
		assert transcription.resolve_transcription_model() == "elevenlabs/scribe-v2"

	def test_model_config_wins(self):
		row = AIModel.objects.get(provider="openrouter", name="x-ai/grok-stt-2.0")
		ModelConfig.objects.update_or_create(pk=1, defaults={"transcription_model": row})
		assert transcription.resolve_transcription_model() == "x-ai/grok-stt-2.0"

	@override_settings(TRANSCRIPTION_MODEL="nope/unknown")
	def test_unsupported_model_falls_back(self):
		assert transcription.resolve_transcription_model() == "elevenlabs/scribe-v2"


@override_settings(CACHES=LOCMEM)
class TranscribeTest(TestCase):
	def setUp(self):
		cache.clear()

	def _post(self, body):
		resp = mock.Mock(status_code=200, text="")
		resp.json.return_value = body
		return mock.patch("agents.transcription.requests.post", return_value=resp)

	def test_posts_base64_with_resolved_model_and_format(self):
		data = (FIXTURES / "voice.ogg").read_bytes()
		with self._post({"text": "hello", "usage": {"cost": 0.0012, "prompt_tokens": 7}}) as post:
			out = transcription.transcribe(data, "ogg")
		payload = post.call_args.kwargs["json"]
		assert payload["model"] == "elevenlabs/scribe-v2"
		assert payload["input_audio"] == {"data": base64.b64encode(data).decode(), "format": "ogg"}
		assert (out.text, out.tokens, out.cost_usd) == ("hello", 7, Decimal("0.0012"))

	def test_parses_each_captured_response(self):
		for name in ("scribe-v2", "grok-stt-2.0", "gemini-3.5-transcribe"):
			body = json.loads((FIXTURES / f"openrouter_{name}.json").read_text())
			with self._post(body):
				out = transcription.transcribe(b"x", "ogg")
			assert out.text.strip(), name
			assert out.cost_usd is not None and out.cost_usd > 0, name
			assert out.tokens >= 0, name

	def test_missing_cost_is_none(self):
		with self._post({"text": "hi", "usage": {"prompt_tokens": 3}}):
			assert transcription.transcribe(b"x", "mp3").cost_usd is None

	def test_non_200_raises(self):
		resp = mock.Mock(status_code=500, text="boom")
		with mock.patch("agents.transcription.requests.post", return_value=resp):
			with self.assertRaises(transcription.TranscriptionError):
				transcription.transcribe(b"x", "mp3")

	def test_each_supported_model_is_sent_as_given(self):
		for model in transcription.SUPPORTED_TRANSCRIPTION_MODELS:
			with self._post({"text": "ok", "usage": {}}) as post:
				transcription.transcribe(b"x", "ogg", model=model)
			assert post.call_args.kwargs["json"]["model"] == model

	def test_malformed_200_replies_raise_transcription_error(self):
		for body in ([], "str", {"text": "x", "usage": [1]}, {"text": "x", "usage": {"cost": "abc"}}, {"text": "x", "usage": {"input_tokens": "many"}}):
			with self._post(body):
				with self.assertRaises(transcription.TranscriptionError, msg=repr(body)):
					transcription.transcribe(b"x", "ogg")

	def test_null_usage_is_fine(self):
		with self._post({"text": "hi", "usage": None}):
			out = transcription.transcribe(b"x", "ogg")
		assert (out.tokens, out.cost_usd) == (0, None)

	def test_non_json_200_raises(self):
		resp = mock.Mock(status_code=200, text="<html>")
		resp.json.side_effect = ValueError("no json")
		with mock.patch("agents.transcription.requests.post", return_value=resp):
			with self.assertRaises(transcription.TranscriptionError):
				transcription.transcribe(b"x", "ogg")

	def test_request_exception_raises(self):
		with mock.patch("agents.transcription.requests.post", side_effect=requests.Timeout("slow")):
			with self.assertRaises(transcription.TranscriptionError):
				transcription.transcribe(b"x", "ogg")

	def test_response_body_is_never_logged(self):
		marker = "SECRET-MARKER-123"
		bad = mock.Mock(status_code=500, text=marker)
		bad.json.return_value = {"error": marker}
		malformed = mock.Mock(status_code=200, text=marker)
		malformed.json.return_value = {"text": marker, "usage": {"cost": marker}}
		for resp in (bad, malformed):
			with mock.patch("agents.transcription.requests.post", return_value=resp), 			     mock.patch("agents.transcription.logger") as log:
				with self.assertRaises(transcription.TranscriptionError):
					transcription.transcribe(b"x", "ogg")
			assert log.mock_calls
			assert marker not in str(log.mock_calls)
