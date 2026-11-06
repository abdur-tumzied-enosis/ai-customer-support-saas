from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from orders.models import OrderAction

User = get_user_model()
SECRET = "SECRET-KEY-123"


class ActionApiTest(TestCase):
	def setUp(self):
		self.admin = User.objects.create_user(username="a@x.test", email="a@x.test", password="x")
		self.member = User.objects.create_user(username="m@x.test", email="m@x.test", password="x")
		self.project = Project.objects.create(name="P", slug="p-actions-api")
		ProjectUser.objects.create(project=self.project, user=self.admin, role="admin")
		ProjectUser.objects.create(project=self.project, user=self.member, role="member")
		self.list_url = f"/api/orders/actions/?project_id={self.project.id}"
		self.client = APIClient()
		self.client.force_authenticate(user=self.admin)
		self.action = OrderAction.objects.create(
			project=self.project, name="Courier", method="POST", url="https://courier.example.com/orders",
			headers=[{"name": "X-Api-Key", "value": SECRET}], body_template='{"id": {{ order.number }}}',
			success_codes=[200],
		)

	def payload(self, **over):
		data = {
			"name": "New", "method": "POST", "url": "https://courier.example.com/new",
			"headers": [{"name": "X-Api-Key", "value": SECRET}], "body_template": '{"id": {{ order.number }}}',
		}
		data.update(over)
		return data

	def test_headers_write_only(self):
		r = self.client.get(self.list_url)
		self.assertEqual(r.status_code, 200, r.content)
		self.assertNotContains(r, SECRET)
		self.assertEqual(r.json()["data"][0]["headers"], [{"name": "X-Api-Key", "has_value": True}])

	def test_blank_value_keeps_secret_on_edit(self):
		r = self.client.patch(
			f"/api/orders/actions/{self.action.id}/?project_id={self.project.id}",
			{"headers": [{"name": "X-Api-Key", "value": ""}]}, format="json",
		)
		self.assertEqual(r.status_code, 200, r.content)
		self.action.refresh_from_db()
		self.assertEqual(self.action.headers, [{"name": "X-Api-Key", "value": SECRET}])

	def test_reserved_header_rejected(self):
		r = self.client.post(self.list_url, self.payload(headers=[{"name": "Host", "value": "x"}]), format="json")
		self.assertEqual(r.status_code, 400, r.content)
		self.assertEqual(r.json()["error"]["code"], "invalid_headers")

	def test_internal_ip_url_rejected(self):
		r = self.client.post(self.list_url, self.payload(url="http://192.168.1.10/hook"), format="json")
		self.assertEqual(r.status_code, 400, r.content)
		self.assertEqual(r.json()["error"]["code"], "internal_url")

	def test_invalid_json_body_rejected(self):
		r = self.client.post(self.list_url, self.payload(body_template='{"name": "{{ customer.name }}"}'), format="json")
		self.assertEqual(r.status_code, 400, r.content)
		self.assertEqual(r.json()["error"]["code"], "invalid_body_template")

	def test_auto_run_requires_active(self):
		r = self.client.post(self.list_url, self.payload(auto_run=True, is_active=False), format="json")
		self.assertEqual(r.status_code, 400, r.content)
		self.assertEqual(r.json()["error"]["code"], "auto_run_needs_active")

	def test_member_cannot_create(self):
		self.client.force_authenticate(user=self.member)
		r = self.client.post(self.list_url, self.payload(), format="json")
		self.assertEqual(r.status_code, 403, r.content)
