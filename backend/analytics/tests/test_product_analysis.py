from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from billiard.exceptions import SoftTimeLimitExceeded
from django.test import TestCase

from analytics.models import ProductAnalysis, TrendSnapshot
from analytics.services.llm_analysis import (
    LLMAnalysisError,
    validate_llm_explanation,
)
from analytics.services.product_analysis import create_product_analysis
from catalog.models import Product, SuccessfulProduct


def create_product(asin="B000000001"):
    return Product.objects.create(
        asin=asin,
        title="Wireless Headphones",
        normalized_title="wireless headphones",
        category="Electronics",
        rating=Decimal("4.50"),
        reviews_count=500,
        product_url=f"https://www.amazon.com/dp/{asin}",
        image_url=f"https://images.example.com/{asin}.jpg",
    )


class FakeClient:
    provider = "anthropic"
    model = "test-model"

    def __init__(self, result="LLM strengths, risks, and recommendation."):
        self.result = result

    def generate_explanation(self, **kwargs):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class ProductAnalysisPersistenceTests(TestCase):
    def setUp(self):
        self.product = create_product()
        self.successful = SuccessfulProduct.objects.create(
            title="Wireless Headphones",
            normalized_title="wireless headphones",
            category="Electronics",
            keywords=["wireless", "audio"],
        )

    def test_no_llm_client_creates_deterministic_analysis(self):
        analysis = create_product_analysis(
            self.product,
            successful_products=[self.successful],
        )

        self.assertEqual(ProductAnalysis.objects.count(), 1)
        self.assertEqual(analysis.provider, "deterministic")
        self.assertEqual(analysis.model_name, "fallback-v1")
        self.assertTrue(analysis.reasoning)
        self.assertEqual(
            analysis.input_snapshot["explanation"],
            {
                "source": "deterministic",
                "model": "fallback-v1",
                "llm_status": "not_configured",
            },
        )
        self.assertGreaterEqual(analysis.final_score, Decimal("0"))
        self.assertLessEqual(analysis.final_score, Decimal("100"))

    @patch(
        "analytics.services.product_analysis.build_fallback_explanation",
        return_value="Deterministic explanation.",
    )
    @patch("analytics.services.product_analysis.calculate_product_score")
    def test_score_fields_and_input_snapshot_are_persisted_exactly(
        self,
        calculate_score,
        build_fallback,
    ):
        score = SimpleNamespace(
            trend_score=Decimal("11.25"),
            boost_score=Decimal("7.50"),
            baseline_score=Decimal("63.75"),
            final_score=Decimal("82.50"),
            input_snapshot={
                "product": {"asin": self.product.asin},
                "trends": {"snapshot_id": None},
                "historical_match": {"reason": "fixture"},
            },
        )
        calculate_score.return_value = score

        analysis = create_product_analysis(
            self.product,
            successful_products=[self.successful],
        )

        calculate_score.assert_called_once_with(
            self.product,
            trend_snapshot=None,
            successful_products=[self.successful],
        )
        build_fallback.assert_called_once_with(self.product, score)
        self.assertEqual(ProductAnalysis.objects.count(), 1)
        self.assertEqual(analysis.product, self.product)
        self.assertEqual(analysis.trend_score, Decimal("11.25"))
        self.assertEqual(analysis.boost_score, Decimal("7.50"))
        self.assertEqual(analysis.baseline_score, Decimal("63.75"))
        self.assertEqual(analysis.final_score, Decimal("82.50"))
        self.assertEqual(analysis.provider, "deterministic")
        self.assertEqual(analysis.model_name, "fallback-v1")
        self.assertEqual(analysis.reasoning, "Deterministic explanation.")
        self.assertEqual(
            analysis.input_snapshot,
            {
                **score.input_snapshot,
                "explanation": {
                    "source": "deterministic",
                    "model": "fallback-v1",
                    "llm_status": "not_configured",
                },
            },
        )
        self.assertNotIn("explanation", score.input_snapshot)

    def test_repeated_analysis_preserves_history(self):
        first = create_product_analysis(self.product)
        second = create_product_analysis(self.product)

        self.assertEqual(self.product.analyses.count(), 2)
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(self.product.analyses.first(), second)

    def test_llm_error_falls_back_without_losing_deterministic_score(self):
        deterministic = create_product_analysis(self.product)
        fallback = create_product_analysis(
            self.product,
            llm_client=FakeClient(LLMAnalysisError("provider unavailable")),
        )

        self.assertEqual(fallback.final_score, deterministic.final_score)
        self.assertEqual(fallback.provider, "deterministic")
        self.assertEqual(fallback.model_name, "fallback-v1")
        self.assertEqual(fallback.reasoning, deterministic.reasoning)
        self.assertEqual(
            fallback.input_snapshot["explanation"],
            {
                "source": "deterministic",
                "model": "fallback-v1",
                "llm_status": "fallback_after_error",
            },
        )

    def test_empty_llm_response_uses_fallback(self):
        analysis = create_product_analysis(
            self.product,
            llm_client=FakeClient("   "),
        )

        self.assertEqual(analysis.provider, "deterministic")
        self.assertEqual(analysis.model_name, "fallback-v1")
        self.assertTrue(analysis.reasoning)
        self.assertEqual(
            analysis.input_snapshot["explanation"],
            {
                "source": "deterministic",
                "model": "fallback-v1",
                "llm_status": "fallback_after_error",
            },
        )

    def test_malformed_llm_response_uses_fallback(self):
        analysis = create_product_analysis(
            self.product,
            llm_client=FakeClient({"unexpected": "shape"}),
        )

        self.assertEqual(analysis.provider, "deterministic")
        self.assertEqual(analysis.model_name, "fallback-v1")
        self.assertEqual(
            analysis.input_snapshot["explanation"],
            {
                "source": "deterministic",
                "model": "fallback-v1",
                "llm_status": "fallback_after_error",
            },
        )

    def test_soft_timeout_propagates_without_persisting_analysis(self):
        timeout = SoftTimeLimitExceeded("analysis soft timeout")

        with self.assertRaises(SoftTimeLimitExceeded) as raised:
            create_product_analysis(
                self.product,
                llm_client=FakeClient(timeout),
            )

        self.assertIs(raised.exception, timeout)
        self.assertFalse(ProductAnalysis.objects.exists())

    @patch(
        "analytics.services.product_analysis.validate_llm_explanation",
        wraps=validate_llm_explanation,
    )
    def test_valid_llm_response_is_persisted_with_source(self, validate):
        analysis = create_product_analysis(
            self.product,
            llm_client=FakeClient("  Concise model explanation.  "),
        )

        validate.assert_called_once_with("  Concise model explanation.  ")
        self.assertEqual(analysis.provider, "anthropic")
        self.assertEqual(analysis.model_name, "test-model")
        self.assertEqual(analysis.reasoning, "Concise model explanation.")
        self.assertEqual(
            analysis.input_snapshot["explanation"],
            {
                "source": "anthropic",
                "model": "test-model",
                "llm_status": "succeeded",
            },
        )

    def test_score_uses_supplied_latest_trend_snapshot(self):
        snapshot = TrendSnapshot.objects.create(
            product=self.product,
            keyword="wireless headphones",
            geo="US",
            period="today 3-m",
            current_interest=80,
            average_interest=60,
            growth_percent=Decimal("50.00"),
            series=[40, 60, 80],
        )

        analysis = create_product_analysis(
            self.product,
            trend_snapshot=snapshot,
        )

        self.assertGreater(analysis.trend_score, Decimal("0"))
        self.assertEqual(
            analysis.input_snapshot["trends"]["snapshot_id"],
            snapshot.pk,
        )
