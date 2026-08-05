from importlib import import_module
from types import SimpleNamespace

from django.test import SimpleTestCase


def select_keyword(product):
    module = import_module("analytics.services.trend_keywords")
    return module.select_trend_keyword(product)


class TrendKeywordSelectionTests(SimpleTestCase):
    def test_existing_search_keyword_is_trimmed_and_not_regenerated(self):
        product = SimpleNamespace(
            search_keyword="  wireless headphones  ",
            normalized_title="a completely different product title",
        )

        self.assertEqual(select_keyword(product), "wireless headphones")

    def test_empty_search_keyword_uses_six_meaningful_non_numeric_words(self):
        product = SimpleNamespace(
            search_keyword="",
            normalized_title=(
                "wireless bluetooth 2025 headphones over ear noise "
                "cancelling black"
            ),
        )

        keyword = select_keyword(product)

        self.assertEqual(keyword, "wireless bluetooth headphones over ear noise")
        self.assertTrue(keyword)
        self.assertEqual(len(keyword.split()), 6)

    def test_generated_keyword_is_limited_to_one_hundred_characters(self):
        product = SimpleNamespace(
            search_keyword="",
            normalized_title=" ".join(["extraordinaryproductword"] * 8),
        )

        keyword = select_keyword(product)

        self.assertTrue(keyword)
        self.assertLessEqual(len(keyword), 100)

    def test_empty_or_numeric_only_title_returns_empty_string(self):
        for title in ("", "   ", "2024 100 42"):
            with self.subTest(title=title):
                product = SimpleNamespace(
                    search_keyword="",
                    normalized_title=title,
                )
                self.assertEqual(select_keyword(product), "")
