import logging
from datetime import timedelta

import requests
from celery import shared_task
from django.conf import settings
from django.db.models import DecimalField, F, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from channels_app.models import Message

from .models import CreditTransaction, CreditWallet
from .service import charge_delivered_reply

logger = logging.getLogger("billing.tasks")


def charge_missed_replies() -> int:
	now = timezone.now()
	candidates = list(
		Message.objects.filter(
			direction="out", sender="bot", meta__billable="reply",
			created_at__lt=now - timedelta(minutes=10), created_at__gte=now - timedelta(days=7),
		).filter(~Q(external_message_id="") | Q(contact__platform="widget"))
		.select_related("project", "conversation")
	)
	charged = set(CreditTransaction.objects.filter(
		idempotency_key__in=[f"reply:{m.id}" for m in candidates]
	).values_list("idempotency_key", flat=True))
	missed = [m for m in candidates if f"reply:{m.id}" not in charged]
	for msg in missed:
		charge_delivered_reply(msg)
	return len(missed)


def check_balances() -> list:
	"""Report wallets whose stored balance differs from their ledger sum; never corrects them."""
	ledger = Coalesce(Sum("transactions__amount"), Value(0), output_field=DecimalField(max_digits=14, decimal_places=2))
	bad = list(CreditWallet.objects.annotate(ledger=ledger).exclude(balance=F("ledger")))
	for w in bad:
		logger.error("Wallet %s balance %s does not match ledger sum %s", w.pk, w.balance, w.ledger)
	return bad


@shared_task
def reconcile() -> None:
	charge_missed_replies()
	check_balances()


@shared_task
def notify_telegram(text: str) -> None:
	if not (settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID):
		return
	try:
		requests.post(
			f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage",
			json={"chat_id": settings.TELEGRAM_CHAT_ID, "text": text}, timeout=10,
		).raise_for_status()
	except requests.RequestException:
		logger.exception("Telegram notify failed")  # shortcut: no retry; the request is still in the DB/admin
