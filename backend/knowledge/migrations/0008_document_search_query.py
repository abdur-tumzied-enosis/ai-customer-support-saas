from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('knowledge', '0007_document_crawl_depth_document_images_metadata_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='document',
            name='search_query',
            field=models.CharField(blank=True, default='', help_text='Optional query used to filter and extract targeted content during crawl', max_length=500),
        ),
    ]
