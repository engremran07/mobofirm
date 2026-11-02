"""
Comprehensive tests for the generic tagging system.
"""

import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.tags.models import Tag
from apps.tags.models_tagged_item import TaggedItem
from apps.tags.services.tagging_service import TaggingService

# Import a model to test with (blog.Post)
try:
    from apps.blog.models import Post

    HAS_BLOG = True
except ImportError:
    HAS_BLOG = False

User = get_user_model()


def unique_name(base):
    """Generate a unique name using UUID suffix"""
    return f"{base}-{uuid.uuid4().hex[:8]}"


class TaggingServiceTest(TestCase):
    """Test the generic tagging service"""

    def setUp(self):
        """Set up test data"""
        self.unique_suffix = uuid.uuid4().hex[:8]
        self.service = TaggingService()
        self.user = User.objects.create_user(  # type: ignore[attr-defined]
            email=f"test-{self.unique_suffix}@test.com",
            username=f"testuser-{self.unique_suffix}",
            password="testpass123",
        )

        if HAS_BLOG:
            self.post = Post.objects.create(
                title=f"Test Post {self.unique_suffix}",
                slug=f"test-post-{self.unique_suffix}",
                body="<p>Test content</p>",
                author=self.user,
            )

    def test_tag_object_creates_tag(self):
        """Test that tagging an object creates a tag if it doesn't exist"""
        if not HAS_BLOG:
            self.skipTest("Blog app not installed")

        tag_name = unique_name("python")
        tagged_item = self.service.tag_object(self.post, tag_name, created_by=self.user)

        assert tagged_item is not None
        assert tagged_item.tag.name == tag_name
        assert tagged_item.content_object == self.post
        assert tagged_item.created_by == self.user

        # Verify tag was created
        assert Tag.objects.filter(name=tag_name).exists()

    def test_tag_object_idempotent(self):
        """Test that tagging the same object twice doesn't create duplicates"""
        if not HAS_BLOG:
            self.skipTest("Blog app not installed")

        from django.contrib.contenttypes.models import ContentType

        tag_name = unique_name("python")
        tagged_item1 = self.service.tag_object(self.post, tag_name)
        tagged_item2 = self.service.tag_object(self.post, tag_name)

        assert tagged_item1.id == tagged_item2.id  # type: ignore[attr-defined]

        # GenericForeignKey cannot be used directly in filter(),
        # use content_type and object_id instead
        content_type = ContentType.objects.get_for_model(self.post)
        assert (
            TaggedItem.objects.filter(
                content_type=content_type, object_id=self.post.pk, tag__name=tag_name
            ).count()
            == 1
        )

    def test_get_tags_for_object(self):
        """Test retrieving all tags for an object"""
        if not HAS_BLOG:
            self.skipTest("Blog app not installed")

        tag1 = unique_name("python")
        tag2 = unique_name("django")
        tag3 = unique_name("web-development")
        self.service.tag_object(self.post, tag1)
        self.service.tag_object(self.post, tag2)
        self.service.tag_object(self.post, tag3)

        tags = self.service.get_tags_for_object(self.post)

        assert tags.count() == 3
        tag_names = {tag.name for tag in tags}
        assert tag_names == {tag1, tag2, tag3}

    def test_untag_object(self):
        """Test removing a tag from an object"""
        if not HAS_BLOG:
            self.skipTest("Blog app not installed")

        tag1 = unique_name("python")
        tag2 = unique_name("django")
        self.service.tag_object(self.post, tag1)
        self.service.tag_object(self.post, tag2)

        result = self.service.untag_object(self.post, tag1)

        assert result
        tags = self.service.get_tags_for_object(self.post)
        assert tags.count() == 1
        first_tag = tags.first()
        assert first_tag is not None
        assert first_tag.name == tag2

    def test_untag_nonexistent_tag(self):
        """Test removing a tag that doesn't exist returns False"""
        if not HAS_BLOG:
            self.skipTest("Blog app not installed")

        result = self.service.untag_object(self.post, "nonexistent")
        assert not result

    def test_clear_all_tags(self):
        """Test removing all tags from an object"""
        if not HAS_BLOG:
            self.skipTest("Blog app not installed")

        self.service.tag_object(self.post, unique_name("python"))
        self.service.tag_object(self.post, unique_name("django"))
        self.service.tag_object(self.post, unique_name("web"))

        count = self.service.clear_all_tags(self.post)

        assert count == 3
        tags = self.service.get_tags_for_object(self.post)
        assert tags.count() == 0

    def test_get_objects_for_tag(self):
        """Test finding all objects with a specific tag"""
        if not HAS_BLOG:
            self.skipTest("Blog app not installed")

        post2 = Post.objects.create(
            title=f"Another Post {self.unique_suffix}",
            slug=f"another-post-{self.unique_suffix}",
            body="<p>More content</p>",
            author=self.user,
        )

        tag_name = unique_name("python")
        self.service.tag_object(self.post, tag_name)
        self.service.tag_object(post2, tag_name)

        objects = self.service.get_objects_for_tag(tag_name, Post)

        assert len(objects) == 2
        assert self.post in objects
        assert post2 in objects

    def test_bulk_tag_objects(self):
        """Test tagging multiple objects at once"""
        if not HAS_BLOG:
            self.skipTest("Blog app not installed")

        post2 = Post.objects.create(
            title=f"Post 2 {self.unique_suffix}",
            slug=f"post-2-{self.unique_suffix}",
            body="<p>Content 2</p>",
            author=self.user,
        )

        tag1 = unique_name("python")
        tag2 = unique_name("tutorial")
        tagged_items = self.service.bulk_tag_objects(
            [self.post, post2], [tag1, tag2], created_by=self.user
        )

        assert len(tagged_items) == 4  # 2 posts × 2 tags

        # Verify both posts have both tags
        post1_tags = self.service.get_tags_for_object(self.post)
        post2_tags = self.service.get_tags_for_object(post2)

        assert post1_tags.count() == 2
        assert post2_tags.count() == 2

    def test_sync_tags_for_object(self):
        """Test synchronizing tags (add new, remove old)"""
        if not HAS_BLOG:
            self.skipTest("Blog app not installed")

        old_tag1 = unique_name("old-tag-1")
        old_tag2 = unique_name("old-tag-2")
        keep_tag = unique_name("keep-this")
        new_tag1 = unique_name("new-tag-1")
        new_tag2 = unique_name("new-tag-2")

        # Initial tags
        self.service.tag_object(self.post, old_tag1)
        self.service.tag_object(self.post, old_tag2)
        self.service.tag_object(self.post, keep_tag)

        # Sync to new set
        self.service.sync_tags_for_object(
            self.post, [keep_tag, new_tag1, new_tag2], created_by=self.user
        )

        tags = set(
            self.service.get_tags_for_object(self.post).values_list("name", flat=True)
        )

        assert tags == {keep_tag, new_tag1, new_tag2}
        assert old_tag1 not in tags
        assert old_tag2 not in tags

    def test_get_popular_tags(self):
        """Test getting most frequently used tags"""
        if not HAS_BLOG:
            self.skipTest("Blog app not installed")

        python_tag = unique_name("python")
        django_tag = unique_name("django")
        fastapi_tag = unique_name("fastapi")

        # Create multiple posts and tag them
        posts = []
        for i in range(5):
            post = Post.objects.create(
                title=f"Post {self.unique_suffix}-{i}",
                slug=f"post-{self.unique_suffix}-{i}",
                body="<p>Content</p>",
                author=self.user,
            )
            posts.append(post)

        # Tag with different frequencies
        for post in posts[:5]:
            self.service.tag_object(post, python_tag)
        for post in posts[:3]:
            self.service.tag_object(post, django_tag)
        for post in posts[:1]:
            self.service.tag_object(post, fastapi_tag)

        popular = self.service.get_popular_tags(limit=3)

        # Filter to only tags we created (in case of test pollution)
        popular_names = [
            t.name for t in popular if t.name in {python_tag, django_tag, fastapi_tag}
        ]

        # Python should be first (5 uses)
        assert popular_names[0] == python_tag
        python_result = next(t for t in popular if t.name == python_tag)
        assert python_result.usage_count == 5

    def test_search_tags(self):
        """Test searching for tags by name"""
        search_prefix = unique_name("pysearch")
        Tag.objects.create(
            name=f"{search_prefix}-basics", slug=f"{search_prefix}-basics"
        )
        Tag.objects.create(
            name=f"{search_prefix}-advanced", slug=f"{search_prefix}-advanced"
        )
        Tag.objects.create(name=unique_name("django"), slug=unique_name("django-slug"))

        results = self.service.search_tags(search_prefix)

        assert results.count() == 2
        names = {tag.name for tag in results}
        assert names == {f"{search_prefix}-basics", f"{search_prefix}-advanced"}

    def test_get_related_tags(self):
        """Test finding tags that appear together"""
        if not HAS_BLOG:
            self.skipTest("Blog app not installed")

        python_tag = unique_name("python")
        programming_tag = unique_name("programming")
        web_tag = unique_name("web")

        # Create posts with related tags
        for i in range(3):
            post = Post.objects.create(
                title=f"Python Post {self.unique_suffix}-{i}",
                slug=f"python-post-{self.unique_suffix}-{i}",
                body="<p>Content</p>",
                author=self.user,
            )
            self.service.tag_object(post, python_tag)
            self.service.tag_object(post, programming_tag)

        # Add one more post with python and different tag
        post = Post.objects.create(
            title=f"Python Web Post {self.unique_suffix}",
            slug=f"python-web-{self.unique_suffix}",
            body="<p>Content</p>",
            author=self.user,
        )
        self.service.tag_object(post, python_tag)
        self.service.tag_object(post, web_tag)

        related = self.service.get_related_tags(python_tag, limit=2)

        # 'programming' should be most related (appears 3 times with python)
        tag_names = [tag.name for tag in related]
        assert programming_tag in tag_names

    def test_get_tag_cloud_data(self):
        """Test generating tag cloud data with weights"""
        if not HAS_BLOG:
            self.skipTest("Blog app not installed")

        common_tag = unique_name("common")
        medium_tag = unique_name("medium")
        rare_tag = unique_name("rare")

        # Create posts with varying tag frequencies
        for i in range(10):
            post = Post.objects.create(
                title=f"Post {self.unique_suffix}-{i}",
                slug=f"post-{self.unique_suffix}-{i}",
                body="<p>Content</p>",
                author=self.user,
            )
            # Tag all with 'common'
            self.service.tag_object(post, common_tag)

            # Tag some with 'medium'
            if i < 5:
                self.service.tag_object(post, medium_tag)

            # Tag few with 'rare'
            if i < 2:
                self.service.tag_object(post, rare_tag)

        cloud_data = self.service.get_tag_cloud_data(Post, min_count=1)

        # Filter to only our tags in case of test pollution
        our_tags = {common_tag, medium_tag, rare_tag}
        our_cloud_data = [item for item in cloud_data if item["tag"] in our_tags]

        assert len(our_cloud_data) == 3

        # Find the 'common' tag
        common_item = next(item for item in our_cloud_data if item["tag"] == common_tag)
        rare_item = next(item for item in our_cloud_data if item["tag"] == rare_tag)

        # common should have higher weight than rare
        assert common_item["weight"] > rare_item["weight"]
        assert common_item["count"] == 10
        assert rare_item["count"] == 2


class TaggedItemModelTest(TestCase):
    """Test the TaggedItem model directly"""

    def setUp(self):
        import uuid

        self.unique_suffix = str(uuid.uuid4())[:8]
        self.user = User.objects.create_user(  # type: ignore[attr-defined]
            email=f"test-{self.unique_suffix}@test.com",
            username=f"testuser-{self.unique_suffix}",
        )

        if HAS_BLOG:
            self.post = Post.objects.create(
                title=f"Test Post {self.unique_suffix}",
                slug=f"test-post-{self.unique_suffix}",
                body="<p>Test</p>",
                author=self.user,
            )

    def test_tagged_item_string_representation(self):
        """Test the string representation of TaggedItem"""
        if not HAS_BLOG:
            self.skipTest("Blog app not installed")

        tag_name = f"test-tag-{self.unique_suffix}"
        Tag.objects.create(name=tag_name, slug=tag_name)
        service = TaggingService()
        tagged_item = service.tag_object(self.post, tag_name)

        str_repr = str(tagged_item)
        assert tag_name in str_repr

    def test_target_model_name_property(self):
        """Test the target_model_name property"""
        if not HAS_BLOG:
            self.skipTest("Blog app not installed")

        service = TaggingService()
        tag_name = f"test-{self.unique_suffix}"
        tagged_item = service.tag_object(self.post, tag_name)

        model_name = tagged_item.target_model_name
        assert model_name.lower() == "post"
