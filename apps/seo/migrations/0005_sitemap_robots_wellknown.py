import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = (("seo", "0004_interlink_review"),)

    operations = (
        migrations.AddField(
            model_name="seosettings",
            name="sitemap_page_size",
            field=models.PositiveIntegerField(
                default=5000,
                help_text="URLs per sitemap file (protocol limit 50,000).",
                validators=[
                    django.core.validators.MinValueValidator(100),
                    django.core.validators.MaxValueValidator(50000),
                ],
            ),
        ),
        migrations.AddField(
            model_name="seosettings",
            name="sitemap_images_enabled",
            field=models.BooleanField(
                default=True,
                help_text="Add <image:image> entries for images that exist on this site.",
            ),
        ),
        migrations.AddField(
            model_name="seosettings",
            name="sitemap_news_enabled",
            field=models.BooleanField(
                default=False,
                help_text="Publish sitemap-news.xml with blog posts from the last 48 hours. "
                "Only enable for a site that qualifies for Google News.",
            ),
        ),
        migrations.AddField(
            model_name="seosettings",
            name="robots_crawl_delay",
            field=models.PositiveSmallIntegerField(
                default=0,
                help_text="Seconds between requests for bingbot only (Google ignores "
                "Crawl-delay). 0 omits the directive.",
                validators=[django.core.validators.MaxValueValidator(30)],
            ),
        ),
        migrations.AddField(
            model_name="seosettings",
            name="security_contact",
            field=models.CharField(
                blank=True,
                default="",
                help_text="security.txt Contact: an email address or an https:// URL.",
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name="seosettings",
            name="security_policy_url",
            field=models.URLField(
                blank=True,
                default="",
                help_text="security.txt Policy: https:// URL.",
            ),
        ),
        migrations.AddField(
            model_name="seosettings",
            name="security_expires",
            field=models.DateTimeField(
                blank=True,
                help_text="security.txt Expires (required; the file is served only while "
                "this date is in the future).",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="seosettings",
            name="humans_txt_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="seosettings",
            name="humans_txt",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Body of /humans.txt.",
                max_length=4000,
            ),
        ),
    )
