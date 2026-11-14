import base64
import io
import logging
import math
from dataclasses import dataclass
from decimal import Decimal

import requests
from django.conf import settings
from tinytag import TinyTag

logger = logging.getLogger(__name__)

BASE_URL = "https://openrouter.ai/api/v1/audio/transcriptions"
TIMEOUT = 45
SUPPORTED_TRANSCRIPTION_MODELS = ("elevenlabs/scribe-v2", "x-ai/grok-stt-2.0", "google/gemini-3.5-transcribe")
DEFAULT_TRANSCRIPTION_MODEL = "elevenlabs/scribe-v2"


class TranscriptionError(Exception):
	pass


@dataclass
class Transcript:
	text: str
	tokens: int
	cost_usd: Decimal | None
	model: str
	usage: dict


def audio_format(data: bytes) -> str | None:
	"""Container from the first bytes; the stored extension is unreliable (Messenger/Instagram audio lands as .bin)."""
	if data[:4] == b"OggS":
		return "ogg"
	if data[4:8] == b"ftyp":  # any brand: tinytag's own sniff only accepts M4A/M4B
		return "m4a"
	if data[:3] == b"ID3" or (len(data) > 1 and data[0] == 0xFF and data[1] & 0xE6 == 0xE2):  # sync + layer III (ADTS AAC has layer 0)
		return "mp3"
	if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
		return "wav"
	return None  # AMR and anything unknown: unmeasurable


def audio_seconds(data: bytes, fmt: str) -> int | None:
	"""Whole seconds, rounded up; None when the duration cannot be read."""
	try:
		duration = TinyTag.get(filename=f"audio.{fmt}", file_obj=io.BytesIO(data)).duration or 0
	except Exception:
		return None
	return math.ceil(duration) if duration > 0 else None


def resolve_transcription_model() -> str:
	from .llm import active_model

	picked = active_model("transcription")
	name = picked[1] if picked else getattr(settings, "TRANSCRIPTION_MODEL", None) or DEFAULT_TRANSCRIPTION_MODEL
	if name in SUPPORTED_TRANSCRIPTION_MODELS:
		return name
	logger.warning("Unsupported transcription model %r, using %s", name, DEFAULT_TRANSCRIPTION_MODEL)
	return DEFAULT_TRANSCRIPTION_MODEL


def transcribe(data: bytes, fmt: str, model: str | None = None) -> Transcript:
	model = model or resolve_transcription_model()
	try:
		resp = requests.post(
			BASE_URL,
			headers={"Authorization": f"Bearer {settings.OPENROUTER_API_KEY}"},
			json={"model": model, "input_audio": {"data": base64.b64encode(data).decode(), "format": fmt}},
			timeout=TIMEOUT,
		)
	except requests.RequestException as exc:
		logger.warning("Transcription request failed: %s", type(exc).__name__)
		raise TranscriptionError(type(exc).__name__) from exc
	if resp.status_code != 200:
		logger.warning("Transcription HTTP %s", resp.status_code)
		raise TranscriptionError(f"HTTP {resp.status_code}")
	try:
		body = resp.json()
	except ValueError as exc:
		logger.warning("Transcription returned invalid JSON (HTTP %s)", resp.status_code)
		raise TranscriptionError(f"HTTP {resp.status_code}: invalid JSON") from exc
	usage = body.get("usage") or {}
	tokens = usage.get("input_tokens") or usage.get("prompt_tokens") or usage.get("total_tokens") or 0
	cost = Decimal(str(usage["cost"])) if usage.get("cost") is not None else None
	return Transcript(body.get("text") or "", int(tokens), cost, model, usage)
