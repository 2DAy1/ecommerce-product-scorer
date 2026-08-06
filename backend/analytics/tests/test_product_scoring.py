from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from analytics.services.product_scoring import (
    build_fallback_explanation,
    calculate_product_score,
)
from analytics.services.sales_boost import calculate_sales_boost


def product(**overrides):
    values = {
        "title": "Wireless Headphones",
        "normalized_title": "wireless headphones",
        "category": "Electronics",
        "rating": Decimal("4.50"),
        "reviews_count": 500,
        "search_keyword": "wireless audio",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def trend(**overrides):
    values = {
        "pk": 7,
        "current_interest": 80,
        "average_interest": 60,
        "growth_percent": Decimal("50.00"),
        "series": [40, 60, 80],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def successful(pk=1, **overrides):
    values = {
        "pk": pk,
        "title": "Wireless Headphones",
        "normalized_title": "wireless headphones",
        "category": "Electronics",
        "keywords": ["wireless", "audio"],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class ProductScoringTests(SimpleTestCase):
    def test_score_is_bounded_between_zero_and_one_hundred(self):
        score = calculate_product_score(
            product(rating=Decimal("999"), reviews_count=10**20),
            trend_snapshot=trend(
                current_interest=999,
                average_interest=999,
                growth_percent=Decimal("99999"),
            ),
            successful_products=[successful()],
        )

        self.assertGreaterEqual(score.final_score, Decimal("0"))
        self.assertLessEqual(score.final_score, Decimal("100"))

    def test_identical_inputs_produce_identical_score_and_snapshot(self):
        first = calculate_product_score(
            product(), trend_snapshot=trend(), successful_products=[successful()]
        )
        second = calculate_product_score(
            product(), trend_snapshot=trend(), successful_products=[successful()]
        )

        self.assertEqual(first, second)

    def test_missing_trend_still_produces_valid_reduced_score(self):
        score = calculate_product_score(product(), trend_snapshot=None)

        self.assertEqual(score.trend_score, Decimal("0.00"))
        self.assertGreaterEqual(score.final_score, Decimal("0"))
        self.assertLessEqual(score.final_score, Decimal("100"))

    def test_missing_rating_uses_documented_neutral_reduced_signal(self):
        score = calculate_product_score(product(rating=None, reviews_count=0))

        self.assertEqual(score.baseline_score, Decimal("35.00"))

    def test_review_normalization_is_capped(self):
        capped = calculate_product_score(product(reviews_count=10_000))
        extreme = calculate_product_score(product(reviews_count=10**20))

        self.assertEqual(capped.baseline_score, extreme.baseline_score)
        self.assertEqual(capped.input_snapshot["amazon"]["review_score"], "30.00")

    def test_extreme_growth_is_capped(self):
        capped = calculate_product_score(product(), trend_snapshot=trend(growth_percent=100))
        extreme = calculate_product_score(
            product(), trend_snapshot=trend(growth_percent=100_000)
        )

        self.assertEqual(capped.trend_score, extreme.trend_score)

    def test_empty_timeline_does_not_trust_growth(self):
        empty = calculate_product_score(
            product(), trend_snapshot=trend(growth_percent=100, series=[])
        )
        populated = calculate_product_score(
            product(), trend_snapshot=trend(growth_percent=100, series=[20, 80])
        )

        self.assertLess(empty.trend_score, populated.trend_score)

    def test_sales_boost_increases_relevant_component_and_final_score(self):
        without_boost = calculate_product_score(product())
        with_boost = calculate_product_score(
            product(), successful_products=[successful()]
        )

        self.assertEqual(with_boost.boost_score, Decimal("10.00"))
        self.assertGreater(with_boost.final_score, without_boost.final_score)

    def test_unrelated_successful_product_does_not_boost(self):
        result = calculate_sales_boost(
            product(),
            [
                successful(
                    normalized_title="garden hose",
                    title="Garden Hose",
                    category="Garden",
                    keywords=["outdoor", "watering"],
                )
            ],
        )

        self.assertEqual(result.score, Decimal("0.00"))

    def test_exact_title_and_category_is_stronger_than_keyword_overlap(self):
        exact = calculate_sales_boost(product(), [successful()])
        keyword = calculate_sales_boost(
            product(),
            [
                successful(
                    normalized_title="audio accessory",
                    title="Audio Accessory",
                    keywords=["wireless"],
                )
            ],
        )

        self.assertGreater(exact.score, keyword.score)

    def test_exact_apple_earpods_title_and_category_keeps_maximum_boost(self):
        title = (
            "Apple EarPods Headphones with USB-C Plug, Wired Ear Buds with "
            "Built-in Remote to Control Music, Phone Calls, and Volume"
        )

        result = calculate_sales_boost(
            product(
                title=title,
                normalized_title=title,
                category="Amazon Best Sellers",
                search_keyword="",
            ),
            [
                successful(
                    title=title,
                    normalized_title=title,
                    category="Amazon Best Sellers",
                    keywords=["apple", "earpods", "headphones"],
                )
            ],
        )

        self.assertEqual(result.score, Decimal("10.00"))
        self.assertEqual(result.reason, "Exact normalized title and category match")

    def test_stop_word_with_does_not_boost_blink(self):
        result = calculate_sales_boost(
            product(
                title="blink plus plan with monthly auto-renewal",
                normalized_title="blink plus plan with monthly auto renewal",
                category="Amazon Best Sellers",
                search_keyword="",
            ),
            [
                successful(
                    title="Apple EarPods Headphones with USB-C Plug",
                    normalized_title="apple earpods headphones with usb c plug",
                    category="Amazon Best Sellers",
                    keywords=["apple", "earpods", "headphones"],
                )
            ],
        )

        self.assertEqual(result.score, Decimal("0.00"))
        self.assertEqual(result.matched_tokens, ())
        self.assertNotIn("with", result.reason)

    def test_generic_amazon_category_does_not_add_keyword_bonus(self):
        result = calculate_sales_boost(
            product(
                title="Reusable Collagen Mask",
                normalized_title="reusable collagen mask",
                category="Amazon Best Sellers",
                search_keyword="",
            ),
            [
                successful(
                    title="Collagen Serum",
                    normalized_title="collagen serum",
                    category="amazon best sellers",
                    keywords=["collagen"],
                )
            ],
        )

        self.assertEqual(result.score, Decimal("1.25"))
        self.assertEqual(result.reason, "Keyword-token match: collagen")

    def test_stop_words_are_excluded_from_tokens_and_reasoning(self):
        result = calculate_sales_boost(
            product(
                title="The mask with collagen and acne",
                normalized_title="the mask with collagen and acne",
                category="Beauty",
                search_keyword="",
            ),
            [
                successful(
                    title="A mask for collagen in skincare",
                    normalized_title="a mask for collagen in skincare",
                    category="Other",
                    keywords=[],
                )
            ],
        )

        self.assertEqual(result.matched_tokens, ("collagen", "mask"))
        for stop_word in ("a", "and", "for", "in", "the", "with"):
            self.assertNotIn(stop_word, result.matched_tokens)
        self.assertEqual(result.reason, "Keyword-token match: collagen, mask")

    def test_biodance_keeps_meaningful_keyword_boost(self):
        result = calculate_sales_boost(
            product(
                title=(
                    "BIODANCE Bio-Collagen Real Deep Mask, Hydrating Overnight "
                    "Hydrogel Face Mask"
                ),
                normalized_title=(
                    "biodance bio collagen real deep mask hydrating overnight "
                    "hydrogel face mask"
                ),
                category="Amazon Best Sellers",
                search_keyword="",
            ),
            [
                successful(
                    title="BIODANCE Bio-Collagen Real Deep Mask",
                    normalized_title="biodance bio collagen real deep mask",
                    category="Amazon Best Sellers",
                    keywords=["mask", "collagen", "skincare"],
                )
            ],
        )

        self.assertEqual(result.score, Decimal("4.00"))
        self.assertIn("biodance", result.matched_tokens)
        self.assertIn("collagen", result.matched_tokens)
        self.assertIn("mask", result.matched_tokens)

    def test_mighty_patch_keeps_meaningful_keyword_boost(self):
        result = calculate_sales_boost(
            product(
                title=(
                    "Mighty Patch Hero Cosmetics Original Nighttime Acne Pimple "
                    "Patches, 36 Ct | #1 Hydrocolloid Acne Patches"
                ),
                normalized_title=(
                    "mighty patch hero cosmetics original nighttime acne pimple "
                    "patches 36 ct 1 hydrocolloid acne patches"
                ),
                category="Amazon Best Sellers",
                search_keyword="",
            ),
            [
                successful(
                    title=(
                        "Mighty Patch Hero Cosmetics Original Nighttime Acne "
                        "Pimple Patches"
                    ),
                    normalized_title=(
                        "mighty patch hero cosmetics original nighttime acne "
                        "pimple patches"
                    ),
                    category="Amazon Best Sellers",
                    keywords=["acne", "patches", "skincare"],
                )
            ],
        )

        self.assertEqual(result.score, Decimal("4.00"))
        self.assertIn("acne", result.matched_tokens)
        self.assertIn("patch", result.matched_tokens)
        self.assertIn("patches", result.matched_tokens)

    def test_meaningful_category_match_keeps_existing_bonus(self):
        result = calculate_sales_boost(
            product(
                title="Wireless Charger Stand",
                normalized_title="wireless charger stand",
                category="Electronics",
                search_keyword="",
            ),
            [
                successful(
                    title="Wireless Adapter",
                    normalized_title="wireless adapter",
                    category="electronics",
                    keywords=["wireless"],
                )
            ],
        )

        self.assertEqual(result.score, Decimal("3.25"))
        self.assertEqual(result.reason, "Category and keyword-token match: wireless")

    def test_empty_and_one_character_keywords_do_not_match(self):
        result = calculate_sales_boost(
            product(title="A", normalized_title="a", search_keyword=""),
            [
                successful(
                    normalized_title="different",
                    title="Different",
                    category="Other",
                    keywords=["", "a"],
                )
            ],
        )

        self.assertEqual(result.score, Decimal("0.00"))

    def test_fallback_explanation_is_stable_non_empty_and_mentions_missing_trends(self):
        score = calculate_product_score(product(), trend_snapshot=None)

        first = build_fallback_explanation(product(), score)
        second = build_fallback_explanation(product(), score)

        self.assertEqual(first, second)
        self.assertTrue(first)
        self.assertIn("no Google Trends snapshot", first)
        self.assertIn("Final score", first)
