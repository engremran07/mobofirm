from __future__ import annotations

import html
import xml.etree.ElementTree as ET
from datetime import datetime

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.blog.models import Category, Post, PostStatus
from apps.tags.models import Tag


class Command(BaseCommand):
    help = "Import basic posts/categories/tags from a WordPress WXR (XML) export."

    def add_arguments(self, parser):
        parser.add_argument("wxr_path", help="Path to WordPress XML export")
        parser.add_argument(
            "--author-email", help="Email of author to assign", default=None
        )
        parser.add_argument(
            "--status", help="Default status for imported posts", default="published"
        )

    def handle(self, *args, **options):
        path = options["wxr_path"]
        author_email = options.get("author_email")
        default_status = options.get("status", "published").lower()
        try:
            tree = ET.parse(path)  # noqa: S314
        except Exception as exc:
            raise CommandError(f"Failed to parse XML: {exc}")  # noqa: B904

        root = tree.getroot()
        channel = root.find("channel")
        if channel is None:
            raise CommandError("Invalid WXR: missing channel")

        User = get_user_model()
        author = None
        if author_email:
            author = User.objects.filter(email__iexact=author_email).first()

        ns = {
            "content": "http://purl.org/rss/1.0/modules/content/",
            "dc": "http://purl.org/dc/elements/1.1/",
            "wp": "http://wordpress.org/export/1.2/",
        }

        imported = 0
        for item in channel.findall("item"):
            post_type = item.findtext("wp:post_type", namespaces=ns)
            if post_type != "post":
                continue
            title = item.findtext("title") or "Untitled"
            body = item.findtext("content:encoded", namespaces=ns) or ""
            post_date = item.findtext("wp:post_date", namespaces=ns) or ""
            status = item.findtext("wp:status", namespaces=ns) or default_status
            cats = [
                c.text
                for c in item.findall("category")
                if c.get("domain") == "category"
            ]
            tags = [
                c.text
                for c in item.findall("category")
                if c.get("domain") == "post_tag"
            ]

            dt = timezone.now()
            try:
                dt = datetime.fromisoformat(post_date)
            except Exception:  # noqa: S110
                pass

            post, _created = Post.objects.get_or_create(
                title=html.unescape(title),
                defaults={
                    "body": body,
                    "summary": (body[:240] if body else "") or title,
                    "status": PostStatus.PUBLISHED if status == "publish" else status,
                    "publish_at": dt,
                    "published_at": dt,
                },
            )
            if author:
                post.author = author
            post.save()

            for cat_name in cats:
                if not cat_name:
                    continue
                cat_obj, _ = Category.objects.get_or_create(name=cat_name.strip()[:120])
                post.category = post.category or cat_obj
                post.save(update_fields=["category"])

            for tag_name in tags:
                if not tag_name:
                    continue
                tag_obj, _ = Tag.objects.get_or_create(name=tag_name.strip()[:80])
                post.tags.add(tag_obj)

            imported += 1

        self.stdout.write(self.style.SUCCESS(f"Imported {imported} posts from {path}"))
