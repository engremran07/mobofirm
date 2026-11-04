import os
import random
import re
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.text import slugify

from apps.ads.models import AdCreative, AdPlacement, Campaign, PlacementAssignment
from apps.blog.models import Category, Post, PostStatus

try:
    from faker import Faker

    fake = Faker()
except ImportError:
    fake = None


class Command(BaseCommand):
    help = "Scans templates for ad placements, creates placeholders, and generates dummy blog posts."

    def handle(self, *args, **options):
        self.stdout.write("Starting setup of test environment...")

        # 1. Create Placeholder Campaign & Creative
        placeholder_creative = self.create_placeholder_campaign()

        # 2. Scan for Ad Placements and assign placeholder
        self.scan_ad_placements(placeholder_creative)

        # 3. Generate Dummy Blog Posts
        self.generate_dummy_posts()

        self.stdout.write(self.style.SUCCESS("Test environment setup complete!"))

    def create_placeholder_campaign(self):
        self.stdout.write("Creating placeholder campaign and creative...")
        campaign, _ = Campaign.objects.get_or_create(
            name="Placeholder Campaign",
            defaults={
                "type": "house",
                "is_active": True,
                "start_at": timezone.now(),
                "budget": 1000.00,
            },
        )

        # Create a generic HTML creative
        creative, _ = AdCreative.objects.get_or_create(
            name="Generic Placeholder",
            campaign=campaign,
            defaults={
                "creative_type": "html",
                "html": """
                <div style="width:100%; height:100%; min-height:250px; background:#f8fafc; border:2px dashed #cbd5e1; display:flex; align-items:center; justify-content:center; border-radius:8px;">
                    <div style="text-align:center; color:#64748b;">
                        <h3 style="margin:0; font-weight:600;">Ad Space Available</h3>
                        <p style="margin:5px 0 0; font-size:0.875rem;">Contact us to advertise here</p>
                    </div>
                </div>
                """,
                "is_active": True,
                "weight": 1,
            },
        )
        return creative

    def scan_ad_placements(self, placeholder_creative):
        self.stdout.write("Scanning templates for ad placements...")

        template_dirs = []
        if hasattr(settings, "TEMPLATES"):
            for t in settings.TEMPLATES:
                template_dirs.extend(t.get("DIRS", []))

        for root, dirs, files in os.walk(settings.BASE_DIR):  # noqa: B007
            if "templates" in dirs:
                template_dirs.append(os.path.join(root, "templates"))

        regex = re.compile(r"{%\s*render_ad_slot\s+['\"]([^'\"]+)['\"]")

        count = 0
        for template_dir in template_dirs:
            for root, _, files in os.walk(template_dir):
                for file in files:
                    if file.endswith(".html"):
                        file_path = os.path.join(root, file)
                        try:
                            with open(file_path, encoding="utf-8") as f:
                                content = f.readlines()
                                for i, line in enumerate(content):
                                    matches = regex.findall(line)
                                    for slug in matches:
                                        self.register_placement(
                                            slug, file_path, i + 1, placeholder_creative
                                        )
                                        count += 1
                        except Exception as e:
                            self.stdout.write(
                                self.style.WARNING(f"Could not read {file_path}: {e}")
                            )

        self.stdout.write(f"Found and processed {count} ad placement references.")

    def register_placement(self, slug, file_path, line_number, placeholder_creative):
        rel_path = os.path.relpath(file_path, settings.BASE_DIR)
        reference = f"{rel_path}:{line_number}"

        placement, created = AdPlacement.objects.get_or_create(
            code=slug,
            defaults={
                "name": slug.replace("-", " ").replace("_", " ").title(),
                "slug": slug,
                "description": f"Auto-discovered from {rel_path}",
                "template_reference": reference,
                "is_enabled": True,
                "is_active": True,
            },
        )

        if not created:
            if placement.template_reference != reference:
                placement.template_reference = reference
                placement.save(update_fields=["template_reference"])

        # Assign placeholder if no assignments exist
        if placeholder_creative and not placement.assignments.exists():  # type: ignore[attr-defined]
            PlacementAssignment.objects.create(
                placement=placement,
                creative=placeholder_creative,
                weight=1,
                is_active=True,
            )
            self.stdout.write(f"Assigned placeholder to {slug}")

    def generate_dummy_posts(self):
        self.stdout.write("Generating 50 dummy blog posts...")

        User = get_user_model()
        if not User.objects.exists():
            self.stdout.write(
                self.style.ERROR("No users found. Please create a superuser first.")
            )
            return

        author = User.objects.filter(is_superuser=True).first() or User.objects.first()

        categories = ["Technology", "AI", "Programming", "Startups", "Gadgets"]
        cat_objs = []
        for cat_name in categories:
            c, _ = Category.objects.get_or_create(name=cat_name)
            cat_objs.append(c)

        existing_count = Post.objects.count()
        _target_count = existing_count + 50  # noqa: F841

        created_count = 0

        for i in range(50):  # noqa: B007
            title = self.get_random_title()
            slug = slugify(title)

            original_slug = slug
            counter = 1
            while Post.objects.filter(slug=slug).exists():
                slug = f"{original_slug}-{counter}"
                counter += 1

            body = self.get_random_body()

            post = Post(
                title=title,
                slug=slug,
                summary=self.get_random_text(150),
                body=body,
                author=author,
                category=random.choice(cat_objs),  # noqa: S311
                status=PostStatus.PUBLISHED,
                is_published=True,
                published_at=timezone.now() - timedelta(days=random.randint(0, 365)),  # noqa: S311
                allow_comments=True,
                reading_time=random.randint(2, 15),  # noqa: S311
            )
            post.save()
            created_count += 1

        self.stdout.write(
            self.style.SUCCESS(f"Successfully created {created_count} posts.")
        )

    def get_random_title(self):
        if fake:
            return fake.sentence(nb_words=6).rstrip(".")
        return f"Random Post Title {random.randint(1000, 9999)}"  # noqa: S311

    def get_random_text(self, length=100):
        if fake:
            return fake.text(max_nb_chars=length)
        return "Lorem ipsum dolor sit amet " * (length // 20)

    def get_random_body(self):
        if fake:
            # Generate 10-15 paragraphs to ensure we trigger the ad injector multiple times
            paragraphs = fake.paragraphs(nb=random.randint(10, 15))  # noqa: S311
            html = ""
            for p in paragraphs:
                html += f"<p>{p}</p>"
            return html
        return "<p>This is a dummy post body.</p>" * 10
