"""External download link shortener: modes, first-party codes, providers, fallbacks.

No test reaches the network: provider calls go through a fake fetcher and any code path
that would build the real ``SafeFetcher`` fails the test loudly.
"""

from __future__ import annotations

import hashlib
import logging
import types
from datetime import timedelta
from urllib.parse import quote

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.firmwares import shortener, shortener_providers
from apps.firmwares.models import (
    Brand,
    FirmwareSourceType,
    Model,
    OfficialFirmware,
)
from apps.firmwares.shortener_constants import (
    BREAKER_THRESHOLD,
    CODE_ALPHABET,
    CODE_LENGTH,
    ProviderKind,
    Result,
    ShortenerMode,
)
from apps.firmwares.shortener_models import (
    DownloadShortLink,
    DownloadShortLinkDay,
    FirmwareShortenerOverride,
    ShortenerOutcome,
    ShortenerSettings,
    ThirdPartyShortLink,
)
from apps.firmwares.shortener_providers import ProviderConfig, ProviderError
from apps.seo.fetcher import BlockedDestination, FetchResult, FetchTimeout

pytestmark = pytest.mark.django_db

EXTERNAL_URL = "https://downloads.example.invalid/external.zip"
SECRET = "sk-live-TOPSECRET-1234"
HUMAN = {"HTTP_USER_AGENT": "Mozilla/5.0 (X11; Linux x86_64) Firefox/130.0"}


def answer(
    text: str,
    status: int = 200,
    content_type: str = "text/plain",
    skipped: bool = False,
) -> FetchResult:
    return FetchResult(
        url="https://provider.test/",
        final_url="https://provider.test/",
        status=status,
        headers={"content-type": content_type},
        body=text.encode(),
        body_skipped=skipped,
    )


class FakeFetcher:
    """Replays answers (the last one repeats) and records every call."""

    def __init__(self, *answers: FetchResult | Exception) -> None:
        self.answers = list(answers) or [answer("https://is.gd/abc123")]
        self.calls: list[types.SimpleNamespace] = []

    def fetch(
        self, url, *, method="GET", headers=None, body=None, follow_redirects=True
    ):
        self.calls.append(
            types.SimpleNamespace(
                url=url,
                method=method,
                headers=dict(headers or {}),
                body=body,
                follow_redirects=follow_redirects,
            )
        )
        nxt = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    def refuse(timeout):
        raise AssertionError("A test tried to build the real SafeFetcher.")

    monkeypatch.setattr(shortener_providers, "default_fetcher", refuse)


@pytest.fixture(autouse=True)
def _site_origin_without_request(monkeypatch):
    """Contract calls without a request need a configured site address."""
    real = shortener.absolute_url

    def with_default(path, request=None):
        if request is None:
            return f"http://testserver{path}" if path.startswith("/") else path
        return real(path, request)

    monkeypatch.setattr(shortener, "absolute_url", with_default)


@pytest.fixture(name="use_fetcher")
def use_fetcher_fixture(monkeypatch):
    """Install a fake fetcher for page/view level calls that cannot pass one in."""

    def install(fetcher: FakeFetcher) -> FakeFetcher:
        monkeypatch.setattr(
            shortener_providers, "default_fetcher", lambda timeout: fetcher
        )
        return fetcher

    return install


@pytest.fixture(name="brand")
def brand_fixture():
    return Brand.objects.create(name="Short Brand", slug="short-brand")


@pytest.fixture(name="firmware")
def firmware_fixture(brand):
    device = Model.objects.create(brand=brand, name="Short Phone", slug="short-phone")
    return OfficialFirmware.objects.create(
        original_file_name="external.zip",
        source_type=FirmwareSourceType.EXTERNAL,
        stored_file_path="",
        external_url=EXTERNAL_URL,
        brand=brand,
        model=device,
        is_verified=True,
    )


@pytest.fixture(name="settings_row")
def settings_row_fixture():
    return ShortenerSettings.get_solo()


def set_mode(mode: str, **extra) -> ShortenerSettings:
    row = ShortenerSettings.get_solo()
    for name, value in {"mode": mode, **extra}.items():
        setattr(row, name, value)
    row.save()
    return row


def detail_url(firmware) -> str:
    return reverse(
        "firmwares:firmware_detail",
        kwargs={"firmware_type": "official", "firmware_id": firmware.pk},
    )


def page_url(firmware) -> str:
    return "http://testserver" + reverse(
        "firmwares:firmware_download",
        kwargs={"firmware_type": "official", "firmware_id": firmware.pk},
    )


def share_url_of(client: Client, firmware) -> str:
    response = client.get(detail_url(firmware))
    assert response.status_code == 200
    return response.context["share_url"]


# ----------------------------------------------------------- modes matrix ----


def test_off_shares_the_detail_page_and_touches_nothing(client, firmware, use_fetcher):
    fake = use_fetcher(FakeFetcher())

    assert share_url_of(client, firmware) == "http://testserver" + detail_url(firmware)
    assert DownloadShortLink.objects.count() == 0
    assert fake.calls == []


def test_first_party_shares_the_short_code_without_any_provider_call(
    client, firmware, use_fetcher
):
    fake = use_fetcher(FakeFetcher())
    set_mode(ShortenerMode.FIRST_PARTY)

    shared = share_url_of(client, firmware)

    link = DownloadShortLink.objects.get()
    assert shared == f"http://testserver/d/{link.code}/"
    assert fake.calls == []


def test_third_party_shortens_the_download_page_never_the_file_host(
    client, firmware, use_fetcher
):
    fake = use_fetcher(FakeFetcher(answer("https://is.gd/abc123")))
    set_mode(ShortenerMode.THIRD_PARTY)

    shared = share_url_of(client, firmware)

    assert shared == "https://is.gd/abc123"
    assert DownloadShortLink.objects.count() == 0
    (call,) = fake.calls
    assert quote(page_url(firmware), safe="") in call.url
    assert "downloads.example.invalid" not in call.url
    assert call.follow_redirects is False


def test_both_chains_the_first_party_link_into_the_provider(
    client, firmware, use_fetcher
):
    fake = use_fetcher(FakeFetcher(answer("https://is.gd/chained")))
    set_mode(ShortenerMode.BOTH)

    shared = share_url_of(client, firmware)

    link = DownloadShortLink.objects.get()
    assert shared == "https://is.gd/chained"
    assert quote(f"http://testserver/d/{link.code}/", safe="") in fake.calls[0].url


@pytest.mark.parametrize("mode", list(ShortenerMode))
def test_a_hosted_file_is_never_shortened(client, brand, use_fetcher, mode):
    fake = use_fetcher(FakeFetcher())
    set_mode(mode)
    device = Model.objects.create(brand=brand, name="Hosted", slug="hosted")
    hosted = OfficialFirmware.objects.create(
        original_file_name="hosted.zip",
        stored_file_path="/firmware/hosted.zip",
        brand=brand,
        model=device,
        is_verified=True,
    )

    assert share_url_of(client, hosted) == "http://testserver" + detail_url(hosted)
    assert DownloadShortLink.objects.count() == 0
    assert fake.calls == []


def test_an_unapproved_firmware_is_not_shortenable(firmware):
    set_mode(ShortenerMode.FIRST_PARTY)
    firmware.is_verified = False
    firmware.save()

    assert shortener.is_shortenable(firmware) is False
    assert shortener.shorten_download(firmware).shortened is False
    assert DownloadShortLink.objects.count() == 0


def test_the_download_page_shares_the_short_link_too(client, firmware):
    set_mode(ShortenerMode.FIRST_PARTY)
    response = client.get(
        reverse(
            "firmwares:firmware_download",
            kwargs={"firmware_type": "official", "firmware_id": firmware.pk},
        )
    )

    code = DownloadShortLink.objects.get().code
    assert response.context["share_url"] == f"http://testserver/d/{code}/"


# --------------------------------------------------------------- overrides ----


def test_brand_override_beats_the_global_mode(firmware, brand):
    set_mode(ShortenerMode.FIRST_PARTY)
    brand.shortener_mode = "off"
    brand.save()
    firmware.refresh_from_db()

    assert shortener.resolve_mode(firmware) == "off"
    assert shortener.shorten_download(firmware).shortened is False


def test_firmware_override_beats_the_brand_and_global_mode(firmware, brand):
    set_mode(ShortenerMode.OFF)
    brand.shortener_mode = "third_party"
    brand.save()
    FirmwareShortenerOverride.objects.create(
        firmware_type="official", firmware_id=firmware.pk, mode="first_party"
    )
    firmware.refresh_from_db()

    result = shortener.shorten_download(
        firmware, fetcher=FakeFetcher(AssertionError("not called"))
    )

    assert result.first_party and not result.third_party
    assert shortener.resolve_mode(firmware) == "first_party"


def test_inherit_follows_the_global_mode(firmware):
    set_mode(ShortenerMode.BOTH)

    assert shortener.resolve_mode(firmware) == "both"


def test_forcing_third_party_works_while_the_global_mode_is_off(firmware, brand):
    brand.shortener_mode = "third_party"
    brand.save()
    firmware.refresh_from_db()

    result = shortener.shorten_download(
        firmware, fetcher=FakeFetcher(answer("https://is.gd/forced"))
    )

    assert result.url == "https://is.gd/forced"


# ------------------------------------------------------- first-party links ----


def make_link(firmware) -> DownloadShortLink:
    return shortener.ensure_first_party_link(firmware)


def test_codes_are_long_unambiguous_and_idempotent(firmware):
    first = make_link(firmware)
    again = make_link(firmware)

    assert first.pk == again.pk
    assert DownloadShortLink.objects.count() == 1
    assert len(first.code) == CODE_LENGTH
    assert set(first.code) <= set(CODE_ALPHABET)
    assert first.target_digest == hashlib.sha256(EXTERNAL_URL.encode()).hexdigest()


def test_a_code_collision_retries_with_a_new_code(firmware, brand, monkeypatch):
    other = OfficialFirmware.objects.create(
        original_file_name="other.zip",
        source_type=FirmwareSourceType.EXTERNAL,
        stored_file_path="",
        external_url="https://downloads.example.invalid/other.zip",
        brand=brand,
        is_verified=True,
    )
    taken = make_link(other).code
    codes = iter([taken, "b" * CODE_LENGTH])
    monkeypatch.setattr(shortener, "new_code", lambda: next(codes))

    link = make_link(firmware)

    assert link.code == "b" * CODE_LENGTH


def test_an_exhausted_code_space_rolls_back_and_falls_back(
    firmware, brand, monkeypatch
):
    other = OfficialFirmware.objects.create(
        original_file_name="other.zip",
        source_type=FirmwareSourceType.EXTERNAL,
        stored_file_path="",
        external_url="https://downloads.example.invalid/other.zip",
        brand=brand,
        is_verified=True,
    )
    taken = make_link(other).code
    monkeypatch.setattr(shortener, "new_code", lambda: taken)
    set_mode(ShortenerMode.FIRST_PARTY)

    with pytest.raises(shortener.ShortenerError):
        make_link(firmware)
    result = shortener.shorten_download(firmware)

    assert result.url == page_url(firmware) or result.url.endswith("/download/")
    assert (result.shortened, result.fallback) == (False, "code_space_exhausted")
    assert DownloadShortLink.objects.filter(firmware_id=firmware.pk).count() == 0
    failed = ShortenerOutcome.objects.get()
    assert (failed.stage, failed.result, failed.code) == (
        "first_party",
        Result.FAILED,
        "code_space_exhausted",
    )


def test_changing_the_external_url_makes_a_new_code_and_retires_the_old_one(
    client, firmware
):
    old = make_link(firmware)
    firmware.external_url = "https://downloads.example.invalid/new-build.zip"
    firmware.save()

    new = make_link(firmware)

    assert new.code != old.code
    assert client.get(f"/d/{old.code}/").status_code == 404
    assert client.get(f"/d/{new.code}/").status_code == 302


# --------------------------------------------------------- redirect (/d/) ----


def test_a_code_redirects_through_the_download_gate(client, firmware):
    link = make_link(firmware)

    response = client.get(f"/d/{link.code}/", **HUMAN)

    expected = reverse(
        "firmwares:firmware_download",
        kwargs={"firmware_type": "official", "firmware_id": firmware.pk},
    )
    assert response.status_code == 302
    assert response["Location"] == expected
    assert response["Cache-Control"] == "no-store"
    assert "noindex" in response["X-Robots-Tag"]
    gate = client.get(expected, **HUMAN)
    assert gate.status_code == 200
    assert gate.context["download_token"]
    assert gate.context["external_host"] == "downloads.example.invalid"


def test_the_redirect_never_leaves_the_site_or_exposes_the_file_host(client, firmware):
    link = make_link(firmware)

    response = client.get(f"/d/{link.code}/", **HUMAN)

    location = response["Location"]
    assert location.startswith("/firmwares/file/official/")
    assert "//" not in location
    assert "example.invalid" not in location


@pytest.mark.parametrize(
    "code",
    [
        "aaaaaaaa",
        "ZZZZZZZZ",
        "short",
        "a" * 40,
        "..%2f..%2f",
        "https:%2f%2fevil.test",
        "0000000O",
    ],
)
def test_forged_or_malformed_codes_are_404(client, code, firmware):
    make_link(firmware)

    assert client.get(f"/d/{code}/", **HUMAN).status_code == 404
    assert DownloadShortLinkDay.objects.count() == 0


def test_a_code_pointing_at_an_unknown_type_or_firmware_is_404(client, firmware):
    DownloadShortLink.objects.create(
        code="cccccccc",
        firmware_type="nonsense",
        firmware_id=firmware.pk,
        target_digest=shortener.target_digest(EXTERNAL_URL),
    )
    DownloadShortLink.objects.create(
        code="dddddddd",
        firmware_type="official",
        firmware_id="00000000-0000-0000-0000-000000000000",
        target_digest=shortener.target_digest(EXTERNAL_URL),
    )

    assert client.get("/d/cccccccc/").status_code == 404
    assert client.get("/d/dddddddd/").status_code == 404


def test_a_tampered_digest_is_refused(client, firmware):
    link = make_link(firmware)
    DownloadShortLink.objects.filter(pk=link.pk).update(target_digest="0" * 64)

    assert client.get(f"/d/{link.code}/", **HUMAN).status_code == 404


def test_withdrawn_or_unapproved_firmware_is_404(client, firmware):
    link = make_link(firmware)
    firmware.is_active = False
    firmware.save()
    assert client.get(f"/d/{link.code}/", **HUMAN).status_code == 404
    firmware.is_active = True
    firmware.is_verified = False
    firmware.save()
    assert client.get(f"/d/{link.code}/", **HUMAN).status_code == 404


def test_a_host_removed_from_the_allow_list_is_404(client, firmware, settings):
    link = make_link(firmware)
    settings.FIRMWARE_EXTERNAL_URL_ALLOWED_HOSTS = ["other.example.test"]

    assert client.get(f"/d/{link.code}/", **HUMAN).status_code == 404


def test_only_safe_methods_are_accepted(client, firmware):
    link = make_link(firmware)

    assert client.post(f"/d/{link.code}/").status_code == 405


def test_human_clicks_are_counted_per_day_without_personal_data(client, firmware):
    link = make_link(firmware)

    client.get(f"/d/{link.code}/", **HUMAN)
    client.get(f"/d/{link.code}/", **HUMAN)

    day = DownloadShortLinkDay.objects.get()
    assert (day.clicks, day.bot_clicks) == (2, 0)
    assert day.day == timezone.now().date()
    stored = {field.name for field in DownloadShortLinkDay._meta.get_fields()}
    assert stored == {"id", "link", "day", "clicks", "bot_clicks"}


@pytest.mark.parametrize(
    "headers",
    [
        {"HTTP_USER_AGENT": "Googlebot/2.1 (+http://www.google.com/bot.html)"},
        {"HTTP_USER_AGENT": "WhatsApp/2.23 A"},
        {"HTTP_USER_AGENT": "Mozilla/5.0 facebookexternalhit/1.1"},
        {"HTTP_USER_AGENT": "python-requests/2.32"},
        {},
        {**HUMAN, "HTTP_SEC_PURPOSE": "prefetch"},
        {**HUMAN, "HTTP_X_MOZ": "prefetch"},
    ],
)
def test_bots_previews_and_prefetches_are_not_clicks(client, firmware, headers):
    link = make_link(firmware)

    response = client.get(f"/d/{link.code}/", **headers)

    assert response.status_code == 302
    day = DownloadShortLinkDay.objects.get()
    assert (day.clicks, day.bot_clicks) == (0, 1)


def test_head_requests_are_not_clicks(client, firmware):
    link = make_link(firmware)

    assert client.head(f"/d/{link.code}/", **HUMAN).status_code == 302
    day = DownloadShortLinkDay.objects.get()
    assert (day.clicks, day.bot_clicks) == (0, 1)


# ---------------------------------------------------------------- adapters ----


def config(kind: str, **extra) -> ProviderConfig:
    return ProviderConfig(kind=kind, **extra)


def test_isgd_and_tinyurl_use_fixed_keyless_endpoints():
    fake = FakeFetcher(answer("https://is.gd/x1"))
    assert shortener_providers.shorten(config("isgd"), "https://site.test/a b", fake)
    assert fake.calls[0].url == (
        "https://is.gd/create.php?format=simple&url=https%3A%2F%2Fsite.test%2Fa%20b"
    )
    fake = FakeFetcher(answer("https://tinyurl.com/x2\n"))
    assert (
        shortener_providers.shorten(config("tinyurl"), "https://site.test/", fake)
        == "https://tinyurl.com/x2"
    )
    assert fake.calls[0].url.startswith("https://tinyurl.com/api-create.php?url=")


def test_query_api_builds_the_api_key_url_and_reads_json_or_text():
    cfg = config(
        "query_api",
        endpoint="https://ouo.test/api?api={key}&url={url}&format=json",
        api_key="k/ey+1",
    )
    fake = FakeFetcher(
        answer(
            '{"status":"success","shortenedUrl":"https://ouo.test/s1"}',
            content_type="application/json",
        ),
        answer("https://ouo.test/s2"),
    )

    assert (
        shortener_providers.shorten(cfg, "https://site.test/p?q=1", fake)
        == "https://ouo.test/s1"
    )
    assert (
        shortener_providers.shorten(cfg, "https://site.test/p", fake)
        == "https://ouo.test/s2"
    )
    assert fake.calls[0].url == (
        "https://ouo.test/api?api=k%2Fey%2B1&url=https%3A%2F%2Fsite.test%2Fp%3Fq%3D1&format=json"
    )


def test_json_api_posts_a_templated_body_with_an_auth_header_and_reads_a_path():
    cfg = config(
        "json_api",
        endpoint="https://api.short.test/v1/links",
        method="POST",
        api_key="tok",
        auth_header="Authorization",
        auth_prefix="Bearer ",
        body_template='{"long": "{url}", "meta": {"k": "{key}"}, "n": 1}',
        response_path="data.links.0.short",
    )
    fake = FakeFetcher(
        answer(
            '{"data":{"links":[{"short":"https://api.short.test/zz"}]}}',
            content_type="application/json",
        )
    )

    assert (
        shortener_providers.shorten(cfg, "https://site.test/p", fake)
        == "https://api.short.test/zz"
    )
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call.method == "POST"
    assert call.headers["Authorization"] == "Bearer tok"
    assert call.headers["Content-Type"] == "application/json"
    assert call.body == (
        b'{"long": "https://site.test/p", "meta": {"k": "tok"}, "n": 1}'
    )


def test_json_api_get_uses_the_url_template():
    cfg = config("json_api", endpoint="https://api.short.test/s?u={url}", method="GET")
    fake = FakeFetcher(
        answer(
            '{"result":{"short_url":"https://api.short.test/q"}}',
            content_type="application/json",
        )
    )

    assert (
        shortener_providers.shorten(cfg, "https://site.test/", fake)
        == "https://api.short.test/q"
    )
    assert fake.calls[0].method == "GET" and fake.calls[0].body is None


@pytest.mark.parametrize(
    ("reply", "code"),
    [
        (answer("oops", status=500), "http_500"),
        (answer("slow down", status=429), "rate_limited"),
        (answer("", status=200), "empty_response"),
        (answer("Error: bad url"), "bad_short_url"),
        (answer("http://insecure.test/x"), "bad_short_url"),
        (answer("javascript:alert(1)"), "bad_short_url"),
        (answer("https://user:pw@evil.test/x"), "bad_short_url"),
        (answer("{not json"), "bad_json"),
        (
            answer(
                '{"status":"error","message":"no"}', content_type="application/json"
            ),
            "no_short_url",
        ),
        (
            answer("<html>", skipped=True, content_type="image/png"),
            "unexpected_content_type",
        ),
        (answer("", status=301), "http_301"),
    ],
)
def test_bad_provider_answers_become_stable_codes(reply, code):
    with pytest.raises(ProviderError) as caught:
        shortener_providers.shorten(
            config("isgd"), "https://site.test/", FakeFetcher(reply)
        )

    assert caught.value.code == code


def test_fetch_errors_are_reduced_to_their_code_and_drop_the_message():
    leaky = FetchTimeout(f"Timed out fetching https://ouo.test/api?api={SECRET}&url=x")
    cfg = config(
        "query_api", endpoint="https://ouo.test/api?api={key}&url={url}", api_key=SECRET
    )

    with pytest.raises(ProviderError) as caught:
        shortener_providers.shorten(cfg, "https://site.test/", FakeFetcher(leaky))

    assert caught.value.code == "timeout"
    assert SECRET not in str(caught.value) and SECRET not in repr(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True


def test_the_ssrf_guard_refusal_is_a_provider_error():
    with pytest.raises(ProviderError) as caught:
        shortener_providers.shorten(
            config("isgd"),
            "https://site.test/",
            FakeFetcher(BlockedDestination("private")),
        )

    assert caught.value.code == "blocked_destination"


def test_the_default_fetcher_is_the_bounded_safe_fetcher(monkeypatch):
    monkeypatch.undo()  # drop the autouse guard: only builds the object, no I/O
    fetcher = shortener_providers.default_fetcher(3)

    assert type(fetcher).__name__ == "SafeFetcher"
    assert fetcher.policy.total_timeout == 3.0
    assert fetcher.policy.max_redirects == 0
    assert fetcher.policy.max_bytes <= 65_536


def test_config_repr_never_contains_the_key():
    cfg = config("query_api", endpoint="https://x.test/?k={key}", api_key=SECRET)

    assert SECRET not in repr(cfg)
    assert SECRET not in repr(
        shortener_providers.Call("GET", f"https://x.test/?k={SECRET}")
    )


@pytest.mark.parametrize(
    ("endpoint", "kind", "fragment"),
    [
        ("", "query_api", "Enter"),
        ("http://x.test/?u={url}", "query_api", "https"),
        ("https://user:pw@x.test/?u={url}", "query_api", "credentials"),
        ("https://x.test/?u={url}&z={other}", "query_api", "placeholders"),
        ("https://x.test/?k={key}", "query_api", "{url}"),
    ],
)
def test_endpoint_templates_are_validated(endpoint, kind, fragment):
    problems = shortener_providers.endpoint_errors(endpoint, kind=kind)

    assert any(fragment in problem for problem in problems)


def test_endpoint_template_accepts_a_good_value_and_body_templates_are_checked():
    assert (
        shortener_providers.endpoint_errors(
            "https://x.test/?k={key}&u={url}", kind="query_api"
        )
        == []
    )
    assert shortener_providers.body_template_errors('{"u": "{url}"}') == []
    assert shortener_providers.body_template_errors("[1]")
    assert shortener_providers.body_template_errors("{bad")
    assert shortener_providers.body_template_errors('{"u": 1}')


# ----------------------------------------------- caching, breaker, fallback ----


def isgd_row(**extra) -> ShortenerSettings:
    return set_mode(ShortenerMode.THIRD_PARTY, provider_kind=ProviderKind.ISGD, **extra)


def test_the_provider_result_is_cached_per_target_and_idempotent(firmware):
    isgd_row()
    fake = FakeFetcher(answer("https://is.gd/once"))

    first = shortener.shorten_download(firmware, fetcher=fake)
    second = shortener.shorten_download(firmware, fetcher=fake)

    assert first.url == second.url == "https://is.gd/once"
    assert len(fake.calls) == 1
    cached = ThirdPartyShortLink.objects.get()
    expected_key = shortener.cache_key(ShortenerSettings.get_solo(), page_url(firmware))
    assert cached.cache_key == expected_key and len(expected_key) == 64
    assert ShortenerOutcome.objects.filter(result=Result.CREATED).count() == 1


def test_the_cache_key_changes_with_provider_endpoint_and_epoch(settings_row):
    base = shortener.cache_key(settings_row, "https://site.test/a")
    assert shortener.cache_key(settings_row, "https://site.test/b") != base
    settings_row.provider_kind = ProviderKind.TINYURL
    assert shortener.cache_key(settings_row, "https://site.test/a") != base
    settings_row.provider_kind = ProviderKind.ISGD
    settings_row.cache_epoch += 1
    assert shortener.cache_key(settings_row, "https://site.test/a") != base


def test_a_lost_cache_race_returns_the_winning_link(firmware, monkeypatch):
    row = isgd_row()
    target = page_url(firmware)

    def racing(_config, _target_url, _fetcher=None):
        ThirdPartyShortLink.objects.create(
            cache_key=shortener.cache_key(row, target),
            provider_kind="isgd",
            target_url=target,
            short_url="https://is.gd/winner",
        )
        return "https://is.gd/loser"

    monkeypatch.setattr(shortener, "shorten", racing)

    result = shortener.shorten_download(firmware)

    assert result.url == "https://is.gd/winner"
    assert ThirdPartyShortLink.objects.count() == 1


def test_a_provider_failure_falls_back_to_the_plain_page_and_caches_nothing(firmware):
    isgd_row()
    fake = FakeFetcher(answer("boom", status=503))

    result = shortener.shorten_download(firmware, fetcher=fake)

    assert result.url == page_url(firmware)
    assert (result.shortened, result.fallback) == (False, "http_503")
    assert ThirdPartyShortLink.objects.count() == 0
    failed = ShortenerOutcome.objects.get()
    assert (failed.stage, failed.result, failed.code) == (
        "third_party",
        Result.FAILED,
        "http_503",
    )
    assert failed.firmware_id == firmware.pk
    assert failed.target_host == "testserver"


def test_both_falls_back_to_the_first_party_link_when_the_provider_fails(firmware):
    set_mode(ShortenerMode.BOTH)
    fake = FakeFetcher(FetchTimeout("slow"))

    result = shortener.shorten_download(firmware, fetcher=fake)

    link = DownloadShortLink.objects.get()
    assert result.url == f"http://testserver/d/{link.code}/"
    assert (result.shortened, result.first_party, result.third_party) == (
        True,
        True,
        False,
    )
    assert result.fallback == "timeout"


def test_a_failure_never_breaks_the_firmware_page(client, firmware, use_fetcher):
    use_fetcher(FakeFetcher(FetchTimeout("slow")))
    isgd_row()

    assert share_url_of(client, firmware) == "http://testserver" + detail_url(firmware)


def test_an_unconfigured_provider_falls_back_without_a_call(firmware):
    set_mode(
        ShortenerMode.THIRD_PARTY,
        provider_kind=ProviderKind.QUERY_API,
        provider_endpoint="",
    )
    fake = FakeFetcher()

    result = shortener.shorten_download(firmware, fetcher=fake)

    assert (result.shortened, result.fallback) == (False, "misconfigured")
    assert fake.calls == []


def test_the_circuit_opens_after_repeated_failures_and_recovers(firmware):
    isgd_row()
    fake = FakeFetcher(answer("down", status=502))
    now = timezone.now()

    for _ in range(BREAKER_THRESHOLD):
        shortener.shorten_download(firmware, fetcher=fake, now=now)
    row = ShortenerSettings.get_solo()
    assert row.breaker_failures == BREAKER_THRESHOLD
    assert shortener.breaker_is_open(row, now)
    calls_when_open = len(fake.calls)

    skipped = shortener.shorten_download(firmware, fetcher=fake, now=now)

    assert len(fake.calls) == calls_when_open
    assert skipped.fallback == "circuit_open" and not skipped.shortened
    results = list(ShortenerOutcome.objects.values_list("result", flat=True))
    assert results.count(Result.FAILED) == BREAKER_THRESHOLD
    assert results.count(Result.BREAKER_OPEN) == 1

    later = now + timedelta(hours=1)
    fake.answers = [answer("https://is.gd/back")]
    healed = shortener.shorten_download(firmware, fetcher=fake, now=later)

    assert healed.url == "https://is.gd/back"
    row.refresh_from_db()
    assert (row.breaker_failures, row.breaker_open_until) == (0, None)


def test_a_failing_probe_after_the_pause_reopens_the_circuit_at_once(firmware):
    isgd_row(breaker_failures=BREAKER_THRESHOLD)
    now = timezone.now()
    fake = FakeFetcher(answer("down", status=502))

    shortener.shorten_download(firmware, fetcher=fake, now=now)

    assert shortener.breaker_is_open(ShortenerSettings.get_solo(), now)


def test_the_audit_table_keeps_only_the_newest_rows(monkeypatch):
    monkeypatch.setattr(shortener, "MAX_OUTCOME_ROWS", 3)

    for index in range(6):
        shortener.record_outcome("third_party", Result.FAILED, code=f"c{index}")

    assert list(
        ShortenerOutcome.objects.order_by("created_at", "id").values_list(
            "code", flat=True
        )
    ) == ["c3", "c4", "c5"]


# ------------------------------------------------------------ health check ----


@pytest.fixture(name="site_origin")
def site_origin_fixture(monkeypatch):
    monkeypatch.setattr(
        shortener, "absolute_url", lambda path, request=None: f"https://site.test{path}"
    )


@pytest.mark.usefixtures("site_origin")
def test_health_check_reports_success_and_clears_the_circuit():
    isgd_row(
        breaker_failures=BREAKER_THRESHOLD,
        breaker_open_until=timezone.now() + timedelta(hours=1),
    )
    fake = FakeFetcher(answer("https://is.gd/probe"))

    outcome = shortener.check_provider(fetcher=fake)

    assert outcome.ok and "is.gd" in outcome.message
    row = ShortenerSettings.get_solo()
    assert (row.last_check_ok, row.breaker_failures, row.breaker_open_until) == (
        True,
        0,
        None,
    )
    assert fake.calls[0].url.endswith(quote("https://site.test/", safe=""))
    assert ShortenerOutcome.objects.get().stage == "health_check"
    assert ThirdPartyShortLink.objects.count() == 0


@pytest.mark.usefixtures("site_origin")
def test_health_check_reports_failure_without_secrets():
    set_mode(
        ShortenerMode.THIRD_PARTY,
        provider_kind=ProviderKind.QUERY_API,
        provider_endpoint="https://ouo.test/api?api={key}&url={url}",
    )
    row = ShortenerSettings.get_solo()
    row.set_api_key(SECRET)
    row.save()
    leaky = FetchTimeout(f"Timed out fetching https://ouo.test/api?api={SECRET}")

    outcome = shortener.check_provider(fetcher=FakeFetcher(leaky))

    assert not outcome.ok and outcome.code == "timeout"
    row.refresh_from_db()
    assert row.last_check_ok is False
    dump = " ".join(
        [outcome.message, row.last_check_message]
        + [
            f"{o.code} {o.detail} {o.target_host}"
            for o in ShortenerOutcome.objects.all()
        ]
    )
    assert SECRET not in dump


@pytest.mark.usefixtures("site_origin")
def test_health_check_on_an_unconfigured_provider_does_not_call_out():
    set_mode(ShortenerMode.THIRD_PARTY, provider_kind=ProviderKind.JSON_API)
    fake = FakeFetcher()

    outcome = shortener.check_provider(fetcher=fake)

    assert (outcome.ok, outcome.code) == (False, "misconfigured")
    assert fake.calls == []


# ----------------------------------------------------------------- secrets ----


def test_the_api_key_is_stored_encrypted_and_never_logged(firmware, caplog):
    row = set_mode(
        ShortenerMode.THIRD_PARTY,
        provider_kind=ProviderKind.QUERY_API,
        provider_endpoint="https://ouo.test/api?api={key}&url={url}",
    )
    row.set_api_key(SECRET)
    row.save()
    row.refresh_from_db()
    assert SECRET not in row.provider_api_key_encrypted
    assert row.get_api_key() == SECRET
    fake = FakeFetcher(
        FetchTimeout(f"Timed out fetching https://ouo.test/api?api={SECRET}")
    )

    with caplog.at_level(logging.DEBUG):
        result = shortener.shorten_download(firmware, fetcher=fake)

    assert SECRET in fake.calls[0].url  # the provider does need the key on the wire
    assert result.fallback == "timeout"
    assert SECRET not in caplog.text
    assert not ShortenerOutcome.objects.filter(detail__contains=SECRET).exists()
    assert SECRET not in repr(shortener.provider_config(row))


def test_an_undecryptable_key_is_treated_as_unconfigured(firmware):
    row = set_mode(
        ShortenerMode.THIRD_PARTY,
        provider_kind=ProviderKind.QUERY_API,
        provider_endpoint="https://ouo.test/api?api={key}&url={url}",
        provider_api_key_encrypted="not-a-fernet-token",
    )
    assert shortener.provider_config(row) is None

    result = shortener.shorten_download(firmware, fetcher=FakeFetcher())

    assert result.fallback == "misconfigured"
