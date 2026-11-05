from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .catalog import compute_status, match_product, save_variants
from .models import IgnoredProductPage, Product, ProductCandidate
from .price import parse_price
from .product_extract import COMPARED_FIELDS
from .products import _bad_body, _catalog_gate, _denied, _not_found
from .views import _kb_error, _project_user, _resolve_kb

LOW_CONFIDENCE = 0.6


def _kb_for(request, data, admin: bool):
	"""(kb, None) or (None, error); `data` is the query string or the JSON body."""
	pu = _project_user(request.user, data.get("project_id"))
	if not pu:
		return None, _denied()
	_, err = _catalog_gate(request, pu.project, admin=admin)
	if err:
		return None, err
	kb = _resolve_kb(pu.project, data.get("kb_id"))
	return (kb, None) if kb else (None, _not_found())


def _candidate_dict(c) -> dict:
	return {
		"id": str(c.id), "name": c.name, "confidence": c.confidence, "low_confidence": c.confidence < LOW_CONFIDENCE,
		"payload": c.payload, "matched_product_id": str(c.matched_product_id) if c.matched_product_id else None,
		"changes": c.changes, "status": c.status,
	}


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def candidate_list(request):
	kb, err = _kb_for(request, request.query_params, admin=False)
	if err:
		return err
	wanted = request.query_params.get("status", "pending")
	rows = kb.product_candidates.filter(status=wanted).select_related("document").order_by("page_url", "created_at", "id")
	pages = {}
	for c in rows:
		page = pages.setdefault(c.page_url, {"page_url": c.page_url, "page_title": c.document.page_title if c.document else "", "candidates": []})
		page["candidates"].append(_candidate_dict(c))
	ignored = list(kb.ignored_product_pages.order_by("url").values_list("url", flat=True))
	return Response({"success": True, "data": {"pages": list(pages.values()), "ignored_pages": ignored}})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def candidate_ignore_page(request):
	if not isinstance(request.data, dict):
		return _bad_body()
	kb, err = _kb_for(request, request.data, admin=True)
	if err:
		return err
	url = request.data.get("page_url")
	if not isinstance(url, str) or not url.strip() or len(url) > 1000:
		return _kb_error("invalid_value", "page_url is required", status.HTTP_400_BAD_REQUEST)
	with transaction.atomic():
		IgnoredProductPage.objects.get_or_create(kb=kb, url=url)
		n = kb.product_candidates.filter(page_url=url, status="pending").update(status="ignored")
	return Response({"success": True, "data": {"ignored": n}})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def product_extract(request):
	if not isinstance(request.data, dict):
		return _bad_body()
	kb, err = _kb_for(request, request.data, admin=True)
	if err:
		return err
	from .tasks import extract_product_candidates

	extract_product_candidates.delay(str(kb.id))
	return Response({"success": True, "data": {"queued": True}}, status=status.HTTP_202_ACCEPTED)


def _update_matched(product, payload, fields):
	"""Set the changed, unlocked fields straight on the product. Never touches locked_fields (that is owner-edit semantics)."""
	locked = set(product.locked_fields or [])
	changed = False
	for field in COMPARED_FIELDS:
		new = payload.get(field)
		if field == "price":
			new = parse_price(new) if new else None
		if field in locked or new in (None, "") or new == getattr(product, field):
			continue
		setattr(product, field, new)
		changed = True
	if changed:
		product.status = compute_status(product, fields)
		product.save()


def _create_product(kb, cand, fields):
	p = cand.payload
	product = Product(
		kb=kb, source="kb", product_url=cand.page_url, name=cand.name, description=p.get("description") or "",
		external_id=p.get("external_id") or None, sku=p.get("sku") or "", price=parse_price(p.get("price")),
		custom=p.get("custom") or {},
	)
	product.status = compute_status(product, fields)
	product.save()
	variants = [v for v in p.get("variants") or [] if v.get("label")]
	if variants:
		save_variants(product, [{**v, "price": parse_price(v.get("price"))} for v in variants])
	return product


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def candidate_import(request):
	if not isinstance(request.data, dict):
		return _bad_body()
	kb, err = _kb_for(request, request.data, admin=True)
	if err:
		return err
	ids = request.data.get("ids")
	if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
		return _kb_error("invalid_value", "ids must be a non-empty list", status.HTTP_400_BAD_REQUEST)
	from .tasks import fetch_product_images

	fields = list(kb.product_fields.all())
	imported = updated = 0
	rejected = []
	seen_ids = set()
	for cid in ids:
		try:
			cand = kb.product_candidates.filter(id=cid, status="pending").first()
		except (ValueError, TypeError, ValidationError):
			cand = None
		if not cand:
			rejected.append({"id": cid, "message": "Candidate not found or already handled"})
			continue
		p = cand.payload
		ext = p.get("external_id") or None
		if ext and ext in seen_ids:
			rejected.append({"id": cid, "message": f"Another picked product already uses ID {ext}"})
			continue
		first_attrs = ((p.get("variants") or [{}])[0] or {}).get("attributes") or {}
		with transaction.atomic():
			product, _ = match_product(kb, ext, p.get("sku") or "", cand.name, first_attrs)
			if product:
				new_ext = p.get("external_id")
				if new_ext and "external_id" not in (product.locked_fields or []) and \
						Product.objects.filter(kb=kb, external_id=new_ext).exclude(id=product.id).exists():
					rejected.append({"id": cid, "message": f"Another product already uses ID {new_ext}"})
					continue
				_update_matched(product, p, fields)
				updated += 1
			else:
				try:
					with transaction.atomic():
						product = _create_product(kb, cand, fields)
				except IntegrityError:
					rejected.append({"id": cid, "message": f"Another product already uses ID {ext}"})
					continue
				imported += 1
				srcs = p.get("image_srcs") or []
				if srcs:
					transaction.on_commit(lambda pid=str(product.id), s=list(srcs): fetch_product_images.delay(pid, s))
			cand.status = "imported"
			cand.matched_product = product
			cand.save(update_fields=["status", "matched_product"])
		if ext:
			seen_ids.add(ext)
	return Response({"success": True, "data": {"imported": imported, "updated": updated, "rejected": rejected}})
