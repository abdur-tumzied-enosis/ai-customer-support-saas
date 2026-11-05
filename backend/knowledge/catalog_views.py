import re

from django.db import IntegrityError, transaction
from django.db.models import Count
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .catalog import compute_status
from .models import Product, ProductField
from .products import _bad_body, _catalog_gate, _denied, _not_found
from .views import _kb_error, _project_user, _resolve_kb


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


def _field_dict(f) -> dict:
	return {"id": str(f.id), "kb_id": str(f.kb_id), "key": f.key, "label": f.label, "kind": f.kind,
		"choices": f.choices, "required": f.required, "show_to_agent": f.show_to_agent, "position": f.position}


def recompute_kb_status(kb) -> None:
	"""Re-decide status for every product in the KB: a fixed number of queries plus one bulk update."""
	fields = list(kb.product_fields.all())
	changed = []
	for product in kb.products.prefetch_related("variants"):
		new = compute_status(product, fields)
		if new != product.status:
			product.status = new
			changed.append(product)
	if changed:
		Product.objects.bulk_update(changed, ["status"])


def _bad(code, message):
	return _kb_error(code, message, status.HTTP_400_BAD_REQUEST)


_KEY_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")


def _clean_field(data, field):
	"""(values, None) or (None, error) for the editable fields present in `data`; `field` is the existing row or None."""
	out = {}
	if field is None:
		key = data.get("key")
		if not isinstance(key, str) or not _KEY_RE.fullmatch(key):
			return None, _bad("invalid_value", "key must be 1-64 characters: a lowercase letter, then lowercase letters, digits or underscores")
		out["key"] = key
	if "label" in data or field is None:
		label = data.get("label")
		if not isinstance(label, str) or not label.strip():
			return None, _bad("invalid_value", "label is required")
		out["label"] = label.strip()[:255]
	if "kind" in data:
		if data["kind"] not in ("text", "number", "choice"):
			return None, _bad("invalid_value", "kind must be text, number or choice")
		out["kind"] = data["kind"]
	if "choices" in data:
		choices = data["choices"]
		if not isinstance(choices, list) or not all(isinstance(c, str) for c in choices):
			return None, _bad("invalid_value", "choices must be a list of strings")
		out["choices"] = [c.strip() for c in choices if c.strip()]
	for name in ("required", "show_to_agent"):
		if name in data:
			if not isinstance(data[name], bool):
				return None, _bad("invalid_value", f"{name} must be a boolean")
			out[name] = data[name]
	if "position" in data:
		if not isinstance(data["position"], int) or isinstance(data["position"], bool):
			return None, _bad("invalid_value", "position must be an integer")
		out["position"] = data["position"]
	kind = out.get("kind", field.kind if field else "text")
	choices = out.get("choices", field.choices if field else [])
	if kind == "choice" and not choices:
		return None, _bad("choices_required", "A choice field needs at least one choice")
	return out, None


class ProductFieldListCreateView(APIView):
	permission_classes = [IsAuthenticated]

	def get(self, request):
		pu = _project_user(request.user, request.query_params.get("project_id"))
		if not pu:
			return _denied()
		_, err = _catalog_gate(request, pu.project, admin=False)
		if err:
			return err
		kb = _resolve_kb(pu.project, request.query_params.get("kb_id"))
		if not kb:
			return _not_found()
		fields = kb.product_fields.order_by("position", "key")
		return Response({"success": True, "data": [_field_dict(f) for f in fields]})

	def post(self, request):
		if not isinstance(request.data, dict):
			return _bad_body()
		pu = _project_user(request.user, request.data.get("project_id"))
		if not pu:
			return _denied()
		_, err = _catalog_gate(request, pu.project, admin=True)
		if err:
			return err
		kb = _resolve_kb(pu.project, request.data.get("kb_id"))
		if not kb:
			return _not_found()
		values, err = _clean_field(request.data, None)
		if err:
			return err
		with transaction.atomic():
			try:
				with transaction.atomic():
					field = ProductField.objects.create(kb=kb, **values)
			except IntegrityError:
				return _bad("duplicate_key", "A field with this key already exists")
			recompute_kb_status(kb)
		return Response({"success": True, "data": _field_dict(field)}, status=status.HTTP_201_CREATED)


def _field_or_error(request, field_id):
	field = ProductField.objects.filter(id=field_id).select_related("kb__project").first()
	if not field:
		return None, _not_found()
	_, err = _catalog_gate(request, field.kb.project, admin=True)
	return (None, err) if err else (field, None)


class ProductFieldDetailView(APIView):
	permission_classes = [IsAuthenticated]

	def patch(self, request, field_id):
		field, err = _field_or_error(request, field_id)
		if err:
			return err
		if not isinstance(request.data, dict):
			return _bad_body()
		values, err = _clean_field(request.data, field)
		if err:
			return err
		with transaction.atomic():
			for name, value in values.items():
				setattr(field, name, value)
			field.save()
			if "required" in values:
				recompute_kb_status(field.kb)
		return Response({"success": True, "data": _field_dict(field)})

	def delete(self, request, field_id):
		field, err = _field_or_error(request, field_id)
		if err:
			return err
		with transaction.atomic():
			field.delete()  # values stay in Product.custom
			recompute_kb_status(field.kb)
		return Response({"success": True, "data": None})
