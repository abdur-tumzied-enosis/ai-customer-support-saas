from django.conf import settings
from django.db import transaction
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView

from orders.api import _err, _ok, _scoped

from .models import ACTIONS, CreditPackage, CreditRequest, CreditTransaction, PricingSettings
from .service import balance_of, price_of
from .tasks import notify_telegram

NOTE_KINDS = {"grant", "adjust", "signup_bonus", "initial_allocation"}  # admin-written text meant for the customer


def money(value) -> str:
	return f"{value:.2f}"


class WalletView(APIView):
	permission_classes = [IsAuthenticated]

	def get(self, request):
		project, err = _scoped(request, admin=False)
		if err:
			return err
		balance = balance_of(project)
		return _ok({"balance": money(balance), "low": balance < PricingSettings.get().low_balance_credits, "empty": balance <= 0})


class TransactionListView(APIView):
	permission_classes = [IsAuthenticated]

	def get(self, request):
		project, err = _scoped(request, admin=True)
		if err:
			return err
		qs = CreditTransaction.objects.filter(wallet__project=project).order_by("-created_at", "-id")
		if request.query_params.get("kind"):
			qs = qs.filter(kind=request.query_params["kind"])
		try:
			offset = max(0, int(request.query_params.get("offset", 0)))
			limit = min(100, max(1, int(request.query_params.get("limit", 50))))
		except ValueError:
			offset, limit = 0, 50
		return _ok({
			"count": qs.count(),
			"results": [{
				"id": t.id, "created_at": t.created_at.isoformat(), "kind": t.kind, "action": t.action,
				"action_label": ACTIONS[t.action][0] if t.action in ACTIONS else "",
				"amount": money(t.amount), "balance_after": money(t.balance_after),
				"note": t.note if t.kind in NOTE_KINDS else "", "conversation_id": str(t.conversation_id) if t.conversation_id else None,
			} for t in qs[offset:offset + limit]],
		})


class PricingView(APIView):
	permission_classes = [AllowAny]
	authentication_classes = []

	def get(self, request):
		actions = [(a, price_of(a)) for a in ACTIONS]
		return _ok({
			"packages": [{
				"id": p.id, "name": p.name, "credits": p.credits, "bonus_credits": p.bonus_credits, "price": money(p.price),
				"currency": p.currency, "features": p.features, "highlighted": p.highlighted,
			} for p in CreditPackage.objects.filter(active=True)],
			"actions": [{"action": a, "label": ACTIONS[a][0], "credits": money(c)} for a, c in actions if c],
			"signup_credits": money(PricingSettings.get().signup_credits),
		})


def _request_data(r):
	return {"id": r.id, "reference": r.reference, "status": r.status, "package": r.package.name, "amount": money(r.package.price),
		"currency": r.package.currency, "bkash_number": settings.BKASH_NUMBER}


def _notify(text):
	transaction.on_commit(lambda: notify_telegram.delay(text))


class CreditRequestView(APIView):
	permission_classes = [IsAuthenticated]

	def post(self, request):
		project, err = _scoped(request, admin=True, project_id=request.data.get("project_id"))
		if err:
			return err
		phone = str(request.data.get("phone", "")).strip()
		pid = str(request.data.get("package_id", ""))
		package = CreditPackage.objects.filter(pk=pid, active=True).first() if pid.isdigit() else None
		if not package or not (6 <= len(phone) <= 20):
			return _err("invalid", "Choose a package and enter a valid phone number", 400)
		r = CreditRequest.objects.create(project=project, user=request.user, package=package, phone=phone)
		lines = [f"New credit request {r.reference}", f"Project: {project.name}",
			f"Package: {package.name} ({package.credits}+{package.bonus_credits} credits, {money(package.price)} {package.currency})", f"Contact: {phone}"]
		_notify("\n".join(lines))
		return _ok(_request_data(r), 201)


class CreditRequestPayView(APIView):
	permission_classes = [IsAuthenticated]

	def post(self, request, pk):
		project, err = _scoped(request, admin=True, project_id=request.data.get("project_id"))
		if err:
			return err
		trx_id = str(request.data.get("trx_id", "")).strip()
		bkash_phone = str(request.data.get("bkash_phone", "")).strip()
		if not trx_id or len(trx_id) > 40 or not (6 <= len(bkash_phone) <= 20):
			return _err("invalid", "Enter the bKash transaction ID and the number you sent from", 400)
		r = CreditRequest.objects.filter(pk=pk, project=project, status__in=["requested", "paid"]).select_related("package").first()
		if not r:
			return _err("not_found", "Request not found", 404)
		r.trx_id, r.bkash_phone, r.status = trx_id, bkash_phone, "paid"
		r.save(update_fields=["trx_id", "bkash_phone", "status", "updated_at"])
		lines = [f"Payment submitted for {r.reference}", f"Project: {project.name}",
			f"Package: {r.package.name} ({money(r.package.price)} {r.package.currency})", f"TrxID: {trx_id}", f"From bKash: {bkash_phone}"]
		_notify("\n".join(lines))
		return _ok(_request_data(r))
