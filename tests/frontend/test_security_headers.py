import base64
import hashlib
import re
from pathlib import Path

from django.test import TestCase

from app.settings.security import PRODUCTION_CSP

PROJECT_ROOT = Path(__file__).resolve().parents[2]
HTTP_ASSET = re.compile(r"""(?:src|href)\s*=\s*["']http://""", re.IGNORECASE)
INLINE_STYLE = re.compile(r"""\sstyle\s*=""", re.IGNORECASE)


class SecurityHeaderTests(TestCase):
    def test_pages_send_a_restrictive_content_security_policy(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        policy = response.headers["Content-Security-Policy"]
        self.assertIn("default-src 'self'", policy)
        self.assertIn("frame-ancestors 'none'", policy)
        self.assertIn("object-src 'none'", policy)
        self.assertNotIn("unsafe-inline", policy)
        self.assertNotIn("unsafe-eval", policy)
        self.assertNotIn("http:", policy)
        self.assertNotIn("*", policy)

    def test_untrusted_origins_are_not_granted_cross_origin_access(self):
        response = self.client.get("/", HTTP_ORIGIN="https://attacker.example")

        self.assertNotIn("Access-Control-Allow-Origin", response.headers)
        self.assertNotIn("Access-Control-Allow-Credentials", response.headers)

    def test_production_csp_upgrades_insecure_resource_requests(self):
        self.assertIn("upgrade-insecure-requests", PRODUCTION_CSP)

    def test_templates_do_not_load_http_assets(self):
        templates = PROJECT_ROOT / "templates"
        insecure_assets = [
            str(path.relative_to(PROJECT_ROOT))
            for path in templates.rglob("*.html")
            if HTTP_ASSET.search(path.read_text(encoding="utf-8"))
        ]

        self.assertEqual(insecure_assets, [])

    def test_templates_do_not_emit_inline_styles(self):
        templates = PROJECT_ROOT / "templates"
        inline_styles = [
            str(path.relative_to(PROJECT_ROOT))
            for path in templates.rglob("*.html")
            if INLINE_STYLE.search(path.read_text(encoding="utf-8"))
        ]

        self.assertEqual(inline_styles, [])

    def test_htmx_indicator_styles_are_csp_compatible(self):
        frontend = PROJECT_ROOT / "frontend"
        site_script = (frontend / "src" / "site.js").read_text(encoding="utf-8")
        site_styles = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((frontend / "src" / "styles").rglob("*.scss"))
        )

        self.assertIn("htmx.config.includeIndicatorStyles = false;", site_script)
        self.assertIn(".htmx-indicator", site_styles)
        self.assertIn(".htmx-request", site_styles)

    def test_browsable_api_highlighting_style_uses_its_exact_csp_hash(self):
        response = self.client.get(
            "/users/api/profiles/",
            HTTP_ACCEPT="text/html",
        )

        self.assertIn(response.status_code, {401, 403})
        self.assertTrue(response.headers.get("WWW-Authenticate") in {None, "Token"})
        style_blocks = re.findall(
            r"<style(?:\s[^>]*)?>(.*?)</style>",
            response.content.decode(),
            flags=re.IGNORECASE | re.DOTALL,
        )
        self.assertTrue(style_blocks)
        policy = response.headers["Content-Security-Policy"]

        for style in style_blocks:
            digest = base64.b64encode(hashlib.sha256(style.encode()).digest()).decode()
            self.assertIn(f"'sha256-{digest}'", policy)
