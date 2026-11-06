import uuid
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.utils.dateparse import parse_date
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from knowledge.models import CustomInstruction, KnowledgeBase, Product

from .access import project_for
from .action_config import ActionError, clean_live_lookup, header_summary, validate_action
from .actions import already_sent_orders, execute_run, run_action_for_orders, SENDABLE
from .tasks import run_action_run
from .models import Order, OrderAction, OrderActionRun, OrderSettings
from .service import (
	OrderStatusError, OrderValidationError, apply_lines, get_or_create_draft, set_status, submit_order, update_customer,
	validate_for_submit,
)
from .totals import collect_amount
from .prompt import build_order_prompt
from .requirements import check
from channels_app.models import Conversation, ProjectWebhook

from .export import write_orders_csv
from .from_chat import prefill_from_conversation
from .guard import check_instruction
from .improve import NoSafeSuggestion, improve
from .instructions import RESOLVE_SCOPES, find_order_instructions
from .setup import run_setup

CHANNELS = {"facebook", "instagram", "whatsapp", "widget"}


class _Bad(Exception):
	def __init__(self, code: str, message: str):
		super().__init__(message)
		self.code, self.message = code, message


def _ok(data, http_status=200):
	return Response({"success": True, "data": data}, status=http_status)


def _err(code: str, message: str, http_status: int):
	return Response({"success": False, "error": {"code": code, "message": message}}, status=http_status)


def _scoped(request, admin: bool, project_id=None):
	"""(project, None) for an allowed caller, else (None, error response). project_id defaults to the query string."""
	try:
		return project_for(request, project_id or request.query_params.get("project_id"), admin=admin), None
	except Http404:
		return None, _err("not_found", "Project not found", 404)
	except PermissionDenied:
		return None, _err("access_denied", "Admin access required", 403)


def _zones(value):
	if not isinstance(value, list):
		raise _Bad("invalid_zones", "zones must be a list")
	out = []
	for zone in value:
		name = str(zone.get("name", "")).strip() if isinstance(zone, dict) else ""
		if not name:
			raise _Bad("invalid_zones", "every zone needs a name")
		charge = zone.get("charge")
		if charge in (None, ""):
			charge = None
		else:
			try:
				amount = Decimal(str(charge))
			except InvalidOperation:
				raise _Bad("invalid_zones", f"charge for {name} is not a number")
			if not amount.is_finite() or amount < 0:
				raise _Bad("invalid_zones", f"charge for {name} must be 0 or more")
			charge = str(amount)
		out.append({"name": name, "charge": charge})
	return out


def _payments(value):
	if not isinstance(value, list) or not value:
		raise _Bad("invalid_payment_methods", "add at least one payment method")
	out = []
	for method in value:
		label = str(method.get("label", "")).strip() if isinstance(method, dict) else ""
		if not label:
			raise _Bad("invalid_payment_methods", "every payment method needs a label")
		out.append({"label": label, "default": bool(method.get("default"))})
	if sum(m["default"] for m in out) != 1:
		raise _Bad("invalid_payment_methods", "exactly one payment method must be the default")
	return out


def _emails(value):
	if not isinstance(value, list) or len(value) > 10:
		raise _Bad("invalid_emails", "at most 10 notification addresses")
	for address in value:
		try:
			validate_email(str(address))
		except ValidationError:
			raise _Bad("invalid_emails", f"not an email address: {address}")
	return [str(address).strip() for address in value]


def _expiry(value):
	try:
		hours = int(value)
	except (TypeError, ValueError):
		raise _Bad("invalid_expiry", "draft expiry must be a whole number of hours")
	if not 1 <= hours <= 168:
		raise _Bad("invalid_expiry", "draft expiry must be 1 to 168 hours")
	return hours


def _channels(value):
	if not isinstance(value, list) or any(not isinstance(c, str) or c not in CHANNELS for c in value):
		raise _Bad("invalid_channels", "channels must be from: facebook, instagram, whatsapp, widget")
	return list(dict.fromkeys(value))


def _form_fields(value):
	if not isinstance(value, dict):
		raise _Bad("invalid_form_fields", "form fields must be an object")
	optional, custom = value.get("optional", []), value.get("custom", [])
	if not isinstance(optional, list) or not isinstance(custom, list):
		raise _Bad("invalid_form_fields", "optional and custom must be lists")
	for field in custom:
		if not isinstance(field, dict) or not str(field.get("key", "")).strip() or not str(field.get("label", "")).strip():
			raise _Bad("invalid_form_fields", "every custom field needs a key and a label")
	return {
		"optional": [str(x) for x in optional],
		"custom": [
			{"key": str(f["key"]).strip(), "label": str(f["label"]).strip(), "required": bool(f.get("required"))}
			for f in custom
		],
	}


CLEANERS = {
	"channels": _channels,
	"zones": _zones,
	"payment_methods": _payments,
	"form_fields": _form_fields,
	"notification_emails": _emails,
	"draft_expiry_hours": _expiry,
}


def _settings_dict(settings, project) -> dict:
	instructions = [i.message for i in CustomInstruction.objects.filter(project=project, scope="order")]  # every KB's, as the bot sees them
	return {
		"channels": settings.channels,
		"zones": settings.zones,
		"payment_methods": settings.payment_methods,
		"form_fields": settings.form_fields,
		"notification_emails": settings.notification_emails,
		"draft_expiry_hours": settings.draft_expiry_hours,
		"live_lookup": {**settings.live_lookup, "headers": header_summary(settings.live_lookup.get("headers", []))},
		"enabled": settings.enabled,
		"test_mode": settings.test_mode,
		"currency": project.currency,
		"prompt_preview": build_order_prompt(settings, instructions, project.currency),
	}


class SettingsView(APIView):
	"""GET: any member reads the order settings. PATCH: an admin changes them."""
	permission_classes = [IsAuthenticated]

	def get(self, request):
		project, error = _scoped(request, admin=False)
		if error:
			return error
		settings, _ = OrderSettings.objects.get_or_create(project=project)
		return _ok(_settings_dict(settings, project))

	def patch(self, request):
		project, error = _scoped(request, admin=True)
		if error:
			return error
		settings, _ = OrderSettings.objects.get_or_create(project=project)
		try:
			updates = {key: clean(request.data[key]) for key, clean in CLEANERS.items() if key in request.data}
		except _Bad as e:
			return _err(e.code, e.message, 400)
		if "live_lookup" in request.data:
			try:
				updates["live_lookup"] = clean_live_lookup(request.data["live_lookup"], settings.live_lookup)
			except ActionError as e:
				return _err(e.code, e.message, 400)
		for key, value in updates.items():
			setattr(settings, key, value)
		settings.save()
		return _ok(_settings_dict(settings, project))


class RequirementsView(APIView):
	permission_classes = [IsAuthenticated]

	def get(self, request):
		project, error = _scoped(request, admin=False)
		if error:
			return error
		return _ok(check(project))


class EnableView(APIView):
	permission_classes = [IsAuthenticated]

	def post(self, request):
		project, error = _scoped(request, admin=True)
		if error:
			return error
		if any(item["status"] == "fail" for item in check(project)):
			return _err("requirements_unmet", "Finish the required setup before going live", 409)
		if not Product.objects.filter(kb__project=project, status="active").exists():
			return _err("no_active_product", "Add at least one product with a price before going live", 409)
		settings, _ = OrderSettings.objects.get_or_create(project=project)
		settings.enabled = True
		settings.save(update_fields=["enabled", "updated_at"])
		return _ok({"enabled": True})


class DisableView(APIView):
	permission_classes = [IsAuthenticated]

	def post(self, request):
		project, error = _scoped(request, admin=True)
		if error:
			return error
		settings, _ = OrderSettings.objects.get_or_create(project=project)
		settings.enabled = False
		settings.test_mode = False  # turning ordering off ends test mode too
		settings.save(update_fields=["enabled", "test_mode", "updated_at"])
		return _ok({"enabled": False, "test_mode": False})


class TestModeView(APIView):
	permission_classes = [IsAuthenticated]

	def post(self, request):
		project, error = _scoped(request, admin=True)
		if error:
			return error
		flag = request.data.get("on")
		if not isinstance(flag, bool):
			return _err("invalid_flag", "on must be true or false", 400)
		settings, _ = OrderSettings.objects.get_or_create(project=project)
		settings.test_mode = flag
		settings.save(update_fields=["test_mode", "updated_at"])
		return _ok({"test_mode": flag})


class SetupRunView(APIView):
	permission_classes = [IsAuthenticated]

	def post(self, request):
		project, error = _scoped(request, admin=True, project_id=request.data.get("project_id"))
		if error:
			return error
		kb_id = request.data.get("kb_id")
		try:
			kb_ok = KnowledgeBase.objects.filter(pk=kb_id, project=project).exists()
		except (ValueError, ValidationError):
			kb_ok = False
		if not kb_ok:
			return _err("invalid_kb", "Knowledge base not found in this project", 400)
		run_setup(str(project.id), str(kb_id))
		return _ok({"status": "running"}, 202)


class SetupStatusView(APIView):
	permission_classes = [IsAuthenticated]

	def get(self, request):
		project, error = _scoped(request, admin=True)
		if error:
			return error
		settings, _ = OrderSettings.objects.get_or_create(project=project)
		return _ok({"status": settings.setup_status, "report": settings.setup_report})


class InstructionReviewView(APIView):
	permission_classes = [IsAuthenticated]

	def get(self, request):
		project, error = _scoped(request, admin=False)
		if error:
			return error
		return _ok([
			{"id": str(i.id), "label": i.label, "excerpt": i.message[:160]} for i in find_order_instructions(project)
		])


class InstructionResolveView(APIView):
	"""Converts or turns off existing order-taking instructions. Nothing is deleted."""
	permission_classes = [IsAuthenticated]

	def post(self, request):
		project, error = _scoped(request, admin=True, project_id=request.data.get("project_id"))
		if error:
			return error
		action = request.data.get("action")
		if action not in RESOLVE_SCOPES:
			return _err("invalid_action", "action must be convert or turn_off", 400)
		ids = request.data.get("ids")
		if not isinstance(ids, list) or not ids:
			return _err("invalid_ids", "ids must be a non-empty list", 400)
		try:
			rows = list(CustomInstruction.objects.filter(project=project, id__in=ids))
		except (ValueError, ValidationError):
			return _err("invalid_ids", "ids must be instruction ids", 400)
		warnings = []
		if action == "convert":  # the same guard as a save: a converted instruction reaches the order prompt
			for row in rows:
				verdict = check_instruction(row.message, str(project.id))
				if verdict.blocked:
					return _err("instruction_blocked", f"'{row.label}' cannot become an order instruction", 422)
				warnings += verdict.warnings
		updated = CustomInstruction.objects.filter(pk__in=[row.pk for row in rows]).update(
			scope=RESOLVE_SCOPES[action], updated_at=timezone.now(),
		)
		return _ok({"updated": updated, "scope": RESOLVE_SCOPES[action], "warnings": warnings})


class InstructionImproveView(APIView):
	"""A safer rewrite of an instruction. Nothing is applied; the client decides."""
	permission_classes = [IsAuthenticated]

	def post(self, request):
		project, error = _scoped(request, admin=True, project_id=request.data.get("project_id"))
		if error:
			return error
		text = str(request.data.get("text", "")).strip()
		scope = request.data.get("scope", "general")
		if not text:
			return _err("invalid_text", "text is required", 400)
		if scope not in ("general", "order"):
			return _err("invalid_scope", "scope must be general or order", 400)
		try:
			return _ok(improve(text, scope, str(project.id)))
		except NoSafeSuggestion:
			return _err("no_safe_suggestion", "No safe rewrite was found; edit the text yourself", 422)


ORDER_STATUSES = [status_value for status_value, _ in Order.STATUS_CHOICES]
HIDDEN_STATUSES = ("draft", "abandoned")  # shown only when a status filter names them
ORDER_CUSTOMER_FIELDS = ("customer_name", "customer_phone", "customer_address", "zone_name", "customer_note", "custom")


def _order_row(order) -> dict:
	return {
		"id": str(order.id), "number": order.number, "status": order.status, "channel": order.channel,
		"is_test": order.is_test, "customer_name": order.customer_name, "customer_phone": order.customer_phone,
		"zone_name": order.zone_name, "currency": order.currency, "total": str(order.total),
		"collect_amount": str(order.collect_amount),
		"conversation_id": str(order.conversation_id) if order.conversation_id else None,
		"created_at": order.created_at.isoformat(),
	}


def _order_detail(order) -> dict:
	row = _order_row(order)
	row.update({
		"customer_address": order.customer_address, "customer_note": order.customer_note, "custom": order.custom,
		"subtotal": str(order.subtotal), "zone_charge": str(order.zone_charge), "advance_paid": str(order.advance_paid),
		"tracking_number": order.tracking_number, "internal_note": order.internal_note,
		"submitted_at": order.submitted_at.isoformat() if order.submitted_at else None,
		"items": [{
			"id": str(i.id), "product_id": str(i.product_id) if i.product_id else None,
			"variant_id": str(i.variant_id) if i.variant_id else None, "name": i.name, "sku": i.sku,
			"variant_label": i.variant_label, "unit_price": str(i.unit_price), "quantity": i.quantity,
			"line_total": str(i.line_total),
		} for i in order.items.all()],
		"history": [{
			"from_status": h.from_status, "to_status": h.to_status, "actor_type": h.actor_type,
			"actor_id": h.actor_id, "created_at": h.created_at.isoformat(),
		} for h in order.history.all()],
	})
	return row


def _order_or_error(request, order_id, admin: bool = False):
	"""(order, None) when the caller is a member of the order's project, else (None, error response)."""
	try:
		order = Order.objects.get(pk=order_id)
	except (Order.DoesNotExist, ValueError, ValidationError):
		return None, _err("not_found", "Order not found", 404)
	try:
		project_for(request, str(order.project_id), admin=admin)
	except Http404:
		return None, _err("not_found", "Order not found", 404)
	except PermissionDenied:
		return None, _err("access_denied", "Admin access required", 403)
	return order, None


def _money(value) -> Decimal:
	try:
		return Decimal(str(value))
	except InvalidOperation:
		raise _Bad("invalid_advance", "advance must be a number")


def _apply_edit(order, request) -> None:
	data = request.data
	settings, _ = OrderSettings.objects.get_or_create(project_id=order.project_id)
	if "items" in data:
		apply_lines(order, data["items"] or [], settings)
	customer = {key: data[key] for key in ORDER_CUSTOMER_FIELDS if key in data}
	if customer:
		update_customer(order, customer, settings)
	if "advance_paid" in data:
		advance = _money(data["advance_paid"])
		if advance < 0 or advance > order.total:
			raise _Bad("invalid_advance", "advance must be between 0 and the total")
		order.advance_paid = advance
		order.collect_amount = collect_amount(order.total, advance)
	if "internal_note" in data:
		order.internal_note = str(data["internal_note"] or "")
	if "tracking_number" in data:
		order.tracking_number = str(data["tracking_number"] or "").strip()
	order.save()
	if "status" in data and data["status"] != order.status:
		if data["status"] not in ORDER_STATUSES:
			raise _Bad("invalid_status", "unknown status")
		set_status(order, data["status"], ("user", str(request.user.id)))


def _filtered_orders(project, params):
	"""The project's orders matching the list filters: (queryset, None), or (None, error response)."""
	qs = Order.objects.filter(project=project)
	if params.get("status"):
		if params["status"] not in ORDER_STATUSES:
			return None, _err("invalid_status", "unknown status", 400)
		qs = qs.filter(status=params["status"])
	else:
		qs = qs.exclude(status__in=HIDDEN_STATUSES)
	if params.get("channel"):
		qs = qs.filter(channel=params["channel"])
	if params.get("conversation_id"):
		try:
			qs = qs.filter(conversation_id=params["conversation_id"])
		except (ValueError, ValidationError):
			return None, _err("invalid_conversation", "conversation_id must be an id", 400)
	for key, lookup in (("date_from", "created_at__date__gte"), ("date_to", "created_at__date__lte")):
		if params.get(key):
			day = parse_date(params[key])
			if day is None:
				return None, _err("invalid_date", f"{key} must be YYYY-MM-DD", 400)
			qs = qs.filter(**{lookup: day})
	if params.get("is_test") in ("true", "false"):
		qs = qs.filter(is_test=params["is_test"] == "true")
	if params.get("q"):
		text = params["q"].strip()
		match = Q(customer_name__icontains=text) | Q(customer_phone__icontains=text)
		if text.isdigit():
			match |= Q(number=int(text))
		qs = qs.filter(match)
	return qs, None


class OrdersListView(APIView):
	permission_classes = [IsAuthenticated]

	def get(self, request):
		project, error = _scoped(request, admin=False)
		if error:
			return error
		qs, error = _filtered_orders(project, request.query_params)
		if error:
			return error
		try:
			page = max(int(request.query_params.get("page", 1)), 1)
			size = min(max(int(request.query_params.get("page_size", 25)), 1), 100)
		except ValueError:
			return _err("invalid_page", "page and page_size must be numbers", 400)
		orders = qs.order_by("-created_at")[(page - 1) * size: page * size]
		return _ok({"orders": [_order_row(o) for o in orders], "count": qs.count()})

	def post(self, request):
		"""Create an order from a chat. Submitted when complete; otherwise it stays a draft and lists what is missing."""
		conv, error = _conversation_or_error(request, request.data.get("conversation_id"))
		if error:
			return error
		settings, _ = OrderSettings.objects.get_or_create(project=conv.project)  # an existing draft is reused, not refused
		try:
			with transaction.atomic():  # a refused line must not leave an empty draft behind
				order = get_or_create_draft(conv, settings)
				apply_lines(order, request.data.get("items") or [], settings)
				update_customer(order, request.data.get("customer") or {}, settings)
		except OrderValidationError as e:
			return Response(
				{"success": False, "error": {"code": "order_invalid", "message": "The order cannot be saved as sent", "problems": e.problems}},
				status=422,
			)
		except ValidationError as e:
			return _err("invalid", " ".join(e.messages), 422)
		problems = validate_for_submit(order, settings)
		if problems:
			return _ok({"order_number": None, "status": "draft", "problems": problems}, 201)
		order = submit_order(order, ("user", str(request.user.id)))
		return _ok({"order_number": order.number, "status": order.status}, 201)


class OrdersExportView(APIView):
	"""CSV of the orders matching the list filters. Admins only: the export carries every customer's details."""
	permission_classes = [IsAuthenticated]

	def get(self, request):
		project, error = _scoped(request, admin=True)
		if error:
			return error
		qs, error = _filtered_orders(project, request.query_params)
		if error:
			return error
		# shortcut: the whole file is built in memory, fine for one shop's export; stream it past tens of thousands of orders
		response = HttpResponse(content_type="text/csv")
		response["Content-Disposition"] = 'attachment; filename="orders.csv"'
		write_orders_csv(response, qs.order_by("-created_at").prefetch_related("items"))
		return response


class OrderDetailView(APIView):
	permission_classes = [IsAuthenticated]

	def get(self, request, order_id):
		order, error = _order_or_error(request, order_id)
		if error:
			return error
		return _ok(_order_detail(order))

	def patch(self, request, order_id):
		order, error = _order_or_error(request, order_id)
		if error:
			return error
		try:
			with transaction.atomic():
				_apply_edit(order, request)
		except OrderStatusError as e:
			return _err(e.code, e.message, 422)
		except _Bad as e:
			return _err(e.code, e.message, 422)
		except OrderValidationError as e:
			return Response(
				{"success": False, "error": {"code": "order_invalid", "message": "The order cannot be saved as sent", "problems": e.problems}},
				status=422,
			)
		except ValidationError as e:
			return _err("invalid", " ".join(e.messages), 422)
		order.refresh_from_db()
		return _ok(_order_detail(order))


class BulkStatusView(APIView):
	permission_classes = [IsAuthenticated]

	def post(self, request):
		new_status = request.data.get("status")
		ids = request.data.get("ids")
		if new_status not in ORDER_STATUSES:
			return _err("invalid_status", "unknown status", 400)
		if not isinstance(ids, list) or not ids:
			return _err("invalid_ids", "ids must be a non-empty list", 400)
		results = []
		for order_id in ids:
			order, error = _order_or_error(request, order_id)
			if error:
				results.append({"id": str(order_id), "ok": False, "error": "not_found"})
				continue
			if order.status != new_status:
				try:
					set_status(order, new_status, ("user", str(request.user.id)))
				except OrderStatusError as e:
					results.append({"id": str(order.id), "ok": False, "error": e.code})
					continue
			results.append({"id": str(order.id), "ok": True})
		return _ok({"results": results})


def _conversation_or_error(request, conversation_id):
	"""(conversation, None) when the caller is a member of its project, else (None, error response)."""
	try:
		conv = Conversation.objects.select_related("project", "knowledge_base", "contact").get(pk=conversation_id)
	except (Conversation.DoesNotExist, ValueError, ValidationError):
		return None, _err("not_found", "Conversation not found", 404)
	try:
		project_for(request, str(conv.project_id))
	except Http404:
		return None, _err("not_found", "Conversation not found", 404)
	return conv, None


class FromChatView(APIView):
	"""Suggested order lines and customer details from a chat. Nothing is saved."""
	permission_classes = [IsAuthenticated]

	def post(self, request):
		conv, error = _conversation_or_error(request, request.data.get("conversation_id"))
		if error:
			return error
		return _ok(prefill_from_conversation(conv))


def _action_dict(action) -> dict:
	return {
		"id": action.id, "name": action.name, "method": action.method, "url": action.url,
		"headers": header_summary(action.headers), "body_template": action.body_template,
		"response_mapping": action.response_mapping, "success_status": action.success_status,
		"success_codes": action.success_codes, "auto_run": action.auto_run, "is_active": action.is_active,
	}


def _action_input(action) -> dict:
	"""The stored action as request data. Headers carry no values, so a blank value keeps each secret."""
	return {
		"name": action.name, "method": action.method, "url": action.url, "body_template": action.body_template,
		"response_mapping": action.response_mapping, "success_status": action.success_status,
		"success_codes": action.success_codes, "auto_run": action.auto_run, "is_active": action.is_active,
		"headers": [{"name": h["name"], "value": ""} for h in action.headers],
	}


def _action_or_error(request, action_id: int, admin: bool = False):
	action = OrderAction.objects.filter(pk=action_id).first()
	if action is None:
		return None, _err("not_found", "Action not found", 404)
	_, err = _scoped(request, admin, action.project_id)
	return (action, None) if err is None else (None, err)


class ActionListView(APIView):
	permission_classes = [IsAuthenticated]

	def get(self, request):
		project, err = _scoped(request, admin=False)
		if err:
			return err
		return _ok([_action_dict(a) for a in OrderAction.objects.filter(project=project).order_by("name")])

	def post(self, request):
		project, err = _scoped(request, admin=True)
		if err:
			return err
		try:
			fields = validate_action(request.data, [])
			with transaction.atomic():
				action = OrderAction.objects.create(project=project, **fields)
		except ActionError as e:
			return _err(e.code, e.message, 400)
		except IntegrityError:
			return _err("name_taken", "An action with this name already exists", 409)
		return _ok(_action_dict(action), 201)


class ActionDetailView(APIView):
	permission_classes = [IsAuthenticated]

	def get(self, request, action_id):
		action, err = _action_or_error(request, action_id)
		return err or _ok(_action_dict(action))

	def patch(self, request, action_id):
		action, err = _action_or_error(request, action_id, admin=True)
		if err:
			return err
		try:
			fields = validate_action({**_action_input(action), **request.data}, action.headers)
			with transaction.atomic():
				for key, value in fields.items():
					setattr(action, key, value)
				action.save()
		except ActionError as e:
			return _err(e.code, e.message, 400)
		except IntegrityError:
			return _err("name_taken", "An action with this name already exists", 409)
		return _ok(_action_dict(action))

	def delete(self, request, action_id):
		action, err = _action_or_error(request, action_id, admin=True)
		if err:
			return err
		action.delete()
		return _ok({"deleted": True})


def _run_dict(run) -> dict:
	return {
		"id": run.id, "action_id": run.action_id, "action_name": run.action.name, "order_id": str(run.order_id),
		"status": run.status, "attempt": run.attempt, "http_status": run.http_status, "error": run.error,
		"response_excerpt": run.response_excerpt, "created_at": run.created_at.isoformat(),
		"finished_at": run.finished_at.isoformat() if run.finished_at else None,
	}


def _uuids(values) -> list | None:
	try:
		return [uuid.UUID(str(v)) for v in values]
	except (ValueError, TypeError):
		return None


def _sent_conflict(numbers: list):
	return Response({"success": False, "error": {
		"code": "already_sent", "message": "Some orders were already sent by this action",
		"details": [str(n) for n in numbers],
	}}, status=409)


class ActionRunView(APIView):
	permission_classes = [IsAuthenticated]

	def post(self, request, action_id):
		action, err = _action_or_error(request, action_id)
		if err:
			return err
		raw = request.data.get("order_ids")
		ids = _uuids(raw) if isinstance(raw, list) and raw else None
		if not ids:
			return _err("invalid_ids", "order_ids must be a non-empty list", 400)
		force = bool(request.data.get("force"))
		orders = list(Order.objects.filter(project=action.project, id__in=ids, status__in=SENDABLE))
		with transaction.atomic():
			OrderAction.objects.select_for_update().get(pk=action.pk)  # one bulk run at a time per action
			sent = already_sent_orders(action, orders)
			if sent and not force:
				return _sent_conflict([o.number for o in sent])
			result = run_action_for_orders(action, orders, ("user", str(request.user.id)), force=force)
		return _ok(result)


class ActionProgressView(APIView):
	permission_classes = [IsAuthenticated]

	def get(self, request, action_id):
		action, err = _action_or_error(request, action_id)
		if err:
			return err
		runs = OrderActionRun.objects.filter(action=action)
		if request.query_params.get("order_ids"):
			ids = _uuids(request.query_params["order_ids"].split(","))
			if ids is None:
				return _err("invalid_ids", "order_ids must be UUIDs", 400)
			runs = runs.filter(order_id__in=ids)
		latest = {}  # newest run per order decides its status
		for order_id, status in runs.order_by("order_id", "-id").values_list("order_id", "status"):
			latest.setdefault(order_id, status)
		counts = {"queued": 0, "running": 0, "succeeded": 0, "failed": 0}
		for status in latest.values():
			counts[status] = counts.get(status, 0) + 1
		return _ok({"counts": counts, "total": len(latest)})


class ActionRunListView(APIView):
	permission_classes = [IsAuthenticated]

	def get(self, request):
		order, err = _order_or_error(request, request.query_params.get("order_id"))
		if err:
			return err
		runs = OrderActionRun.objects.filter(order=order).select_related("action").order_by("-created_at", "-id")
		return _ok([_run_dict(r) for r in runs])


class ActionRunRetryView(APIView):
	permission_classes = [IsAuthenticated]

	def post(self, request, run_id):
		run = OrderActionRun.objects.select_related("action", "order").filter(pk=run_id).first()
		if run is None:
			return _err("not_found", "Run not found", 404)
		_, err = _scoped(request, admin=False, project_id=run.action.project_id)
		if err:
			return err
		if run.status != "failed":
			return _err("not_retryable", "Only a failed run can be retried", 409)
		with transaction.atomic():
			OrderAction.objects.select_for_update().get(pk=run.action_id)
			if already_sent_orders(run.action, [run.order]):  # a later attempt may already have succeeded
				return _sent_conflict([run.order.number])
			new = OrderActionRun.objects.create(
				action=run.action, order=run.order, status="queued", attempt=run.attempt + 1,
				actor_type="user", actor_id=str(request.user.id),
			)
			transaction.on_commit(lambda: run_action_run.delay(new.id))
		return _ok(_run_dict(new), 201)


class ActionTestView(APIView):
	"""Runs one order's request now, in this request, and records it like any other run."""
	permission_classes = [IsAuthenticated]

	def post(self, request, action_id):
		action, err = _action_or_error(request, action_id)
		if err:
			return err
		try:
			number = int(request.data.get("order_number"))
		except (TypeError, ValueError):
			return _err("invalid_order", "order_number must be a number", 400)
		order = Order.objects.filter(project=action.project, number=number, status__in=SENDABLE).first()
		if order is None:
			return _err("not_found", "No sendable order with that number", 404)
		if already_sent_orders(action, [order]) and not request.data.get("force"):
			return _sent_conflict([number])
		run = OrderActionRun.objects.create(
			action=action, order=order, status="queued", actor_type="user", actor_id=str(request.user.id),
		)
		execute_run(run.id)
		run.refresh_from_db()
		return _ok({"ok": run.status == "succeeded", "http_status": run.http_status, "error": run.error,
		            "response_excerpt": run.response_excerpt})


class NotificationsView(APIView):
	"""Order email recipients, and a summary of the project's POST integration. The webhook itself is managed
	under /api/channels/webhook. Its URL receives every project event (conversation.handoff, message.staff_reply and
	the order events), so receivers filter on the event field."""
	permission_classes = [IsAuthenticated]

	def get(self, request):
		project, error = _scoped(request, admin=False)
		if error:
			return error
		settings, _ = OrderSettings.objects.get_or_create(project=project)
		hook = ProjectWebhook.objects.filter(project=project).first()
		webhook = {"url": hook.url, "is_active": hook.is_active, "has_secret": bool(hook.secret)} if hook else {
			"url": "", "is_active": False, "has_secret": False,
		}
		return _ok({"emails": settings.notification_emails, "webhook": webhook})

	def patch(self, request):
		project, error = _scoped(request, admin=True)
		if error:
			return error
		try:
			emails = _emails(request.data.get("emails"))
		except _Bad as e:
			return _err(e.code, e.message, 400)
		OrderSettings.objects.update_or_create(project=project, defaults={"notification_emails": emails})
		return self.get(request)
