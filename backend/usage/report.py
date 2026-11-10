"""Internal AI cost report: known USD cost by source and model, unknown usage, and credits charged for a date range."""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Count, Q, Sum

from billing.models import CreditTransaction

from .models import UsageEvent

KNOWN_SOURCES = ("provider_reported", "token_calculated", "system_default")


def _filter_project(qs, project_id, field):
	"""Scope qs to one project; a malformed or unknown id matches nothing instead of raising."""
	if not project_id:
		return qs
	try:
		return qs.filter(**{field: project_id})
	except (ValueError, ValidationError):
		return qs.none()


def ai_cost_report(start, end, project_id=None) -> dict:
	"""Dates are inclusive, in the current timezone (same as billing.report.coverage)."""
	events = _filter_project(UsageEvent.objects.filter(created_at__date__gte=start, created_at__date__lte=end), project_id, "project_id")
	txns = _filter_project(
		CreditTransaction.objects.filter(kind="charge", created_at__date__gte=start, created_at__date__lte=end), project_id, "wallet__project_id"
	)

	rows = events.values("provider", "model_name", "kind", "cost_source").annotate(
		n=Count("id"),
		tok=Sum("tokens"),
		usd=Sum("cost_usd"),
		unknown_n=Count("id", filter=Q(cost_usd__isnull=True)),
		unknown_tokens=Sum("tokens", filter=Q(cost_usd__isnull=True)),
	)

	known_by_source = {s: Decimal(0) for s in KNOWN_SOURCES}
	models, unknown_events, unknown_tokens, unmatched = {}, 0, 0, set()
	legacy_events, legacy_usd = 0, Decimal(0)
	for r in rows:
		unknown_events += r["unknown_n"]
		unknown_tokens += r["unknown_tokens"] or 0
		# Legacy (blank cost_source, written before snapshots): its stored cost is a separate total, never per-model known cost.
		# Only its unpriced part goes in the model table, under a "legacy" row.
		legacy = r["cost_source"] == ""
		if legacy:
			legacy_events += r["n"] - r["unknown_n"]
			legacy_usd += r["usd"] or Decimal(0)
			if not r["unknown_n"]:
				continue
		key = ("legacy", "") if legacy else (r["provider"], r["model_name"])
		m = models.setdefault(key, {
			"provider": "legacy" if legacy else r["provider"], "model_name": "" if legacy else r["model_name"],
			"events": 0, "input_tokens": 0, "output_tokens": 0, "known_usd": Decimal(0), "unknown_events": 0, "unknown_tokens": 0,
		})
		m["events"] += r["unknown_n"] if legacy else r["n"]
		tok = r["unknown_tokens"] if legacy else r["tok"]
		if r["kind"] == "chat_in":
			m["input_tokens"] += tok or 0
		elif r["kind"] == "chat_out":
			m["output_tokens"] += tok or 0
		if not legacy and r["cost_source"] in KNOWN_SOURCES and r["usd"] is not None:
			known_by_source[r["cost_source"]] += r["usd"]
			m["known_usd"] += r["usd"]
		m["unknown_events"] += r["unknown_n"]
		m["unknown_tokens"] += r["unknown_tokens"] or 0
		if r["unknown_n"] and r["model_name"] and not legacy:
			unmatched.add(r["model_name"])

	credits = -(txns.aggregate(s=Sum("amount"))["s"] or Decimal(0))
	return {
		"known_by_source": known_by_source,
		"unknown_events": unknown_events,
		"unknown_tokens": unknown_tokens,
		"legacy_events": legacy_events,
		"legacy_usd": legacy_usd,
		"credits_charged": credits,
		"models": sorted(models.values(), key=lambda m: m["known_usd"], reverse=True),
		"unmatched_models": sorted(unmatched),
		"has_unknown": unknown_events > 0,
	}
