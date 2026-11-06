from __future__ import annotations

from django.test import RequestFactory, SimpleTestCase, override_settings

from apps.core.utils.ip import get_client_ip


class ClientIpResolutionTests(SimpleTestCase):
    def request(self, **meta):
        return RequestFactory().get("/", **meta)

    def test_spoofed_real_ip_and_forwarded_for_are_ignored_by_default(self):
        request = self.request(
            REMOTE_ADDR="10.0.0.1",
            HTTP_X_REAL_IP="6.6.6.6",
            HTTP_X_FORWARDED_FOR="7.7.7.7, 8.8.8.8",
        )

        self.assertEqual(get_client_ip(request), "10.0.0.1")

    @override_settings(TRUSTED_PROXY_COUNT=1)
    def test_trusted_proxy_count_uses_the_address_its_proxy_appended(self):
        request = self.request(
            REMOTE_ADDR="10.0.0.2",
            HTTP_X_FORWARDED_FOR="6.6.6.6, 203.0.113.9",
            HTTP_X_REAL_IP="1.1.1.1",
        )

        self.assertEqual(get_client_ip(request), "203.0.113.9")

    @override_settings(TRUSTED_PROXY_COUNT=2)
    def test_two_trusted_proxies_peel_from_the_right(self):
        request = self.request(
            REMOTE_ADDR="10.0.0.2",
            HTTP_X_FORWARDED_FOR="6.6.6.6, 203.0.113.9, 10.0.0.2",
        )

        self.assertEqual(get_client_ip(request), "203.0.113.9")

    @override_settings(TRUSTED_PROXY_COUNT=1, TRUSTED_PROXIES=frozenset({"10.0.0.2"}))
    def test_forwarded_header_is_ignored_from_an_untrusted_peer(self):
        trusted = self.request(
            REMOTE_ADDR="10.0.0.2", HTTP_X_FORWARDED_FOR="203.0.113.9"
        )
        untrusted = self.request(
            REMOTE_ADDR="198.51.100.4", HTTP_X_FORWARDED_FOR="203.0.113.9"
        )

        self.assertEqual(get_client_ip(trusted), "203.0.113.9")
        self.assertEqual(get_client_ip(untrusted), "198.51.100.4")

    @override_settings(TRUSTED_PROXY_COUNT=1)
    def test_malformed_or_missing_forwarded_values_fall_back_to_the_peer(self):
        malformed = self.request(
            REMOTE_ADDR="10.0.0.3", HTTP_X_FORWARDED_FOR="not-an-ip"
        )
        missing = self.request(REMOTE_ADDR="10.0.0.3")

        self.assertEqual(get_client_ip(malformed), "10.0.0.3")
        self.assertEqual(get_client_ip(missing), "10.0.0.3")

    def test_unknown_peer_returns_an_empty_string(self):
        request = self.request()
        request.META.pop("REMOTE_ADDR", None)

        self.assertEqual(get_client_ip(request), "")
