"""Cohere embeddings and rerank through OpenRouter."""
import base64
import io
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

import requests
from django.conf import settings
from PIL import Image

BASE_URL = "https://openrouter.ai/api/v1"
EMBED_MODEL = "cohere/embed-5-fast"
RERANK_MODEL = "cohere/rerank-5-fast"
TIMEOUT = 60


class EmbeddingError(Exception):
	pass


@dataclass
class Embedded:
	vectors: list[list[float]]
	tokens: int
	cost_usd: Decimal | None
	model: str


@dataclass
class Reranked:
	scores: list[float]
	tokens: int
	cost_usd: Decimal | None
	model: str


def _post(path: str, payload: dict) -> dict:
	try:
		resp = requests.post(
			f"{BASE_URL}/{path}",
			headers={"Authorization": f"Bearer {settings.OPENROUTER_API_KEY}"},
			json=payload,
			timeout=TIMEOUT,
		)
	except requests.RequestException as exc:
		raise EmbeddingError(f"{path} request failed: {exc}") from exc
	if resp.status_code != 200:
		raise EmbeddingError(f"{path} returned {resp.status_code}: {resp.text[:200]}")
	try:
		return resp.json()
	except ValueError as exc:
		raise EmbeddingError(f"{path} returned invalid JSON") from exc


def _usage(body: dict) -> tuple[int, Decimal | None]:
	usage = body.get("usage") or {}
	tokens = usage.get("prompt_tokens", usage.get("total_tokens", 0)) or 0
	cost = usage.get("cost")
	return int(tokens), (Decimal(str(cost)) if cost is not None else None)


def _post_embeddings(content_input: list, kind: str) -> Embedded:
	body = _post("embeddings", {"model": EMBED_MODEL, "input": content_input, "input_type": kind})
	try:
		vectors = [d["embedding"] for d in sorted(body["data"], key=lambda d: d["index"])]
		tokens, cost = _usage(body)
	except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
		raise EmbeddingError(f"malformed embeddings response: {exc!r}") from exc
	if len(vectors) != len(content_input):
		raise EmbeddingError(f"expected {len(content_input)} vectors, got {len(vectors)}")
	return Embedded(vectors, tokens, cost, EMBED_MODEL)


def embed_texts(texts: list[str], kind: Literal["query", "document"]) -> Embedded:
	if not texts:
		return Embedded([], 0, None, EMBED_MODEL)
	return _post_embeddings(texts, "search_query" if kind == "query" else "search_document")


def embed_image(img: Image.Image) -> Embedded:
	buf = io.BytesIO()
	img.convert("RGB").save(buf, format="JPEG", quality=85)
	url = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
	content = [{"type": "image_url", "image_url": {"url": url}}]
	return _post_embeddings([{"content": content}], "image")


def rerank_scores(query: str, texts: list[str]) -> Reranked:
	body = _post("rerank", {"model": RERANK_MODEL, "query": query, "documents": texts})
	try:
		scores = [r["relevance_score"] for r in sorted(body["results"], key=lambda r: r["index"])]
		tokens, cost = _usage(body)
	except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
		raise EmbeddingError(f"malformed rerank response: {exc!r}") from exc
	if len(scores) != len(texts):
		raise EmbeddingError(f"expected {len(texts)} scores, got {len(scores)}")
	return Reranked(scores, tokens, cost, RERANK_MODEL)
