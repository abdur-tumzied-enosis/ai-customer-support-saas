from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('knowledge', '0008_document_search_query'),
    ]

    operations = [
        migrations.AddField(
            model_name='ingestionjob',
            name='started_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='ingestionjob',
            name='celery_task_kwargs',
            field=models.JSONField(blank=True, default=dict, help_text='Stored task kwargs for recovery re-dispatch'),
        ),
    ]
