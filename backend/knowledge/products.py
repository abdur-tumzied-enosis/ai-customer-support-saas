"""
Product catalog for image search: products with photos, plus the per-project image-search switch.
Every route except features/ answers 404 feature_disabled unless the master, operator and tenant switches are all on.
"""
import uuid

from django.db import transaction
from django.http import HttpResponseRedirect
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.storage import delete_keys, object_size, presign_get, presign_put, thumb_key
from .models import MAX_IMAGES_PER_PRODUCT, MAX_PRODUCT_IMAGE_BYTES, Product, ProductImage, product_image_path
from .tasks import embed_product_image
from .views import _kb_error, _project_user, _resolve_kb

# Photos go browser -> R2 directly: presign (rows `uploading`), PUT, confirm (`pending`), worker resizes + embeds
IMAGE_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


def _features(project) -> dict:
	return {"image_search_available": project.image_search_available, "image_search": project.image_search_on}


def _image_dict(img: ProductImage) -> dict:
	url = f"/api/kb/products/images/{img.id}/file/"
	return {"id": str(img.id), "status": img.status, "url": url, "thumb_url": f"{url}?size=thumb"}


def _product_dict(p: Product) -> dict:
	return {
		"id": str(p.id),
		"name": p.name,
		"description": p.description,
		"created_at": p.created_at.isoformat(),
		"images": [_image_dict(i) for i in p.images.all().order_by("created_at", "id")],
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


def _clean_specs(request, existing: int):
	"""(validated [(filename, ext)], None) or (None, error) from the declared `files`: [{name, size, content_type}].
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
		out.append((name, ext))
	return out, None


def _reserve(product: Product, specs) -> list:
	"""Atomically save the product (if new) and one `uploading` row per file under tmp/; returns the presigned uploads."""
	with transaction.atomic():
		if product._state.adding:
			product.save()
		uploads = []
		for _name, ext in specs:
			img = ProductImage(product=product, status="uploading")
			img.file.name = "tmp/" + product_image_path(product.kb, str(img.id), ext)
			img.save()
			uploads.append({"image_id": str(img.id), **presign_put(img.file.name, f"image/{'jpeg' if ext == 'jpg' else ext}")})
	return uploads


def _created(product: Product, uploads: list) -> Response:
	return Response({"success": True, "data": {"product": _product_dict(product), "uploads": uploads}}, status=status.HTTP_201_CREATED)


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
		value = request.data.get("image_search")
		if not isinstance(value, bool):
			return _kb_error("invalid_value", "image_search must be a boolean", status.HTTP_400_BAD_REQUEST)
		if not project.image_search_available:
			return _kb_error("not_allowed", "Image search is not available for this project", status.HTTP_403_FORBIDDEN)
		project.image_search_enabled = value
		project.save(update_fields=["image_search_enabled"])
		return Response({"success": True, "data": _features(project)})


class ProductListCreateView(APIView):
	permission_classes = [IsAuthenticated]

	def get(self, request):
		pu = _project_user(request.user, request.query_params.get("project_id"))
		if not pu:
			return _denied()
		_, err = _gate(request, pu.project, admin=False)
		if err:
			return err
		kb = _resolve_kb(pu.project, request.query_params.get("kb_id"))
		if not kb:
			return _not_found()
		return Response({"success": True, "data": [_product_dict(p) for p in kb.products.all().order_by("-created_at", "id")]})

	def post(self, request):
		pu = _project_user(request.user, request.data.get("project_id"))
		if not pu:
			return _denied()
		_, err = _gate(request, pu.project, admin=True)
		if err:
			return err
		kb = _resolve_kb(pu.project, request.data.get("kb_id"))
		if not kb:
			return _not_found()
		name = (request.data.get("name") or "").strip()
		if not 1 <= len(name) <= 255:
			return _kb_error("invalid_name", "Name must be 1-255 characters", status.HTTP_400_BAD_REQUEST)
		specs, err = _clean_specs(request, 0)
		if err:
			return err
		if not specs:
			return _kb_error("no_images", "At least one photo is required", status.HTTP_400_BAD_REQUEST)
		product = Product(kb=kb, name=name, description=request.data.get("description") or "")
		return _created(product, _reserve(product, specs))


def _product_or_error(request, product_id, admin: bool):
	product = Product.objects.filter(id=product_id).select_related("kb__project").first()
	if not product:
		return None, _not_found()
	_, err = _gate(request, product.kb.project, admin)
	return (None, err) if err else (product, None)


class ProductDetailView(APIView):
	permission_classes = [IsAuthenticated]

	def patch(self, request, product_id):
		product, err = _product_or_error(request, product_id, admin=True)
		if err:
			return err
		if "name" in request.data:
			name = (request.data.get("name") or "").strip()
			if not 1 <= len(name) <= 255:
				return _kb_error("invalid_name", "Name must be 1-255 characters", status.HTTP_400_BAD_REQUEST)
			product.name = name
		if "description" in request.data:
			product.description = request.data.get("description") or ""
		product.save()
		return Response({"success": True, "data": _product_dict(product)})

	def delete(self, request, product_id):
		product, err = _product_or_error(request, product_id, admin=True)
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
			if size is None or size > MAX_PRODUCT_IMAGE_BYTES:
				img.status, img.error = "failed", "upload_missing" if size is None else "upload_too_large"
				delete_keys(img.file.name)
			else:
				img.status, img.error = "pending", ""
				transaction.on_commit(lambda i=str(img.id): embed_product_image.delay(i))
			img.save(update_fields=["status", "error"])
		return Response({"success": True, "data": [_image_dict(i) for i in images]})


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
