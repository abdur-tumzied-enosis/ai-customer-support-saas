from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings
from django.db import transaction

from .models import AIModel

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
_PER_MTOK = Decimal(1_000_000)
_MAX_MTOK = Decimal(10) ** 6  # the price fields are max_digits=12, decimal_places=6: at most 999999.999999


def _mtok(value):
	"""OpenRouter's per-token USD string → USD per 1M tokens. Missing, negative (dynamic routers, -1), NaN/Infinity,
	non-numeric or too large for the field → None."""
	try:
		d = Decimal(str(value))
	except InvalidOperation:
		return None
	if not d.is_finite() or d < 0:
		return None
	per_mtok = d * _PER_MTOK
	return None if per_mtok >= _MAX_MTOK else per_mtok.quantize(Decimal("0.000001"))


def _entry_prices(model):
	pricing = model.get("pricing") or {}
	return _mtok(pricing.get("prompt")), _mtok(pricing.get("completion"))


def _entry_modalities(model):
	"""OpenRouter's input_modalities, e.g. ["text", "image"]. A missing architecture key means not capable (fail closed)."""
	return (model.get("architecture") or {}).get("input_modalities") or []


def sync_openrouter_prices(api_key=None, timeout=30):
	"""Update existing openrouter AIModel rows (prices, modalities, vision flag) from OpenRouter's model list.
	Creates no rows; a failed fetch writes nothing."""
	key = settings.OPENROUTER_API_KEY if api_key is None else api_key
	resp = requests.get(OPENROUTER_MODELS_URL, headers={"Authorization": f"Bearer {key}"}, timeout=timeout)
	resp.raise_for_status()
	entries = {m["id"]: m for m in resp.json()["data"]}
	updated = unchanged = 0
	not_found = []
	with transaction.atomic():
		for row in AIModel.objects.filter(provider="openrouter"):
			if row.name not in entries:
				not_found.append(row.name)
				continue
			entry = entries[row.name]
			input_price, output_price = _entry_prices(entry)
			modalities = _entry_modalities(entry)
			new = (input_price, output_price, modalities)
			if new == (row.input_price_per_mtok, row.output_price_per_mtok, row.input_modalities):
				unchanged += 1
				continue
			AIModel.objects.filter(pk=row.pk).update(
				input_price_per_mtok=input_price, output_price_per_mtok=output_price,
				input_modalities=modalities, supports_vision="image" in modalities,
			)
			updated += 1
	return {"updated": updated, "unchanged": unchanged, "not_found": not_found}
