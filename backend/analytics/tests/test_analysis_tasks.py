from decimal import Decimal
from unittest.mock import patch

from billiard.exceptions import SoftTimeLimitExceeded
from django.test import TestCase

from analytics.models import JobRun, ProductAnalysis, TrendSnapshot
from analytics.tasks import analyze_products
from catalog.models import Product


def create_product(asin):
    return Product.objects.create(
        asin=asin,
        title=f"Product {asin}",
        normalized_title=f"product {asin.lower()}",
        category="Electronics",
        rating=Decimal("4.00"),
        reviews_count=100,
        product_url=f"https://www.amazon.com/dp/{asin}",
        image_url=f"https://images.example.com/{asin}.jpg",
    )


class FailingClient:
    provider = "anthropic"
    model = "test-model"

    def generate_explanation(self, **kwargs):
        raise TimeoutError("provider timeout")


class ProductAnalysisTaskTests(TestCase):
    def test_successful_batch_uses_latest_snapshot_and_updates_counters(self):
        product = create_product("B000000001")
        TrendSnapshot.objects.create(
            product=product,
            keyword="old",
            geo="US",
            period="today 3-m",
            current_interest=10,
            average_interest=10,
            growth_percent=Decimal("0"),
            series=[10],
        )
        latest = TrendSnapshot.objects.create(
            product=product,
            keyword="latest",
            geo="US",
            period="today 3-m",
            current_interest=90,
            average_interest=80,
            growth_percent=Decimal("20"),
            series=[50, 90],
        )
        create_product("B000000002")
        job = JobRun.objects.create(job_type=JobRun.JobType.PRODUCT_ANALYSIS)

        result = analyze_products.apply(
            args=[str(job.id)], task_id="analysis-task", throw=True
        ).get()

        job.refresh_from_db()
        self.assertEqual(job.status, JobRun.Status.SUCCEEDED)
        self.assertEqual((job.total_items, job.processed_items, job.failed_items), (2, 2, 0))
        self.assertIsNotNone(job.started_at)
        self.assertIsNotNone(job.finished_at)
        self.assertEqual(result["processed_items"], 2)
        self.assertEqual(ProductAnalysis.objects.count(), 2)
        self.assertEqual(
            product.analyses.get().input_snapshot["trends"]["snapshot_id"],
            latest.pk,
        )

    @patch("analytics.tasks.create_product_analysis")
    def test_partial_product_failure_preserves_successes_and_counters(self, create):
        products = [create_product("B000000001"), create_product("B000000002")]
        create.side_effect = [
            ProductAnalysis.objects.create(product=products[0], final_score=50),
            RuntimeError("bad product"),
        ]
        job = JobRun.objects.create(job_type=JobRun.JobType.PRODUCT_ANALYSIS)

        analyze_products.apply(args=[str(job.id)], throw=True).get()

        job.refresh_from_db()
        self.assertEqual(job.status, JobRun.Status.SUCCEEDED)
        self.assertEqual((job.processed_items, job.failed_items), (1, 1))
        self.assertEqual(ProductAnalysis.objects.count(), 1)

    @patch("analytics.tasks.build_llm_client")
    def test_provider_wide_failure_uses_fallback_for_every_product(self, build_client):
        build_client.return_value = FailingClient()
        create_product("B000000001")
        create_product("B000000002")
        job = JobRun.objects.create(job_type=JobRun.JobType.PRODUCT_ANALYSIS)

        analyze_products.apply(args=[str(job.id)], throw=True).get()

        job.refresh_from_db()
        self.assertEqual((job.processed_items, job.failed_items), (2, 0))
        self.assertEqual(
            set(ProductAnalysis.objects.values_list("provider", flat=True)),
            {"deterministic"},
        )

    @patch("analytics.tasks.build_llm_client")
    def test_zero_products_fails_without_building_llm_client(self, build_client):
        job = JobRun.objects.create(job_type=JobRun.JobType.PRODUCT_ANALYSIS)

        result = analyze_products.apply(args=[str(job.id)], throw=True).get()

        job.refresh_from_db()
        self.assertEqual(job.status, JobRun.Status.FAILED)
        self.assertEqual((job.total_items, job.processed_items, job.failed_items), (0, 0, 0))
        self.assertIsNotNone(job.started_at)
        self.assertIsNotNone(job.finished_at)
        self.assertIn("No products", job.error_message)
        self.assertEqual(result, {"total_items": 0, "processed_items": 0, "failed_items": 0})
        build_client.assert_not_called()

    @patch("analytics.tasks.build_llm_client")
    def test_non_pending_job_is_rejected_without_processing(self, build_client):
        create_product("B000000001")
        job = JobRun.objects.create(
            job_type=JobRun.JobType.PRODUCT_ANALYSIS,
            status=JobRun.Status.SUCCEEDED,
            processed_items=4,
        )

        with self.assertRaisesRegex(ValueError, "must be pending"):
            analyze_products.apply(args=[str(job.id)], throw=True).get()

        job.refresh_from_db()
        self.assertEqual(job.status, JobRun.Status.SUCCEEDED)
        self.assertEqual(job.processed_items, 4)
        self.assertFalse(ProductAnalysis.objects.exists())
        build_client.assert_not_called()

    @patch(
        "analytics.tasks.build_llm_client",
        side_effect=RuntimeError("configuration failed"),
    )
    def test_task_level_exception_finalizes_job(self, build_client):
        create_product("B000000001")
        create_product("B000000002")
        job = JobRun.objects.create(job_type=JobRun.JobType.PRODUCT_ANALYSIS)

        with self.assertRaisesRegex(RuntimeError, "configuration failed"):
            analyze_products.apply(args=[str(job.id)], throw=True)

        job.refresh_from_db()
        self.assertEqual(job.status, JobRun.Status.FAILED)
        self.assertEqual((job.processed_items, job.failed_items), (0, 2))
        self.assertIsNotNone(job.started_at)
        self.assertIsNotNone(job.finished_at)
        self.assertIn("configuration failed", str(job.details))

    @patch(
        "analytics.tasks.create_product_analysis",
        side_effect=SoftTimeLimitExceeded("soft timeout"),
    )
    def test_soft_timeout_finalizes_job_and_is_reraised(self, create):
        create_product("B000000001")
        create_product("B000000002")
        job = JobRun.objects.create(job_type=JobRun.JobType.PRODUCT_ANALYSIS)

        with self.assertRaises(SoftTimeLimitExceeded):
            analyze_products.apply(args=[str(job.id)], throw=True)

        job.refresh_from_db()
        self.assertEqual(job.status, JobRun.Status.FAILED)
        self.assertEqual((job.processed_items, job.failed_items), (0, 2))
        self.assertIsNotNone(job.started_at)
        self.assertIsNotNone(job.finished_at)
        self.assertIn("soft timeout", job.error_message)
        create.assert_called_once()
