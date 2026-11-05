from django.db.models import Count
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .products import _catalog_gate, _denied, _not_found
from .views import _project_user, _resolve_kb


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def product_summary(request):
	pu = _project_user(request.user, request.query_params.get("project_id"))
	if not pu:
		return _denied()
	_, err = _catalog_gate(request, pu.project, admin=False)
	if err:
		return err
	kb = _resolve_kb(pu.project, request.query_params.get("kb_id"))
	if not kb:
		return _not_found()
	counts = {"needs_info": 0, "active": 0, "hidden": 0}
	for row in kb.products.values("status").annotate(n=Count("id")):
		counts[row["status"]] = row["n"]
	return Response({"success": True, "data": {"counts": counts, "currency": pu.project.currency}})
