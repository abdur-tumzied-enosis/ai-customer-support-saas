from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings
from django.core.cache import cache
from django.db import transaction

from .models import MODEL_CONFIG_CACHE_KEY, AIModel

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
_PER_MTOK = Decimal(1_000_000)


def _mtok(value):
	"""OpenRouter's per-token USD string → USD per 1M tokens. Negative (dynamic routers, -1) or non-numeric → None."""
	try:
		d = Decimal(str(value))
	except InvalidOperation:
		return None
	return None if d < 0 else (d * _PER_MTOK).quantize(Decimal("0.000001"))


def sync_openrouter_prices(api_key=None, timeout=30):
	"""Update existing openrouter AIModel rows from OpenRouter's model list. Creates no rows; a failed fetch writes nothing."""
	key = settings.OPENROUTER_API_KEY if api_key is None else api_key
	resp = requests.get(OPENROUTER_MODELS_URL, headers={"Authorization": f"Bearer {key}"}, timeout=timeout)
	resp.raise_for_status()
	prices = {
		m["id"]: (_mtok(m["pricing"]["prompt"]), _mtok(m["pricing"]["completion"]))
		for m in resp.json()["data"]
	}
	updated = unchanged = 0
	not_found = []
	with transaction.atomic():
		for row in AIModel.objects.filter(provider="openrouter"):
			if row.name not in prices:
				not_found.append(row.name)
				continue
			new = prices[row.name]
			if new == (row.input_price_per_mtok, row.output_price_per_mtok):
				unchanged += 1
				continue
			AIModel.objects.filter(pk=row.pk).update(input_price_per_mtok=new[0], output_price_per_mtok=new[1])
			updated += 1
		if updated:
			cache.delete(MODEL_CONFIG_CACHE_KEY)  # .update() skips AIModel.save(), which is what normally clears it
	return {"updated": updated, "unchanged": unchanged, "not_found": not_found}
