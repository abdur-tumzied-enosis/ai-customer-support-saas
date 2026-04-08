from django.apps import AppConfig


class KnowledgeConfig(AppConfig):
	default_auto_field = "django.db.models.BigAutoField"
	name = "knowledge"

	def ready(self):
		# Trigger stuck-job recovery when the Celery worker process starts up.
		# Guard against the Django dev-server double-import and migrate/shell commands.
		import sys
		running_celery = any(arg in sys.argv[0] for arg in ('celery',))
		if not running_celery:
			return
		try:
			from django.db import connection
			tables = connection.introspection.table_names()
			if 'knowledge_ingestionjob' not in tables:
				return  # DB not migrated yet
			from .tasks import recover_stuck_jobs
			recover_stuck_jobs.delay()
		except Exception:
			pass  # Never block worker startup


