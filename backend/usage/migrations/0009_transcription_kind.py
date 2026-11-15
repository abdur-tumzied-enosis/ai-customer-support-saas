from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('usage', '0008_usageevent_rerank_kind'),
    ]

    operations = [
        migrations.AlterField(
            model_name='usageevent',
            name='kind',
            field=models.CharField(choices=[('chat_in', 'Chat Input'), ('chat_out', 'Chat Output'), ('embed', 'Embedding'), ('image', 'Image'), ('rerank', 'Rerank'), ('transcription', 'Transcription')], max_length=16),
        ),
    ]
