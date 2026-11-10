import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def backfill_created_by(apps, schema_editor):
	Project = apps.get_model("accounts", "Project")
	ProjectUser = apps.get_model("accounts", "ProjectUser")
	for project in Project.objects.filter(created_by__isnull=True):
		first = ProjectUser.objects.filter(project=project, role="admin").order_by("created_at").first()
		if first:
			Project.objects.filter(pk=project.pk).update(created_by_id=first.user_id)


class Migration(migrations.Migration):

	dependencies = [
		("accounts", "0005_project_currency"),
	]

	operations = [
		migrations.AddField(
			model_name="project",
			name="created_by",
			field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_projects", to=settings.AUTH_USER_MODEL),
		),
		migrations.RunPython(backfill_created_by, migrations.RunPython.noop),
	]
