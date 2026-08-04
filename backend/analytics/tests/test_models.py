import uuid
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase

from analytics.models import JobRun, ProductAnalysis, TrendSnapshot
from catalog.models import Product


def create_product(asin: str = "B000000001") -> Product:
    return Product.objects.create(
        asin=asin,
        title="Test Product",
        normalized_title="test product",
        category="Home",
        product_url=f"https://www.amazon.com/dp/{asin}",
        image_url=f"https://images.example.com/{asin}.jpg",
    )


def trend_data(product: Product, **overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "product": product,
        "keyword": "test product",
        "geo": "US",
        "period": "today 3-m",
        "current_interest": 75,
        "average_interest": 60,
        "growth_percent": Decimal("25.00"),
    }
    data.update(overrides)
    return data


class TrendSnapshotModelTests(TestCase):
    def setUp(self) -> None:
        self.product = create_product()

    def test_series_default_and_product_relationship(self) -> None:
        snapshot = TrendSnapshot.objects.create(**trend_data(self.product))

        self.assertEqual(snapshot.series, [])
        self.assertEqual(self.product.trend_snapshots.get(), snapshot)

    def test_interest_values_must_be_between_zero_and_one_hundred(self) -> None:
        invalid_values = (
            {"current_interest": -1},
            {"current_interest": 101},
            {"average_interest": -1},
            {"average_interest": 101},
        )

        for values in invalid_values:
            with self.subTest(values=values):
                with self.assertRaises(IntegrityError):
                    with transaction.atomic():
                        TrendSnapshot.objects.create(
                            **trend_data(self.product, **values)
                        )

    def test_snapshots_are_deleted_with_product(self) -> None:
        TrendSnapshot.objects.create(**trend_data(self.product))

        self.product.delete()

        self.assertFalse(TrendSnapshot.objects.exists())


class ProductAnalysisModelTests(TestCase):
    def setUp(self) -> None:
        self.product = create_product()

    def test_defaults_and_one_to_one_relationship(self) -> None:
        analysis = ProductAnalysis.objects.create(product=self.product)
        analysis.refresh_from_db()

        self.assertEqual(analysis.trend_score, Decimal("0.00"))
        self.assertEqual(analysis.boost_score, Decimal("0.00"))
        self.assertEqual(analysis.baseline_score, Decimal("0.00"))
        self.assertEqual(analysis.final_score, Decimal("0.00"))
        self.assertEqual(analysis.provider, "")
        self.assertEqual(analysis.model_name, "")
        self.assertEqual(analysis.reasoning, "")
        self.assertEqual(analysis.input_snapshot, {})
        self.assertEqual(self.product.analysis, analysis)

    def test_only_one_analysis_is_allowed_per_product(self) -> None:
        ProductAnalysis.objects.create(product=self.product)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProductAnalysis.objects.create(product=self.product)

    def test_final_score_must_be_between_zero_and_one_hundred(self) -> None:
        for score in (Decimal("-0.01"), Decimal("100.01")):
            with self.subTest(score=score):
                with self.assertRaises(IntegrityError):
                    with transaction.atomic():
                        ProductAnalysis.objects.create(
                            product=self.product,
                            final_score=score,
                        )

    def test_analysis_is_deleted_with_product(self) -> None:
        ProductAnalysis.objects.create(product=self.product)

        self.product.delete()

        self.assertFalse(ProductAnalysis.objects.exists())


class JobRunModelTests(TestCase):
    def test_defaults_and_choices(self) -> None:
        job = JobRun.objects.create(job_type=JobRun.JobType.PRODUCT_COLLECTION)

        self.assertIsInstance(job.id, uuid.UUID)
        self.assertEqual(job.status, JobRun.Status.PENDING)
        self.assertEqual(job.celery_task_id, "")
        self.assertEqual(job.total_items, 0)
        self.assertEqual(job.processed_items, 0)
        self.assertEqual(job.failed_items, 0)
        self.assertEqual(job.error_message, "")
        self.assertEqual(job.details, {})
        self.assertIsNone(job.started_at)
        self.assertIsNone(job.finished_at)
        self.assertEqual(
            {choice.value for choice in JobRun.JobType},
            {"product_collection", "trend_collection", "product_analysis"},
        )
        self.assertEqual(
            {choice.value for choice in JobRun.Status},
            {"pending", "running", "succeeded", "failed"},
        )

    def test_item_counters_must_not_be_negative(self) -> None:
        for field_name in ("total_items", "processed_items", "failed_items"):
            with self.subTest(field_name=field_name):
                with self.assertRaises(IntegrityError):
                    with transaction.atomic():
                        JobRun.objects.create(
                            job_type=JobRun.JobType.PRODUCT_COLLECTION,
                            **{field_name: -1},
                        )
