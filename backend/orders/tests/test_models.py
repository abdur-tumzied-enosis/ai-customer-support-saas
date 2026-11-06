from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.test import RequestFactory, TestCase

from accounts.models import Project, ProjectUser
from channels_app.models import Conversation, CustomerContact
from knowledge.models import KnowledgeBase
from orders.access import project_for
from orders.models import Order


class OrderModelTest(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="0123456789abcdef0123456789abcd")
		self.kb = KnowledgeBase.objects.create(project=self.project, name="default", vector_collection="vc-ord1")
		self.contact = CustomerContact.objects.create(project=self.project, platform="self", external_id="c1")
		self.conv = Conversation.objects.create(project=self.project, contact=self.contact, knowledge_base=self.kb)
		self.conv2 = Conversation.objects.create(project=self.project, contact=self.contact, knowledge_base=self.kb)

	def test_one_draft_per_conversation(self):
		Order.objects.create(project=self.project, conversation=self.conv, channel="self", status="draft")
		with self.assertRaises(IntegrityError):
			with transaction.atomic():
				Order.objects.create(project=self.project, conversation=self.conv, channel="self", status="draft")

	def test_order_number_unique_per_project(self):
		Order.objects.create(project=self.project, conversation=self.conv, channel="self", status="new", number=1001)
		with self.assertRaises(IntegrityError):
			with transaction.atomic():
				Order.objects.create(project=self.project, conversation=self.conv2, channel="self", status="new", number=1001)

	def test_number_null_for_drafts(self):
		d = Order.objects.create(project=self.project, conversation=self.conv, channel="self", status="draft")
		self.assertIsNone(d.number)

	def test_admin_required_for_admin_access(self):
		member = get_user_model().objects.create_user(username="m", password="x")
		ProjectUser.objects.create(project=self.project, user=member, role="member")
		request = RequestFactory().get("/")
		request.user = member
		with self.assertRaises(PermissionDenied):
			project_for(request, str(self.project.id), admin=True)
