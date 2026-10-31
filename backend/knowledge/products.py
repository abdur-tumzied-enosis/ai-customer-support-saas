"""
Product catalog for image search: products with photos, plus the per-project image-search switch.
Every route except features/ answers 404 feature_disabled unless the master, operator and tenant switches are all on.
"""
import mimetypes

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.http import FileResponse
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from agents.image_search import NotAnImage, image_format, load_image
from .models import MAX_IMAGES_PER_PRODUCT, Product, ProductImage, product_image_path
from .tasks import embed_product_image
from .views import _kb_error, _project_user, _resolve_kb


def _features(project) -> dict:
	return {"image_search_available": project.image_search_available, "image_search": project.image_search_on}


def _image_dict(img: ProductImage) -> dict:
	return {"id": str(img.id), "status": img.status, "url": f"/api/kb/products/images/{img.id}/file/"}


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


def _clean_files(request, existing: int):
	"""(validated [(bytes, ext, filename)], None) or (None, error). Nothing is stored here."""
	files = request.FILES.getlist("images")
	if existing + len(files) > MAX_IMAGES_PER_PRODUCT:
		return None, _kb_error("too_many_images", f"A product can have at most {MAX_IMAGES_PER_PRODUCT} photos", status.HTTP_400_BAD_REQUEST)
	out = []
	for f in files:
		data = f.read()
		try:
			load_image(data)
			out.append((data, image_format(data), f.name))
		except NotAnImage:
			return None, _kb_error("invalid_image", f"{f.name} is not a valid image", status.HTTP_400_BAD_REQUEST)
	return out, None


def _store(product: Product, files) -> list:
	"""Save validated files as pending ProductImage rows and queue embedding."""
	images = []
	for data, ext, _name in files:
		img = ProductImage(product=product)
		img.file.name = default_storage.save(product_image_path(product.kb, str(img.id), ext), ContentFile(data))
		img.save()
		images.append(img)
	for img in images:
		embed_product_image.delay(str(img.id))
	return images


class FeaturesView(APIView):
	permission_classes = [IsAuthenticated]

	def get(self, request):
		pu = _project_user(request.user, request.query_params.get("project_id"))
		if not pu:
			return _denied()
		return Response(_features(pu.project))

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
		return Response(_features(project))


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
		return Response([_product_dict(p) for p in kb.products.all().order_by("-created_at", "id")])

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
		files, err = _clean_files(request, 0)
		if err:
			return err
		if not files:
			return _kb_error("no_images", "At least one photo is required", status.HTTP_400_BAD_REQUEST)
		product = Product.objects.create(kb=kb, name=name, description=request.data.get("description") or "")
		try:
			_store(product, files)
		except Exception:
			product.delete()
			raise
		return Response(_product_dict(product), status=status.HTTP_201_CREATED)


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
		return Response(_product_dict(product))

	def delete(self, request, product_id):
		product, err = _product_or_error(request, product_id, admin=True)
		if err:
			return err
		product.delete()
		return Response(status=status.HTTP_204_NO_CONTENT)


class ProductImagesAddView(APIView):
	permission_classes = [IsAuthenticated]

	def post(self, request, product_id):
		product, err = _product_or_error(request, product_id, admin=True)
		if err:
			return err
		files, err = _clean_files(request, product.images.count())
		if err:
			return err
		if not files:
			return _kb_error("no_images", "At least one photo is required", status.HTTP_400_BAD_REQUEST)
		_store(product, files)
		return Response(_product_dict(product), status=status.HTTP_201_CREATED)


def _image_or_error(request, image_id, admin: bool):
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
		return Response(status=status.HTTP_204_NO_CONTENT)


class ProductImageFileView(APIView):
	permission_classes = [IsAuthenticated]

	def get(self, request, image_id):
		img, err = _image_or_error(request, image_id, admin=False)
		if err:
			return err
		if not img.file or not default_storage.exists(img.file.name):
			return _not_found()
		content_type = mimetypes.guess_type(img.file.name)[0] or "application/octet-stream"
		return FileResponse(default_storage.open(img.file.name, "rb"), content_type=content_type)
