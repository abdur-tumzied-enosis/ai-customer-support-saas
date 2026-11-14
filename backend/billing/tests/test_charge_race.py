import threading
from decimal import Decimal

from django.db import connections
from django.test import TransactionTestCase

from accounts.models import Project
from billing import service


class ChargeIfAffordableRaceTest(TransactionTestCase):
	def test_two_concurrent_charges_cannot_both_pass(self):
		project = Project.objects.create(name="P", slug="p-race")
		service.grant(project, Decimal("60"), "adjust", key="seed")   # room for one 60-unit charge, not two
		barrier, results = threading.Barrier(2), []

		def worker(key):
			try:
				barrier.wait()
				results.append(service.charge_if_affordable(project, "reply", key=key, units=60))
			except service.InsufficientCredits:
				results.append(None)
			finally:
				connections.close_all()

		threads = [threading.Thread(target=worker, args=(k,)) for k in ("a", "b")]
		for t in threads: t.start()
		for t in threads: t.join()
		assert len([r for r in results if r is not None]) == 1
		assert service.balance_of(project) == Decimal("0.00")
