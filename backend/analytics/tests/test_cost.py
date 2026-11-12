from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Project, ProjectUser
from usage.models import UsageEvent


# class CostByKindTest(TestCase):
	# def test_cost_by_kind_sums_per_kind_and_skips_null(self):
		# cache.clear()
		# user = get_user_model().objects.create_user(username="a@x.com", email="a@x.com", password="pw-123456")
		# project = Project.objects.create(name="P", slug="p")
		# ProjectUser.objects.create(project=project, user=user)
		# for kind, cost in [("embed", Decimal("0.01")), ("rerank", Decimal("0.02")), ("image", None)]:
			# UsageEvent.objects.create(project=project, kind=kind, tokens=10, cost_usd=cost)
		# client = APIClient()
		# client.force_login(user)
		# data = client.get("/api/analytics/overview/").json()["data"]["token_usage"]
		# self.assertEqual(data["cost_by_kind"], {"embed": "0.01", "rerank": "0.02"})


class ReplyPathBookingTest(TestCase):
	def test_search_books_query_embed_and_rerank(self):
		from types import SimpleNamespace
		from unittest import mock
		from langchain_core.documents import Document
		from agents.embeddings_api import Embedded, Reranked
		from agents.rag import Context, _search

		project = Project.objects.create(name="P", slug="p2")
		embedder = SimpleNamespace(last_usage=Embedded([[0.1]], 7, Decimal("0.001"), "cohere/embed-5-fast"))
		retriever = mock.Mock(vectorstore=SimpleNamespace(embeddings=embedder))
		retriever.invoke.return_value = [Document(page_content="a")]
		ctx = Context(project_id=str(project.id))
		with mock.patch("agents.vector_store.rerank_scores", return_value=Reranked([0.9], 3, Decimal("0.002"), "cohere/rerank")):
			_search([retriever], "q", ctx)
		costs = dict(UsageEvent.objects.filter(project=project).values_list("kind", "cost_usd"))
		self.assertEqual(costs, {"embed": Decimal("0.001"), "rerank": Decimal("0.002")})
