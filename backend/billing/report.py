from collections import defaultdict
from decimal import Decimal

from django.db.models import Count, Q, Sum

from accounts.models import Project
from usage.models import UsageEvent

from .models import CreditTransaction, PricingSettings
from .tasks import check_balances


def conversation_totals(conversation) -> tuple:
	"""(credits charged, known USD cost) for one conversation."""
	credits = -(CreditTransaction.objects.filter(kind="charge", conversation=conversation).aggregate(s=Sum("amount"))["s"] or Decimal("0"))
	known = UsageEvent.objects.filter(conversation=conversation, cost_usd__isnull=False).aggregate(s=Sum("cost_usd"))["s"] or Decimal("0")
	return credits, known


def coverage(start, end) -> dict:
	"""Charges vs known provider cost per project for start..end, both dates inclusive."""
	cfg = PricingSettings.get()
	txns = CreditTransaction.objects.filter(kind="charge", created_at__date__gte=start, created_at__date__lte=end, wallet__project__isnull=False)
	events = UsageEvent.objects.filter(created_at__date__gte=start, created_at__date__lte=end)
	charged, by_action = defaultdict(Decimal), defaultdict(dict)
	for r in txns.values("wallet__project_id", "action").annotate(s=Sum("amount")):
		charged[r["wallet__project_id"]] += -r["s"]
		by_action[r["wallet__project_id"]][r["action"]] = -r["s"]
	known, unpriced = defaultdict(Decimal), defaultdict(int)
	for r in events.values("project_id").annotate(known=Sum("cost_usd"), unpriced=Count("pk", filter=Q(cost_usd__isnull=True))):
		known[r["project_id"]] = r["known"] or Decimal("0")
		unpriced[r["project_id"]] = r["unpriced"]
	ids = set(charged) | set(known) | {p for p, n in unpriced.items() if n}
	names = dict(Project.objects.filter(pk__in=ids).values_list("pk", "name"))
	rows = []
	for pid in sorted(ids, key=lambda p: names.get(p, "")):
		estimated = charged[pid] * cfg.usd_per_credit
		ratio = estimated / known[pid] if known[pid] else None
		rows.append({
			"project_id": pid, "project_name": names.get(pid, ""), "credits_charged": charged[pid],
			"estimated_value_usd": estimated, "known_cost_usd": known[pid], "unpriced_events": unpriced[pid],
			"ratio": ratio, "below_target": ratio is not None and ratio < cfg.target_coverage_ratio,
			"by_action": dict(by_action[pid]),
		})
	return {"mismatched": check_balances(), "rows": rows, "usd_per_credit": cfg.usd_per_credit, "target": cfg.target_coverage_ratio}
