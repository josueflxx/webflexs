from decimal import Decimal

from django.template import Context, Template
from django.test import TestCase


class GlobalNumberFormatTests(TestCase):
    def test_floatformat_adds_thousands_separator(self):
        rendered = Template("{{ value|floatformat:2 }}").render(
            Context({"value": Decimal("1234567.89")})
        ).strip()

        self.assertEqual(rendered, "1.234.567,89")

    def test_floatformat_without_decimals_keeps_grouping(self):
        rendered = Template("{{ value|floatformat:0 }}").render(
            Context({"value": Decimal("1234567")})
        ).strip()

        self.assertEqual(rendered, "1.234.567")
