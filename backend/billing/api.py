from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView

from orders.api import _ok, _scoped

from .models import ACTIONS, CreditPackage, CreditTransaction, PricingSettings
from .service import balance_of, price_of

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
				"name": p.name, "credits": p.credits, "bonus_credits": p.bonus_credits, "price": money(p.price),
				"currency": p.currency, "features": p.features, "highlighted": p.highlighted,
			} for p in CreditPackage.objects.filter(active=True)],
			"actions": [{"action": a, "label": ACTIONS[a][0], "credits": money(c)} for a, c in actions if c],
			"signup_credits": money(PricingSettings.get().signup_credits),
		})
