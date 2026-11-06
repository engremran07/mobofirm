"""Every literal template name referenced by application code must resolve."""

import re
from pathlib import Path

from django.template import TemplateDoesNotExist
from django.template.loader import get_template
from django.test import SimpleTestCase

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_REFERENCE = re.compile(r"""["']([A-Za-z0-9_./-]+\.html)["']""")


class ApplicationTemplateInventoryTests(SimpleTestCase):
    """Template references in ``apps/`` point at loadable templates."""

    def test_literal_application_template_references_resolve(self):
        """No quoted ``*.html`` name in application code is missing."""
        references = set()
        for source in (PROJECT_ROOT / "apps").rglob("*.py"):
            source_text = source.read_text(encoding="utf-8")
            references.update(TEMPLATE_REFERENCE.findall(source_text))

        missing = []
        for reference in sorted(references):
            try:
                get_template(reference)
            except TemplateDoesNotExist:
                missing.append(reference)

        self.assertEqual(missing, [])
