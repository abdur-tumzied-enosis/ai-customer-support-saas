import json
import logging
import re

from django.db import IntegrityError, transaction
from django.db.models import Count
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .candidate_views import _kb_for
from .catalog import compute_status
from .models import Product, ProductField, ProductSource
from .product_api import clean_api_config, clean_secret, encrypt_blob, fetch_api_table
from .product_suggest import MAX_COLUMNS as MAX_SUGGEST_COLUMNS, suggest_mapping
from .product_sources import (
	MAX_FILE_BYTES, TARGETS, SheetNotPublic, TableError, apply_rows, check_headers, fetch_sheet_csv, guess_mapping, has_key,
	parse_table, sheet_export_url,
)
from .tasks import SYNC_LOCK_TIMEOUT, sync_product_source
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


def _source_dict(s) -> dict:
	running = s.sync_locked_at is not None and s.sync_locked_at > timezone.now() - SYNC_LOCK_TIMEOUT
	return {"id": str(s.id), "kind": s.kind, "mapping": s.mapping, "sheet_url": s.sheet_url,
		"last_synced_at": s.last_synced_at.isoformat() if s.last_synced_at else None, "last_error": s.last_error,
		"key_warning": not has_key(s.mapping), "created_at": s.created_at.isoformat(),
		"api_config": s.api_config, "has_secret": s.kind == "api" and s.api_config.get("auth_type", "none") != "none",  # the key never leaves the server
		"auto_sync_enabled": s.auto_sync_enabled, "schedule": s.schedule,
		"next_sync_at": s.next_sync_at.isoformat() if s.next_sync_at else None,
		"sync_failures": s.sync_failures, "sync_running": running}


def _read_upload(request):
	"""((headers, rows), None) or (None, error) from the multipart `file`."""
	upload = request.FILES.get("file")
	if upload is None:
		return None, _bad("invalid_value", "file is required")
	if upload.size > MAX_FILE_BYTES:
		return None, _bad("file_too_large", "The file is larger than 5 MB")
	try:
		return parse_table(upload.name, upload.read()), None
	except TableError as e:
		return None, _bad(e.code, e.message)


def _clean_mapping(raw, headers):
	"""(mapping, None) or (None, error). `raw` is a dict or a JSON string; None means "guess"."""
	if raw in (None, ""):
		return guess_mapping(headers), None
	if isinstance(raw, str):
		try:
			raw = json.loads(raw)
		except ValueError:
			return None, _bad("invalid_value", "mapping must be a JSON object")
	if not isinstance(raw, dict):
		return None, _bad("invalid_value", "mapping must be a JSON object")
	mapping = {}
	for target, col in raw.items():
		valid_target = target in TARGETS or (isinstance(target, str) and re.fullmatch(r"custom:[a-z][a-z0-9_]{0,63}", target))
		if not valid_target or not (col is None or isinstance(col, str)):
			return None, _bad("invalid_value", f"Unknown mapping target {target}")
		if col and col not in headers:
			return None, _bad("invalid_value", f"Column {col} is not in the file")
		mapping[target] = col or None
	return mapping, None


def _preview(request, headers, rows):
	"""What a preview shows: the columns, the guessed or saved mapping, five sample rows."""
	mapping, err = _clean_mapping(request.data.get("mapping"), headers)
	if err:
		return err
	return Response({"success": True, "data": {
		"headers": headers, "mapping": mapping, "sample": rows[:5], "key_warning": not has_key(mapping)}})


def _create(request, kb, kind, table, **fields):
	"""Save a new source and import its rows in one transaction. Sheets and APIs sync on a schedule from the start."""
	headers, rows = table
	mapping, err = _clean_mapping(request.data.get("mapping"), headers)
	if err:
		return err
	if not mapping.get("name"):
		return _bad("name_required", "Choose which column holds the product name")
	started = timezone.now()
	scheduled = kind != "csv"
	with transaction.atomic():
		source = ProductSource.objects.create(kb=kb, kind=kind, mapping=mapping, auto_sync_enabled=scheduled, **fields)
		result = apply_rows(kb, source, rows, mapping, started)
		source.last_synced_at = started
		source.next_sync_at = started + ProductSource.SCHEDULE_INTERVALS[source.schedule] if scheduled else None
		source.save(update_fields=["last_synced_at", "next_sync_at"])
	return Response({"success": True, "data": {"source": _source_dict(source), "result": result}},
		status=status.HTTP_201_CREATED)


class CsvSourcePreviewView(APIView):
	permission_classes = [IsAuthenticated]
	parser_classes = [MultiPartParser]

	def post(self, request):
		kb, err = _kb_for(request, request.data, admin=True)
		if err:
			return err
		table, err = _read_upload(request)
		if err:
			return err
		return _preview(request, *table)


class CsvSourceCreateView(APIView):
	permission_classes = [IsAuthenticated]
	parser_classes = [MultiPartParser]

	def post(self, request):
		kb, err = _kb_for(request, request.data, admin=True)
		if err:
			return err
		table, err = _read_upload(request)
		if err:
			return err
		return _create(request, kb, "csv", table)


class CsvSourceUploadView(APIView):
	"""A new version of a CSV source's file: the saved mapping applies, and rows missing from it are hidden."""
	permission_classes = [IsAuthenticated]
	parser_classes = [MultiPartParser]

	def post(self, request, source_id):
		source, err = _source_for(request, source_id)
		if err:
			return err
		if source.kind != "csv":
			return _bad("not_a_csv", "Only CSV sources take a new file")
		table, err = _read_upload(request)
		if err:
			return err
		headers, rows = table
		try:
			check_headers(source.mapping, headers)
		except TableError as e:
			return _bad(e.code, e.message)
		started = timezone.now()
		with transaction.atomic():
			result = apply_rows(source.kb, source, rows, source.mapping, started)
			source.last_synced_at, source.last_error = started, ""
			source.save(update_fields=["last_synced_at", "last_error"])
		return Response({"success": True, "data": {"source": _source_dict(source), "result": result}})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def product_source_list(request):
	kb, err = _kb_for(request, request.query_params, admin=True)
	if err:
		return err
	sources = kb.product_sources.order_by("-created_at", "id")
	return Response({"success": True, "data": [_source_dict(s) for s in sources]})


def _read_sheet(request):
	"""((headers, rows), None) or (None, error) for the posted sheet_url."""
	sheet_url = str(request.data.get("sheet_url") or "").strip()
	try:
		return parse_table("sheet.csv", fetch_sheet_csv(sheet_export_url(sheet_url))), None
	except ValueError as e:
		return None, _bad("invalid_url", str(e))
	except SheetNotPublic as e:
		return None, _bad("sheet_not_public", str(e))
	except TableError as e:
		return None, _bad(e.code, e.message)


class SheetSourcePreviewView(APIView):
	permission_classes = [IsAuthenticated]

	def post(self, request):
		kb, err = _kb_for(request, request.data, admin=True)
		if err:
			return err
		table, err = _read_sheet(request)
		if err:
			return err
		return _preview(request, *table)


class SheetSourceCreateView(APIView):
	permission_classes = [IsAuthenticated]

	def post(self, request):
		kb, err = _kb_for(request, request.data, admin=True)
		if err:
			return err
		table, err = _read_sheet(request)
		if err:
			return err
		return _create(request, kb, "sheet", table, sheet_url=str(request.data.get("sheet_url") or "").strip())


def _api_request(request):
	"""(config, secret, None) or (None, None, error) from the posted `api` object and `secret`."""
	try:
		config = clean_api_config(request.data.get("api"))
		return config, clean_secret(config["auth_type"], str(request.data.get("secret") or "")), None
	except TableError as e:
		return None, None, _bad(e.code, e.message)


class ApiSourcePreviewView(APIView):
	"""Connection test and preview: runs the saved request once and shows what comes back. Stores nothing."""
	permission_classes = [IsAuthenticated]

	def post(self, request):
		kb, err = _kb_for(request, request.data, admin=True)
		if err:
			return err
		config, secret, err = _api_request(request)
		if err:
			return err
		try:
			headers, rows = fetch_api_table(config, secret)
		except TableError as e:
			return _bad(e.code, e.message)
		return _preview(request, headers, rows)


class ApiSourceCreateView(APIView):
	permission_classes = [IsAuthenticated]

	def post(self, request):
		kb, err = _kb_for(request, request.data, admin=True)
		if err:
			return err
		config, secret, err = _api_request(request)
		if err:
			return err
		try:
			table = fetch_api_table(config, secret)
		except TableError as e:
			return _bad(e.code, e.message)
		body = config.pop("body")  # kept only inside the encrypted blob, never in the plain config
		blob = encrypt_blob({"secret": secret, "body": body}) if (secret or body) else ""
		return _create(request, kb, "api", table, api_config=config, secret_ciphertext=blob)


logger = logging.getLogger(__name__)


class MappingSuggestView(APIView):
	"""AI suggestions for a mapping. Input is the preview's headers and a few rows; nothing is saved."""
	permission_classes = [IsAuthenticated]

	def post(self, request):
		kb, err = _kb_for(request, request.data, admin=True)
		if err:
			return err
		headers, sample = request.data.get("headers"), request.data.get("sample")
		if not isinstance(headers, list) or not headers or len(headers) > MAX_SUGGEST_COLUMNS or not all(isinstance(h, str) for h in headers):
			return _bad("invalid_value", f"headers must be a list of 1 to {MAX_SUGGEST_COLUMNS} column names")
		if not isinstance(sample, list) or len(sample) > 5 or not all(isinstance(r, dict) for r in sample):
			return _bad("invalid_value", "sample must be up to 5 rows")
		try:
			suggestions = suggest_mapping(headers, sample, str(kb.project_id), str(kb.id))
		except Exception:
			logger.exception("Mapping suggestion failed")
			return _kb_error("suggest_failed", "Suggestions are unavailable right now. Map the columns by hand.", status.HTTP_503_SERVICE_UNAVAILABLE)
		return Response({"success": True, "data": {"suggestions": suggestions}})


def _source_for(request, source_id):
	"""(source, None) or (None, error); admin only."""
	kb, err = _kb_for(request, request.data if request.method == "POST" else request.query_params, admin=True)
	if err:
		return None, err
	source = kb.product_sources.filter(id=source_id).first()
	return (source, None) if source else (None, _not_found())


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def product_source_sync(request, source_id):
	source, err = _source_for(request, source_id)
	if err:
		return err
	if source.kind == "csv":
		return _bad("not_a_sheet", "Only Google Sheet and API sources can be synced")
	result = sync_product_source(str(source.id))
	source.refresh_from_db()
	if "error" in result:
		code = status.HTTP_409_CONFLICT if result["error"] == "already_syncing" else status.HTTP_400_BAD_REQUEST
		return Response({"success": False, "error": {"code": result["error"], "message": result["message"]},
			"data": {"source": _source_dict(source)}}, status=code)
	return Response({"success": True, "data": {"source": _source_dict(source), "result": result}})


@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def product_source_schedule(request, source_id):
	"""Turn automatic sync on or off and pick its schedule. Turning it on, or changing it, makes the next run due now."""
	source, err = _source_for(request, source_id)
	if err:
		return err
	if source.kind == "csv":
		return _bad("not_a_sheet", "Only Google Sheet and API sources have a schedule")
	enabled = request.data.get("auto_sync_enabled", source.auto_sync_enabled)
	schedule = request.data.get("schedule", source.schedule)
	if not isinstance(enabled, bool):
		return _bad("invalid_value", "auto_sync_enabled must be true or false")
	if schedule not in dict(ProductSource.SCHEDULE_CHOICES):
		return _bad("invalid_value", "schedule must be hourly or daily")
	source.auto_sync_enabled, source.schedule = enabled, schedule
	source.next_sync_at = timezone.now() if enabled else None
	source.save(update_fields=["auto_sync_enabled", "schedule", "next_sync_at"])
	return Response({"success": True, "data": {"source": _source_dict(source)}})


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def product_source_delete(request, source_id):
	source, err = _source_for(request, source_id)
	if err:
		return err
	source.delete()  # products keep existing: source_ref is SET_NULL
	return Response(status=status.HTTP_204_NO_CONTENT)
