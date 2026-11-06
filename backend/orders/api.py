from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import validate_email
from django.http import Http404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from knowledge.models import CustomInstruction

from .access import project_for
from .models import OrderSettings
from .prompt import build_order_prompt

CHANNELS = {"facebook", "instagram", "whatsapp", "widget"}


class _Bad(Exception):
	def __init__(self, code: str, message: str):
		super().__init__(message)
		self.code, self.message = code, message


def _ok(data, http_status=200):
	return Response({"success": True, "data": data}, status=http_status)


def _err(code: str, message: str, http_status: int):
	return Response({"success": False, "error": {"code": code, "message": message}}, status=http_status)


def _scoped(request, admin: bool):
	"""(project, None) for an allowed caller, else (None, error response)."""
	try:
		return project_for(request, request.query_params.get("project_id"), admin=admin), None
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
	instructions = [i.message for i in CustomInstruction.effective_order_instructions(str(project.id))]
	return {
		"channels": settings.channels,
		"zones": settings.zones,
		"payment_methods": settings.payment_methods,
		"form_fields": settings.form_fields,
		"notification_emails": settings.notification_emails,
		"draft_expiry_hours": settings.draft_expiry_hours,
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
		for key, value in updates.items():
			setattr(settings, key, value)
		settings.save()
		return _ok(_settings_dict(settings, project))
