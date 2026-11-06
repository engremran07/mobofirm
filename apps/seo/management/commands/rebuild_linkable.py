from __future__ import annotations

from django.core.management.base import BaseCommand
from django.urls import reverse

from apps.blog.models import Post
from apps.firmwares.models import Brand, Model
from apps.seo.services.internal_linking.engine import refresh_linkable_entity


class Command(BaseCommand):
    help = "Rebuild the LinkableEntity registry from live posts, brands and models."

    def handle(self, *args, **options):
        count = 0

        for post in Post.published():
            refresh_linkable_entity(
                post,
                title=post.title,
                url=post.get_absolute_url(),
                keywords=post.summary or "",
            )
            count += 1

        for brand in Brand.objects.all():
            refresh_linkable_entity(
                brand,
                title=brand.name,
                url=reverse("firmwares:brand_detail", kwargs={"slug": brand.slug}),
                keywords=brand.name,
            )
            count += 1

        for model in Model.objects.filter(is_active=True).select_related("brand"):
            refresh_linkable_entity(
                model,
                title=f"{model.brand.name} {model.name}",
                url=reverse(
                    "firmwares:model_detail",
                    kwargs={"brand_slug": model.brand.slug, "model_slug": model.slug},
                ),
                keywords=f"{model.brand.name},{model.name}",
            )
            count += 1

        self.stdout.write(self.style.SUCCESS(f"Linkable entities refreshed: {count}"))
