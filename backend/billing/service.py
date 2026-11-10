"""Single module through which every credit balance change goes."""
import logging
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from rest_framework.response import Response

from accounts.models import ProjectUser
from accounts.tasks import queue_email

from .models import ACTIONS, ActionPrice, CreditTransaction, CreditWallet, PricingSettings

logger = logging.getLogger("billing.service")
CENT = Decimal("0.01")


class InsufficientCredits(Exception):
	def __init__(self, price: Decimal, balance: Decimal):
		super().__init__(f"needs {price}, balance {balance}")
		self.price, self.balance = price, balance


def price_of(action: str) -> Decimal:
	try:
		return ActionPrice.objects.get(action=action).credits
	except ActionPrice.DoesNotExist:
		logger.error("No ActionPrice row for %r, using default", action)
		return ACTIONS[action][1]


def wallet_for(project) -> CreditWallet:
	return CreditWallet.objects.get_or_create(project=project, defaults={"project_name": project.name})[0]


def balance_of(project) -> Decimal:
	return CreditWallet.objects.filter(project=project).values_list("balance", flat=True).first() or Decimal("0")


def can_afford(project, amount: Decimal) -> bool:
	return balance_of(project) >= amount


def shopper_blocked(project, cost: Decimal) -> bool:
	return balance_of(project) - cost < -PricingSettings.get().overdraft_credits


def insufficient_credits_response(price: Decimal) -> Response:
	message = f"Not enough credits for this ({price:g} needed). Ask us to top up your project."
	return Response({"success": False, "error": {"code": "insufficient_credits", "message": message}}, status=402)


def _lock(project) -> CreditWallet:
	"""Must run inside atomic()."""
	return CreditWallet.objects.select_for_update().get(pk=wallet_for(project).pk)


def _settle(wallet: CreditWallet, old: Decimal) -> None:
	"""Save the new balance and handle the depletion flag; wallet must be locked and wallet.balance already new."""
	if old > 0 >= wallet.balance and not wallet.depleted_notified:
		wallet.depleted_notified = True
		_notify_depleted(wallet)
	elif wallet.balance > 0:
		wallet.depleted_notified = False
	wallet.save(update_fields=["balance", "depleted_notified", "updated_at"])


def _notify_depleted(wallet: CreditWallet) -> None:
	admins = ProjectUser.objects.filter(project_id=wallet.project_id, role="admin").select_related("user")
	emails = [pu.user.email for pu in admins if pu.user.email]
	if not emails:
		logger.warning("Wallet %s is out of credits but its project has no admin to email", wallet.pk)
		return
	subject = f"Your {settings.PRODUCT_NAME} project is out of credits"
	body = (
		f"Your project \"{wallet.project_name}\" has run out of credits. "
		"Until it is topped up, shopper replies will hand off to staff. Contact us to add credits."
	)
	for email in emails:
		queue_email(email, subject, body)


def _post(wallet: CreditWallet, amount: Decimal, **row_fields) -> CreditTransaction:
	"""Write one ledger row and move the balance. wallet must be locked (select_for_update) inside atomic()."""
	amount = amount.quantize(CENT)
	old, new = wallet.balance, wallet.balance + amount
	try:
		with transaction.atomic(savepoint=True):
			txn = CreditTransaction.objects.create(wallet=wallet, amount=amount, balance_after=new, **row_fields)
	except IntegrityError:
		return CreditTransaction.objects.get(idempotency_key=row_fields["idempotency_key"])
	wallet.balance = new
	_settle(wallet, old)
	return txn


def charge(project, action, *, key: str, units: int = 1, conversation=None, trigger_message=None, result_message=None):
	price = price_of(action)
	if price == 0:
		return None
	with transaction.atomic():
		wallet = _lock(project)
		existing = CreditTransaction.objects.filter(idempotency_key=key).first()
		if existing:
			return existing
		return _post(
			wallet, -price * units, kind="charge", action=action, idempotency_key=key,
			conversation=conversation, trigger_message=trigger_message, result_message=result_message,
		)


def charge_affordable(project, action, rows: list, create) -> int:
	"""Create as many of `rows` as the balance covers (never overdrawing) and charge one row each."""
	price = price_of(action)
	with transaction.atomic():
		wallet = _lock(project)
		if price == 0:
			n = len(rows)
		else:
			n = min(len(rows), int(wallet.balance // price)) if wallet.balance > 0 else 0
		kept = rows[:n]
		create(kept)
		old, balance, txns = wallet.balance, wallet.balance, []
		for row in kept:
			if price:
				balance -= price
				txns.append(CreditTransaction(
					wallet=wallet, amount=-price, balance_after=balance, kind="charge", action=action,
					idempotency_key=f"{action}:{row.pk}",
				))
		if txns:
			CreditTransaction.objects.bulk_create(txns)
			wallet.balance = balance
			_settle(wallet, old)
		return n


def grant(project, amount: Decimal, kind: str, *, key: str = "", created_by=None, note: str = ""):
	with transaction.atomic():
		wallet = _lock(project)
		if key and CreditTransaction.objects.filter(idempotency_key=key).exists():
			return None
		return _post(wallet, amount, kind=kind, idempotency_key=key, created_by=created_by, note=note)


def charge_delivered_reply(out_msg):
	"""Charge the reply once it has reached the shopper; never raises (the reconciler recovers misses)."""
	meta = out_msg.meta or {}
	if meta.get("billable") != "reply":
		return None
	try:
		from channels_app.models import Message
		return charge(
			out_msg.project, "reply", key=f"reply:{out_msg.id}", conversation=out_msg.conversation,
			trigger_message=Message.objects.filter(id=meta.get("trigger_id")).first(), result_message=out_msg,
		)
	except Exception:
		logger.exception("Could not charge delivered reply %s", out_msg.id)
		return None


def grant_signup_bonus(user):
	"""One-off bonus to the user's earliest created project; the per-user key makes later calls no-ops."""
	from accounts.models import Project
	amount = PricingSettings.get().signup_credits
	project = Project.objects.filter(created_by=user).order_by("created_at").first()
	if not amount or not project:
		return None
	# Existing projects can't get both allocations
	if CreditTransaction.objects.filter(wallet__project=project, kind="initial_allocation").exists():
		return None
	return grant(project, amount, "signup_bonus", key=f"signup:{user.pk}")
