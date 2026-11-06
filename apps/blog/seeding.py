"""Editorial article seeding through the blog post service and workflow."""

from __future__ import annotations

from collections.abc import Sequence

from django.contrib.auth.base_user import AbstractBaseUser

from apps.blog.models import Category, Post, PostStatus
from apps.blog.services import workflow
from apps.blog.services.post_service import PostService
from apps.core.seed_data.editorial import ARTICLES, CATEGORIES, ArticleSeed
from apps.core.seeding import SeedReport
from apps.tags.services import ensure_curated_tag, refresh_usage_counts


def _category(slug: str, name: str, report: SeedReport) -> Category:
    category = (
        Category.objects.filter(slug=slug).first()
        or Category.objects.filter(name=name).first()
    )
    created = category is None
    if category is None:
        category = Category.objects.create(slug=slug, name=name)
    report.record("blog categories", created=created)
    return category


def _attach_tags(post: Post, names: Sequence[str]) -> None:
    tags = [tag for tag in map(ensure_curated_tag, names) if tag is not None]
    if tags:
        post.tags.add(*tags)
        refresh_usage_counts(tag.pk for tag in tags)


def seed_editorial(
    *,
    author: AbstractBaseUser,
    publisher: AbstractBaseUser,
    articles: Sequence[ArticleSeed] = ARTICLES,
) -> SeedReport:
    """Create each article as a draft, request review as its author and publish it.

    The publisher is a different staff account holding the blog change permission,
    so publication goes through the same permission check as in production.
    """
    report = SeedReport()
    categories = {
        seed.slug: _category(seed.slug, seed.name, report) for seed in CATEGORIES
    }
    for article in articles:
        post = Post.objects.filter(slug=article.slug).first()
        created = post is None
        if post is None:
            post = PostService().create_post(
                title=article.title,
                body=article.body,
                author=author,
                summary=article.summary,
                category=categories[article.category],
                slug=article.slug,
                seo_title=article.seo_title,
                seo_description=article.seo_description,
                featured=article.featured,
                allow_comments=True,
            )
        report.record("articles", created=created)
        if post.status == PostStatus.DRAFT:
            workflow.request_review(post, user=author)
        if post.status == PostStatus.IN_REVIEW:
            workflow.publish(post, user=publisher)
            report.record("articles published", created=True)
        _attach_tags(post, article.tags)
    return report
