"""
Product catalog for image search: products with photos, plus the per-project image-search switch.
Catalog routes (products list/create/edit/delete, summary) are not image-gated; photo routes (images, confirm, file) answer 404 feature_disabled unless the master, operator and tenant switches are all on.
"""
import uuid
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.http import HttpResponseRedirect
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.storage import delete_keys, object_size, presign_get, presign_put, thumb_key
from .catalog import apply_owner_edit, compute_status, image_dict, product_dict, save_variants
from .models import MAX_IMAGES_PER_PRODUCT, MAX_PRODUCT_IMAGE_BYTES, Product, ProductImage, product_image_path
from .tasks import embed_product_image
from .views import _kb_error, _project_user, _resolve_kb

# Photos go browser -> R2 directly: presign (rows `uploading`), PUT, confirm (`pending`), worker resizes + embeds
IMAGE_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


def _features(project) -> dict:
	return {
		"image_search_available": project.image_search_available,
		"image_search": project.image_search_on,
		"vision_fallback_available": project.vision_fallback_available,
		"vision_fallback": project.vision_fallback_on,
	}


def _denied():
	return _kb_error("access_denied", "No access to this project", status.HTTP_403_FORBIDDEN)


def _not_found():
	return _kb_error("not_found", "Not found", status.HTTP_404_NOT_FOUND)


def _disabled():
	return _kb_error("feature_disabled", "Image search is not enabled", status.HTTP_404_NOT_FOUND)


def _gate(request, project, admin: bool):
	"""(None, error) when the caller may not use image search on `project`, else (membership, None) after the access check."""
	pu = _project_user(request.user, project.id)
	if not pu:
		return None, _not_found()
	if admin and pu.role != "admin":
		return None, _denied()
	if not project.image_search_on:
		return None, _disabled()
	return pu, None


def _catalog_gate(request, project, admin: bool):
	"""(membership, None) or (None, error): membership and role checks only, no image-search switch."""
	pu = _project_user(request.user, project.id)
	if not pu:
		return None, _not_found()
	if admin and pu.role != "admin":
		return None, _denied()
	return pu, None


def _clean_specs(request, existing: int):
	"""(validated [(filename, ext, size)], None) or (None, error) from the declared `files`: [{name, size, content_type}].
	The declared size and type are re-checked at confirm (size) and by the worker (decode)."""
	files = request.data.get("files")
	if not isinstance(files, list):
		return None, _kb_error("invalid_files", "files must be a list", status.HTTP_400_BAD_REQUEST)
	if existing + len(files) > MAX_IMAGES_PER_PRODUCT:
		return None, _kb_error("too_many_images", f"A product can have at most {MAX_IMAGES_PER_PRODUCT} photos", status.HTTP_400_BAD_REQUEST)
	out = []
	for f in files:
		size = f.get("size") if isinstance(f, dict) else None
		if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
			return None, _kb_error("invalid_files", "Each file needs a name, size and content_type", status.HTTP_400_BAD_REQUEST)
		name = str(f.get("name") or "photo")[:255]
		if size > MAX_PRODUCT_IMAGE_BYTES:
			mb = MAX_PRODUCT_IMAGE_BYTES // (1024 * 1024)
			return None, _kb_error("image_too_large", f"{name} is larger than {mb} MB", status.HTTP_400_BAD_REQUEST)
		ext = IMAGE_TYPES.get(f.get("content_type"))
		if not ext:
			return None, _kb_error("invalid_image", f"{name} is not a JPEG, PNG or WebP image", status.HTTP_400_BAD_REQUEST)
		out.append((name, ext, size))
	return out, None


def _reserve(product: Product, specs) -> list:
	"""Atomically save the product (if new) and one `uploading` row per file under tmp/; returns the presigned uploads."""
	with transaction.atomic():
		if product._state.adding:
			product.save()
		uploads = []
		for _name, ext, size in specs:
			img = ProductImage(product=product, status="uploading")
			img.file.name = "tmp/" + product_image_path(product.kb, str(img.id), ext)
			img.save()
			uploads.append({"image_id": str(img.id), **presign_put(img.file.name, f"image/{'jpeg' if ext == 'jpg' else ext}", size=size)})
	return uploads


def _created(product: Product, uploads: list) -> Response:
	return Response({"success": True, "data": {"product": product_dict(product), "uploads": uploads}}, status=status.HTTP_201_CREATED)


class FeaturesView(APIView):
	permission_classes = [IsAuthenticated]

	def get(self, request):
		pu = _project_user(request.user, request.query_params.get("project_id"))
		if not pu:
			return _denied()
		return Response({"success": True, "data": _features(pu.project)})

	def patch(self, request):
		pu = _project_user(request.user, request.data.get("project_id"), admin=True)
		if not pu:
			return _denied()
		project = pu.project
		changes = {k: request.data[k] for k in ("image_search", "vision_fallback") if k in request.data}
		if not changes:
			return _kb_error("invalid_value", "image_search or vision_fallback must be a boolean", status.HTTP_400_BAD_REQUEST)
		for key, value in changes.items():  # validate every key before writing any field
			if not isinstance(value, bool):
				return _kb_error("invalid_value", f"{key} must be a boolean", status.HTTP_400_BAD_REQUEST)
		if "image_search" in changes and not project.image_search_available:
			return _kb_error("not_allowed", "Image search is not available for this project", status.HTTP_403_FORBIDDEN)
		if "vision_fallback" in changes and not project.vision_fallback_available:
			return _kb_error("not_allowed", "Photo help is not available for this project", status.HTTP_403_FORBIDDEN)
		if "image_search" in changes:
			project.image_search_enabled = changes["image_search"]
		if "vision_fallback" in changes:
			project.vision_fallback_enabled = changes["vision_fallback"]
			project.vision_fallback_enabled_at = timezone.now()
			project.vision_fallback_enabled_by = request.user
		fields = [f"{key}_enabled" for key in changes]
		if "vision_fallback" in changes:
			fields += ["vision_fallback_enabled_at", "vision_fallback_enabled_by"]
		project.save(update_fields=fields)
		return Response({"success": True, "data": _features(project)})


def _parse_price(value):
	"""(Decimal | None, None) or (None, error message): empty clears the price."""
	if value is None or (isinstance(value, str) and not value.strip()):
		return None, None
	if isinstance(value, bool):
		return None, "Price must be a number"
	try:
		d = Decimal(str(value).strip())
	except InvalidOperation:
		return None, "Price must be a number"
	if not d.is_finite() or d < 0:
		return None, "Price must be between 0 and 9,999,999,999.99"
	if d >= Decimal(10) ** 10:  # can never be valid; also keeps quantize below within the 28-digit context
		return None, "Price must be between 0 and 9,999,999,999.99"
	try:
		d = d.quantize(Decimal("0.01"))  # round first: 9999999999.996 becomes 10000000000.00 and must be rejected
	except InvalidOperation:
		return None, "Price must be between 0 and 9,999,999,999.99"
	if d >= Decimal(10) ** 10:
		return None, "Price must be between 0 and 9,999,999,999.99"
	return d, None


def _clean_name(value):
	"""(name, None) or (None, error)."""
	if not isinstance(value, str):
		return None, _kb_error("invalid_value", "name must be a string", status.HTTP_400_BAD_REQUEST)
	name = value.strip()
	if not 1 <= len(name) <= 255:
		return None, _kb_error("invalid_name", "Name must be 1-255 characters", status.HTTP_400_BAD_REQUEST)
	return name, None


def _bad_body():
	return _kb_error("invalid_value", "Request body must be an object", status.HTTP_400_BAD_REQUEST)


def _duplicate():
	return _kb_error("duplicate_external_id", "Another product already uses this ID", status.HTTP_400_BAD_REQUEST)


def _clean_changes(data, kb, product):
	"""({model field: value}, None) or (None, error) for the owner-editable fields present in `data`."""
	out = {}
	if "name" in data:
		name, err = _clean_name(data.get("name"))
		if err:
			return None, err
		out["name"] = name
	for key in ("description", "product_url"):
		if key in data:
			out[key] = str(data.get(key) or "")
	if "sku" in data:
		out["sku"] = str(data.get("sku") or "").strip()[:128]
	if "external_id" in data:
		ext = str(data.get("external_id") or "").strip()[:255] or None
		if ext and Product.objects.filter(kb=kb, external_id=ext).exclude(id=product.id).exists():
			return None, _duplicate()
		out["external_id"] = ext
	if "price" in data:
		price, msg = _parse_price(data.get("price"))
		if msg:
			return None, _kb_error("invalid_price", msg, status.HTTP_400_BAD_REQUEST)
		out["price"] = price
	if "available" in data:
		if not isinstance(data.get("available"), bool):
			return None, _kb_error("invalid_value", "available must be a boolean", status.HTTP_400_BAD_REQUEST)
		out["available"] = data["available"]
	if "custom" in data:
		if not isinstance(data.get("custom"), dict):
			return None, _kb_error("invalid_value", "custom must be an object", status.HTTP_400_BAD_REQUEST)
		out["custom"] = data["custom"]
	return out, None


def _clean_variants(value):
	"""(list of cleaned variant dicts, None) or (None, error)."""
	def bad(msg):
		return None, _kb_error("invalid_value", msg, status.HTTP_400_BAD_REQUEST)

	if not isinstance(value, list):
		return bad("variants must be a list")
	out, seen = [], set()
	for item in value:
		if not isinstance(item, dict):
			return bad("Each variant must be an object")
		label = item.get("label")
		if not isinstance(label, str) or not 1 <= len(label.strip()) <= 255:
			return bad("Each variant needs a label of 1-255 characters")
		attributes = item.get("attributes") if item.get("attributes") is not None else {}
		if not isinstance(attributes, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in attributes.items()):
			return bad("Variant attributes must be an object of text values")
		for key in ("external_id", "sku"):
			if item.get(key) is not None and not isinstance(item[key], str):
				return bad(f"Variant {key} must be a string")
		price, msg = _parse_price(item.get("price"))
		if msg:
			return None, _kb_error("invalid_price", msg, status.HTTP_400_BAD_REQUEST)
		available = item.get("available", True)
		if not isinstance(available, bool):
			return bad("Variant available must be a boolean")
		ext = (item.get("external_id") or "").strip()[:255] or None
		if ext:
			if ext in seen:
				return None, _kb_error("duplicate_variant_key", "Two variants share the same ID", status.HTTP_400_BAD_REQUEST)
			seen.add(ext)
		out.append({"external_id": ext, "sku": (item.get("sku") or "").strip()[:128], "label": label.strip(),
			"attributes": attributes, "price": price, "available": available})
	return out, None


class ProductListCreateView(APIView):
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
		products = kb.products.all().select_related("kb__project").prefetch_related("variants", "images").order_by("-created_at", "id")
		return Response({"success": True, "data": [product_dict(p) for p in products]})

	def post(self, request):
		if not isinstance(request.data, dict):
			return _bad_body()
		pu = _project_user(request.user, request.data.get("project_id"))
		if not pu:
			return _denied()
		has_photos = "files" in request.data
		# photos are confirmed by an admin only, so a member's uploading rows could never be confirmed
		_, err = _catalog_gate(request, pu.project, admin=has_photos)
		if err:
			return err
		if has_photos and not pu.project.image_search_on:
			return _disabled()
		kb = _resolve_kb(pu.project, request.data.get("kb_id"))
		if not kb:
			return _not_found()
		_, err = _clean_name(request.data.get("name"))
		if err:
			return err
		specs = []
		if has_photos:
			specs, err = _clean_specs(request, 0)
			if err:
				return err
			if not specs:
				return _kb_error("no_images", "At least one photo is required", status.HTTP_400_BAD_REQUEST)
		variants = None
		if "variants" in request.data:
			variants, err = _clean_variants(request.data.get("variants"))
			if err:
				return err
		product = Product(kb=kb)
		changes, err = _clean_changes(request.data, kb, product)
		if err:
			return err
		for key, value in changes.items():
			setattr(product, key, value)
		product.status = compute_status(product, kb.product_fields.all())
		with transaction.atomic():
			try:
				with transaction.atomic():
					if specs:
						uploads = _reserve(product, specs)
					else:
						product.save()
						uploads = []
			except IntegrityError:  # lost a race with a concurrent create using the same external_id
				if product.external_id:
					return _duplicate()
				raise
			if variants is not None:
				save_variants(product, variants)
		data = {"product": product_dict(product), "uploads": uploads, "key_warning": not (product.external_id or product.sku)}
		return Response({"success": True, "data": data}, status=status.HTTP_201_CREATED if specs else status.HTTP_200_OK)


def _product_or_error(request, product_id, admin: bool, catalog: bool = False):
	product = Product.objects.filter(id=product_id).select_related("kb__project").first()
	if not product:
		return None, _not_found()
	_, err = (_catalog_gate if catalog else _gate)(request, product.kb.project, admin)
	return (None, err) if err else (product, None)


class ProductDetailView(APIView):
	permission_classes = [IsAuthenticated]

	def patch(self, request, product_id):
		product, err = _product_or_error(request, product_id, admin=False, catalog=True)
		if err:
			return err
		if not isinstance(request.data, dict):
			return _bad_body()
		changes, err = _clean_changes(request.data, product.kb, product)
		if err:
			return err
		variants = None
		if "variants" in request.data:
			variants, err = _clean_variants(request.data.get("variants"))
			if err:
				return err
		requested = request.data.get("status")
		if "status" in request.data and requested not in ("active", "needs_info", "hidden"):
			return _kb_error("invalid_status", "status must be active, needs_info or hidden", status.HTTP_400_BAD_REQUEST)
		if requested == "hidden":
			changes["status"] = "hidden"
		elif requested in ("active", "needs_info"):
			price = changes["price"] if "price" in changes else product.price
			if requested == "active" and price is None:
				return _kb_error("price_required", "A product needs a price to be active", status.HTTP_400_BAD_REQUEST)
			product.status = "needs_info"  # un-hide, then compute_status decides
			# unlock status so a later removal from the source hides it again
			product.locked_fields = [k for k in (product.locked_fields or []) if k != "status"]
		apply_owner_edit(product, changes, product.kb.product_fields.all())
		with transaction.atomic():
			try:
				with transaction.atomic():
					product.save()
			except IntegrityError:  # concurrent edit took this external_id
				if "external_id" in changes:
					return _duplicate()
				raise
			if variants is not None:
				save_variants(product, variants)
		return Response({"success": True, "data": product_dict(product)})

	def delete(self, request, product_id):
		product, err = _product_or_error(request, product_id, admin=True, catalog=True)
		if err:
			return err
		product.delete()
		return Response({"success": True, "data": None})


class ProductImagesAddView(APIView):
	permission_classes = [IsAuthenticated]

	def post(self, request, product_id):
		product, err = _product_or_error(request, product_id, admin=True)
		if err:
			return err
		specs, err = _clean_specs(request, product.images.count())
		if err:
			return err
		if not specs:
			return _kb_error("no_images", "At least one photo is required", status.HTTP_400_BAD_REQUEST)
		return _created(product, _reserve(product, specs))


def _image_or_error(request, image_id, admin: bool):
	try:
		image_id = uuid.UUID(str(image_id))  # confirm takes ids from the JSON body, not a <uuid:> route
	except ValueError:
		return None, _not_found()
	img = ProductImage.objects.filter(id=image_id).select_related("product__kb__project").first()
	if not img:
		return None, _not_found()
	_, err = _gate(request, img.product.kb.project, admin)
	return (None, err) if err else (img, None)


class ProductImageDetailView(APIView):
	permission_classes = [IsAuthenticated]

	def delete(self, request, image_id):
		img, err = _image_or_error(request, image_id, admin=True)
		if err:
			return err
		img.delete()
		return Response({"success": True, "data": None})


class ProductImagesConfirmView(APIView):
	"""The browser finished its PUTs: check each object landed within the size cap, then queue resize + embed."""
	permission_classes = [IsAuthenticated]

	def post(self, request):
		ids = request.data.get("image_ids")
		if not isinstance(ids, list) or not ids or len(ids) > MAX_IMAGES_PER_PRODUCT:
			return _kb_error("invalid_ids", "image_ids must be a non-empty list", status.HTTP_400_BAD_REQUEST)
		images = []
		for image_id in ids:  # all access checks before any change
			img, err = _image_or_error(request, image_id, admin=True)
			if err:
				return err
			images.append(img)
		for img in images:
			if img.status != "uploading":
				continue  # already confirmed: a retry or double click
			size = object_size(img.file.name)
			ok = size is not None and size <= MAX_PRODUCT_IMAGE_BYTES
			new_status, error = ("pending", "") if ok else ("failed", "upload_missing" if size is None else "upload_too_large")
			# Compare-and-set: of two overlapping confirms only one moves the row on (and queues the work)
			if not ProductImage.objects.filter(id=img.id, status="uploading").update(status=new_status, error=error):
				img.refresh_from_db(fields=["status", "error"])
				continue
			img.status, img.error = new_status, error
			if ok:
				transaction.on_commit(lambda i=str(img.id): embed_product_image.delay(i))
			else:
				delete_keys(img.file.name)
		return Response({"success": True, "data": [image_dict(i) for i in images]})


class ProductImageFileView(APIView):
	"""Redirects to a short-lived presigned R2 URL: the browser fetches the bytes from R2, not through Django."""
	permission_classes = [IsAuthenticated]

	def get(self, request, image_id):
		img, err = _image_or_error(request, image_id, admin=False)
		if err:
			return err
		if not img.file or img.status in ("uploading", "failed"):
			return _not_found()
		key = thumb_key(img.file.name) if request.query_params.get("size") == "thumb" and img.status == "ready" else img.file.name
		return HttpResponseRedirect(presign_get(key))
