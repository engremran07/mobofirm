import types

import pytest
from django.utils import timezone

from apps.firmwares import access
from apps.firmwares.models import Brand, Model, Variant


@pytest.fixture
def make_user(django_user_model):
    """Create users; ``verified`` users have a confirmed email."""

    def factory(username, *, verified=True, staff=False, superuser=False):
        manager = django_user_model.objects
        values = {
            "username": username,
            "email": f"{username}@example.test",
            "email_verified_at": timezone.now() if verified else None,
        }
        if superuser:
            return manager.create_superuser(**values)
        return manager.create_user(is_staff=staff, **values)

    return factory


@pytest.fixture
def catalog(request):
    request.getfixturevalue("db")
    brand = Brand.objects.create(name="Acme", slug="acme")
    model = Model.objects.create(brand=brand, name="Phone One", slug="phone-one")
    variant = Variant.objects.create(model=model, name="Global", slug="global")
    return types.SimpleNamespace(brand=brand, model=model, variant=variant)


class FakeEntitlementError(Exception):
    pass


class FakeShop:
    """Stand-in for ``apps.shop.entitlements`` with controllable entitlements."""

    EntitlementError = FakeEntitlementError

    def __init__(self):
        self.purchased: set[tuple[int, object]] = set()
        self.featured_users: set[int] = set()
        self.exhausted_users: set[int] = set()
        self.consumed: list[tuple[int, object, str]] = []
        self.fail_consume = False
        self.max_concurrent = 0
        self.ad_free_users: set[int] = set()

    def has_purchased(self, user, firmware):
        return (user.pk, firmware.pk) in self.purchased

    def effective_entitlements(self, user):
        return types.SimpleNamespace(
            can_access_featured=user.pk in self.featured_users,
            limits=types.SimpleNamespace(max_concurrent=self.max_concurrent),
            perks=types.SimpleNamespace(ad_free=user.pk in self.ad_free_users),
        )

    def check_quota(self, user, _firmware):
        if user.pk in self.exhausted_users:
            return types.SimpleNamespace(allowed=False, reason="Quota used up.")
        return types.SimpleNamespace(allowed=True, reason="")

    def consume_quota(self, user, firmware, *, reason):
        if self.fail_consume:
            raise FakeEntitlementError("Quota used up.")
        if (user.pk, firmware.pk, reason) not in self.consumed:
            self.consumed.append((user.pk, firmware.pk, reason))


@pytest.fixture(name="optional_modules")
def optional_modules_fixture(monkeypatch):
    """Replace the optional shop/marketplace modules: ``modules[name] = fake | None``."""
    modules: dict[str, object] = {}
    real = access.optional_module

    def fake(name):
        return modules[name] if name in modules else real(name)

    monkeypatch.setattr(access, "optional_module", fake)
    return modules


@pytest.fixture
def shop(optional_modules):
    fake = FakeShop()
    optional_modules[access.ENTITLEMENTS_MODULE] = fake
    return fake


@pytest.fixture
def sellers(optional_modules):
    """Mark users as approved sellers: ``sellers.add(user.pk)``."""
    approved: set[int] = set()
    optional_modules[access.SELLER_MODULE] = types.SimpleNamespace(
        is_active_seller=lambda user: user.pk in approved
    )
    return approved
