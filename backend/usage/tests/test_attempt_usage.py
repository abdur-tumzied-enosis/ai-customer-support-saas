from decimal import Decimal

from django.test import TestCase

from accounts.models import Project
from usage.models import UsageEvent
from usage.utils import finish_attempt, start_attempt


class AttemptUsageTest(TestCase):
	def setUp(self):
		self.project = Project.objects.create(name="P", slug="p-attempt")

	def test_start_records_unknown_cost(self):
		pk = start_attempt(str(self.project.id), "elevenlabs/scribe-v2", seconds=12)
		e = UsageEvent.objects.get(pk=pk)
		assert (e.kind, e.cost_usd, e.cost_source) == ("transcription", None, "unknown")
		assert e.metadata == {"status": "started", "seconds": 12, "attachment_index": 0}

	def test_finish_records_reported_cost_then_failure_keeps_null(self):
		pk = start_attempt(str(self.project.id), "m", seconds=12)
		finish_attempt(pk, tokens=9, cost_usd=Decimal("0.002"), status="reported", usage={"cost": 0.002})
		e = UsageEvent.objects.get(pk=pk)
		assert (e.tokens, e.cost_usd, e.cost_source) == (9, Decimal("0.002"), "provider_reported")
		assert e.metadata["status"] == "reported"

	def test_finish_without_cost_stays_unknown(self):
		pk = start_attempt(str(self.project.id), "m", seconds=12)
		finish_attempt(pk, tokens=0, cost_usd=None, status="failed")
		e = UsageEvent.objects.get(pk=pk)
		assert (e.cost_usd, e.cost_source) == (None, "unknown")

	def test_never_raises_on_bad_input(self):
		assert start_attempt("not-a-uuid", "m", seconds=1) is None
		finish_attempt("not-a-pk", tokens=0, cost_usd=None, status="failed")
		finish_attempt("00000000-0000-0000-0000-000000000000", tokens=0, cost_usd=None, status="failed")
