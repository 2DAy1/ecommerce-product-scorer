from decimal import Decimal

from django.test import SimpleTestCase

from catalog.services.normalization import (
    clean_product_url,
    extract_asin,
    normalize_title,
    parse_price,
    parse_rating,
    parse_reviews_count,
)


class NormalizationTests(SimpleTestCase):
    def test_extract_asin_uses_attribute_or_supported_url(self):
        self.assertEqual(extract_asin("b012345678"), "B012345678")
        self.assertEqual(
            extract_asin(url="https://www.amazon.com/example/dp/B0ABCDEFGH?tag=x"),
            "B0ABCDEFGH",
        )
        self.assertIsNone(extract_asin("invalid", "/unrelated/path"))

    def test_parse_optional_numeric_values(self):
        self.assertEqual(parse_price("$1,299.99"), Decimal("1299.99"))
        self.assertIsNone(parse_price("Currently unavailable"))
        self.assertEqual(parse_rating("4.7 out of 5 stars"), Decimal("4.7"))
        self.assertIsNone(parse_rating("6 out of 5"))
        self.assertEqual(parse_reviews_count("1,234 ratings"), 1234)
        self.assertEqual(parse_reviews_count("2.5K"), 2500)
        self.assertEqual(parse_reviews_count(None), 0)

    def test_normalize_title_is_stable(self):
        self.assertEqual(
            normalize_title("  Café_Mug — 12 oz! "),
            "café mug 12 oz",
        )

    def test_clean_product_url_removes_tracking(self):
        self.assertEqual(
            clean_product_url(
                "/Example/dp/B012345678/ref=zg_bs?tag=tracker",
                "B012345678",
            ),
            "https://www.amazon.com/dp/B012345678",
        )
