# Generated migration to add name field to ChannelCredential

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('channels_app', '0007_add_default_platforms'),
    ]

    operations = [
        migrations.AddField(
            model_name='channelcredential',
            name='name',
            field=models.CharField(default='', max_length=255, help_text='User-defined name for this channel connection'),
            preserve_default=False,
        ),
    ]
