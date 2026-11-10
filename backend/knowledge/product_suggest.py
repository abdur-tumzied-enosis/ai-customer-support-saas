"""AI suggestions for a source's column mapping. Suggestions only: the user accepts each one, nothing is applied here."""
import json
import re
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from agents.llm import get_llm
from usage.utils import llm_pricing, track_token_usage

from .product_sources import TARGETS

MAX_COLUMNS = 60
MAX_SAMPLE_ROWS = 3
MAX_EXAMPLE_CHARS = 80
SUGGEST_MAX_TOKENS = 1024
# Columns whose name suggests personal or payment data: the model sees the name and the type, never the values
_SENSITIVE = re.compile(r"e-?mail|phone|mobile|\btel\b|address|password|token|secret|card|cvv|passport|national|\bnid\b|birth|\bdob\b", re.I)
_NUMBER = re.compile(r"^-?[\d,]*\.?\d+$")
_BOOL = {"true", "false", "yes", "no", "y", "n", "in stock", "out of stock", "available", "unavailable"}

SYSTEM = (
	"You map the columns of a product list to product fields. Use only the column names given, copied exactly. "
	"Targets: name (product name), external_id (product ID or code), sku, price (number), description, "
	"available (in stock yes/no), variant_label (size, colour or option), product_url (link to the product page), "
	"image_url (link to a product photo). Map a column only when its name and examples fit the target. "
	"Give each suggestion a confidence from 0 to 1 and say briefly why. Lower the confidence when unsure; "
	"leave out targets with no fitting column."
)


class Suggestion(BaseModel):
	target: str = Field(description="one of the target names")
	column: str
	confidence: float = Field(ge=0, le=1)
	reason: str = ""


class SuggestionList(BaseModel):
	suggestions: list[Suggestion] = Field(default_factory=list)


def _infer(values: list[str]) -> str:
	if not values:
		return "empty"
	if all(_NUMBER.match(v.replace(" ", "")) for v in values):
		return "number"
	if all(urlparse(v).scheme in ("http", "https") and urlparse(v).netloc for v in values):
		return "url"
	if all(v.lower() in _BOOL for v in values):
		return "boolean"
	return "text"


def column_profile(headers: list[str], sample: list[dict]) -> list[dict]:
	"""What the model may see: name, inferred type and up to three short examples. Sensitive columns: name and type only."""
	profile = []
	for h in headers[:MAX_COLUMNS]:
		name = str(h)[:100]
		if _SENSITIVE.search(name):
			profile.append({"name": name, "type": "withheld"})
			continue
		values = [str(r.get(h, "")).strip() for r in sample[:MAX_SAMPLE_ROWS] if str(r.get(h, "")).strip()]
		kind = _infer(values)
		if kind == "url":  # a link's query string can carry a key: show only where it points
			examples = [f"{urlparse(v).netloc}{urlparse(v).path}"[:MAX_EXAMPLE_CHARS] for v in values[:MAX_SAMPLE_ROWS]]
		else:
			examples = [v[:MAX_EXAMPLE_CHARS] for v in values[:MAX_SAMPLE_ROWS]]
		profile.append({"name": name, "type": kind, "examples": examples})
	return profile


def _track(project_id: str, kb_id: str, raw) -> None:
	usage = getattr(raw, "usage_metadata", None) or {}
	meta = {"source": "product_mapping_suggest", "kb_id": kb_id}
	pricing = llm_pricing([raw])
	track_token_usage(project_id, "chat_in", usage.get("input_tokens", 0), meta, pricing=pricing["chat_in"])
	track_token_usage(project_id, "chat_out", usage.get("output_tokens", 0), meta, pricing=pricing["chat_out"])


def suggest_mapping(headers: list[str], sample: list[dict], project_id: str, kb_id: str) -> list[dict]:
	"""Validated suggestions, best first. Each target and each column appears at most once."""
	columns = column_profile(headers, sample)
	llm = get_llm(role="memory", max_tokens=SUGGEST_MAX_TOKENS)
	out = llm.with_structured_output(SuggestionList, include_raw=True, method="function_calling").invoke(
		[("system", SYSTEM), ("human", json.dumps({"columns": columns}, ensure_ascii=False))]
	)
	_track(project_id, kb_id, out.get("raw"))
	if out.get("parsed") is None:
		raise ValueError(f"model returned no suggestions: {out.get('parsing_error')}")
	known = set(headers)
	ranked = sorted(out["parsed"].suggestions, key=lambda s: -s.confidence)
	chosen, used_targets, used_columns = [], set(), set()
	for s in ranked:
		if s.target not in TARGETS or s.column not in known or s.target in used_targets or s.column in used_columns:
			continue  # the model named something that is not in the file or not a target: drop it
		used_targets.add(s.target)
		used_columns.add(s.column)
		chosen.append({"target": s.target, "column": s.column, "confidence": round(s.confidence, 2), "reason": s.reason[:200]})
	return chosen
