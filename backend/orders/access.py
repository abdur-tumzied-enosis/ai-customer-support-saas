from django.core.exceptions import PermissionDenied
from django.http import Http404

from knowledge.views import _project_user


def project_for(request, project_id, admin: bool = False):
	"""The caller's project: Http404 when not a member, PermissionDenied when admin is needed and not held."""
	pu = _project_user(request.user, project_id)
	if pu is None:
		raise Http404
	if admin and pu.role != "admin":
		raise PermissionDenied
	return pu.project
