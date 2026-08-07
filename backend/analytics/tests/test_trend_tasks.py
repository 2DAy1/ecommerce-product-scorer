from datetime import timedelta
from decimal import Decimal
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import call, patch

from billiard.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.test import TestCase, tag
from django.utils import timezone

from analytics.models import JobRun, TrendSnapshot
from catalog.models import Product


def create_product(asin: str) -> Product:
    return Product.objects.create(
        asin=asin,
        title=f"Product {asin}",
        normalized_title=f"product {asin.lower()}",
        category="Electronics",
        price=Decimal("10.00"),
        rating=Decimal("4.00"),
        reviews_count=10,
        product_url=f"https://www.amazon.com/dp/{asin}",
        image_url="https://example.com/product.jpg",
    )


def trend_task():
    module = import_module("analytics.tasks")
    return module, module.collect_google_trends


class _MissingGoogleTrendsRateLimitError(RuntimeError):
    pass


def rate_limit_error_type():
    module = import_module("analytics.services.google_trends")
    return getattr(
        module,
        "GoogleTrendsRateLimitError",
        _MissingGoogleTrendsRateLimitError,
    )


class CollectGoogleTrendsTaskTests(TestCase):
    def setUp(self):
        create_product("B012345678")
        create_product("B087654321")
        self.job = JobRun.objects.create(job_type=JobRun.JobType.TREND_COLLECTION)

    def test_successful_task_updates_job_lifecycle_and_counters(self):
        tasks, task = trend_task()
        with (
            patch.object(tasks, "GoogleTrendsCollector") as collector_class,
            patch.object(tasks, "collect_product_trend") as collect_product,
        ):
            collect_product.return_value = SimpleNamespace(pk=1)

            result = task.apply(
                args=[str(self.job.id)],
                task_id="trends-success",
                throw=True,
            ).get()

        self.job.refresh_from_db()
        self.assertEqual(self.job.status, JobRun.Status.SUCCEEDED)
        self.assertEqual(self.job.celery_task_id, "trends-success")
        self.assertEqual(self.job.total_items, 2)
        self.assertEqual(self.job.processed_items, 2)
        self.assertEqual(self.job.failed_items, 0)
        self.assertIsNotNone(self.job.started_at)
        self.assertIsNotNone(self.job.finished_at)
        self.assertEqual(result["processed_items"], 2)
        self.assertEqual(collect_product.call_count, 2)
        collector_class.assert_called_once()

    def test_products_use_pk_order_and_share_configured_collector(self):
        tasks, task = trend_task()
        products = list(Product.objects.order_by("pk"))
        with (
            patch.object(tasks, "GoogleTrendsCollector") as collector_class,
            patch.object(
                tasks,
                "select_trend_keyword",
                side_effect=["first keyword", "second keyword"],
            ) as select_keyword,
            patch.object(tasks, "collect_product_trend") as collect_product,
        ):
            collector = collector_class.return_value.__enter__.return_value

            task.apply(
                args=[str(self.job.id)],
                task_id="trends-context",
                throw=True,
            ).get()

        collector_class.assert_called_once_with(
            headless=settings.TRENDS_HEADLESS,
            request_timeout_seconds=settings.TRENDS_REQUEST_TIMEOUT_SECONDS,
        )
        self.assertEqual(
            select_keyword.call_args_list,
            [call(products[0]), call(products[1])],
        )
        self.assertEqual(
            collect_product.call_args_list,
            [
                call(
                    products[0],
                    collector=collector,
                    geo=settings.TRENDS_GEO,
                    period=settings.TRENDS_PERIOD,
                ),
                call(
                    products[1],
                    collector=collector,
                    geo=settings.TRENDS_GEO,
                    period=settings.TRENDS_PERIOD,
                ),
            ],
        )

    def test_collector_startup_failure_preserves_current_batch_result(self):
        tasks, task = trend_task()
        with (
            patch.object(tasks, "GoogleTrendsCollector") as collector_class,
            patch.object(tasks, "collect_product_trend") as collect_product,
        ):
            collector_class.return_value.__enter__.side_effect = RuntimeError(
                "collector startup failed"
            )

            result = task.apply(
                args=[str(self.job.id)],
                task_id="trends-startup-failed",
                throw=True,
            ).get()

        self.job.refresh_from_db()
        self.assertEqual(self.job.status, JobRun.Status.FAILED)
        self.assertEqual(self.job.processed_items, 0)
        self.assertEqual(self.job.failed_items, 2)
        self.assertEqual(
            self.job.error_message,
            "All 2 products failed trend collection",
        )
        self.assertEqual(
            self.job.details,
            {
                "errors_count": 2,
                "errors": [
                    {
                        "product_id": 0,
                        "asin": "",
                        "error": "collector startup failed",
                    }
                ],
            },
        )
        self.assertEqual(
            result,
            {"total_items": 2, "processed_items": 0, "failed_items": 2},
        )
        collect_product.assert_not_called()

    def test_collector_cleanup_failure_preserves_current_success_result(self):
        tasks, task = trend_task()
        with (
            patch.object(tasks, "GoogleTrendsCollector") as collector_class,
            patch.object(tasks, "collect_product_trend") as collect_product,
        ):
            collector_class.return_value.__exit__.side_effect = RuntimeError(
                "collector cleanup failed"
            )

            result = task.apply(
                args=[str(self.job.id)],
                task_id="trends-cleanup-failed",
                throw=True,
            ).get()

        self.job.refresh_from_db()
        self.assertEqual(self.job.status, JobRun.Status.SUCCEEDED)
        self.assertEqual(self.job.processed_items, 2)
        self.assertEqual(self.job.failed_items, 0)
        self.assertEqual(self.job.error_message, "")
        self.assertEqual(
            self.job.details,
            {
                "errors_count": 0,
                "errors": [
                    {
                        "product_id": 0,
                        "asin": "",
                        "error": "collector cleanup failed",
                    }
                ],
            },
        )
        self.assertEqual(
            result,
            {"total_items": 2, "processed_items": 2, "failed_items": 0},
        )
        self.assertEqual(collect_product.call_count, 2)

    def test_one_product_failure_does_not_stop_remaining_products(self):
        tasks, task = trend_task()
        with (
            patch.object(tasks, "GoogleTrendsCollector"),
            patch.object(
                tasks,
                "collect_product_trend",
                side_effect=[RuntimeError("one product failed"), SimpleNamespace(pk=2)],
            ) as collect_product,
        ):
            task.apply(
                args=[str(self.job.id)],
                task_id="trends-partial",
                throw=True,
            ).get()

        self.job.refresh_from_db()
        self.assertEqual(self.job.status, JobRun.Status.SUCCEEDED)
        self.assertEqual(self.job.total_items, 2)
        self.assertEqual(self.job.processed_items, 1)
        self.assertEqual(self.job.failed_items, 1)
        self.assertEqual(collect_product.call_count, 2)

    def test_job_fails_when_all_products_fail(self):
        tasks, task = trend_task()
        with (
            patch.object(tasks, "GoogleTrendsCollector"),
            patch.object(
                tasks,
                "collect_product_trend",
                side_effect=RuntimeError("collection failed"),
            ),
        ):
            task.apply(
                args=[str(self.job.id)],
                task_id="trends-failed",
                throw=False,
            )

        self.job.refresh_from_db()
        self.assertEqual(self.job.status, JobRun.Status.FAILED)
        self.assertEqual(self.job.total_items, 2)
        self.assertEqual(self.job.processed_items, 0)
        self.assertEqual(self.job.failed_items, 2)
        self.assertTrue(self.job.error_message.strip())
        self.assertIsNotNone(self.job.started_at)
        self.assertIsNotNone(self.job.finished_at)

    @tag("trend_lifecycle_regression")
    def test_empty_catalog_fails_without_starting_collector(self):
        Product.objects.all().delete()
        tasks, task = trend_task()
        with (
            patch.object(tasks, "GoogleTrendsCollector") as collector_class,
            patch.object(tasks, "collect_product_trend") as collect_product,
        ):
            result = task.apply(
                args=[str(self.job.id)],
                task_id="trends-empty-catalog",
                throw=True,
            ).get()

        self.job.refresh_from_db()
        self.assertEqual(self.job.status, JobRun.Status.FAILED)
        self.assertEqual(self.job.total_items, 0)
        self.assertEqual(self.job.processed_items, 0)
        self.assertEqual(self.job.failed_items, 0)
        self.assertIsNotNone(self.job.started_at)
        self.assertIsNotNone(self.job.finished_at)
        self.assertIn("no products", self.job.error_message.lower())
        self.assertEqual(result["total_items"], 0)
        collector_class.assert_not_called()
        collect_product.assert_not_called()

    @tag("trend_lifecycle_regression")
    def test_non_pending_job_is_rejected_without_lifecycle_changes(self):
        tasks, task = trend_task()
        initial_started_at = timezone.now() - timedelta(minutes=10)
        statuses = [
            JobRun.Status.RUNNING,
            JobRun.Status.SUCCEEDED,
            JobRun.Status.FAILED,
        ]

        for status_value in statuses:
            with self.subTest(status=status_value):
                initial_finished_at = (
                    None
                    if status_value == JobRun.Status.RUNNING
                    else timezone.now() - timedelta(minutes=5)
                )
                job = JobRun.objects.create(
                    job_type=JobRun.JobType.TREND_COLLECTION,
                    status=status_value,
                    celery_task_id=f"existing-{status_value}-task",
                    total_items=9,
                    processed_items=4,
                    failed_items=5,
                    error_message=f"existing {status_value} message",
                    details={"original_status": status_value},
                    started_at=initial_started_at,
                    finished_at=initial_finished_at,
                )
                original_values = {
                    field: getattr(job, field)
                    for field in [
                        "status",
                        "celery_task_id",
                        "total_items",
                        "processed_items",
                        "failed_items",
                        "error_message",
                        "details",
                        "started_at",
                        "finished_at",
                    ]
                }
                snapshot_count = TrendSnapshot.objects.count()

                with (
                    patch.object(
                        tasks,
                        "GoogleTrendsCollector",
                    ) as collector_class,
                    patch.object(
                        tasks,
                        "collect_product_trend",
                        return_value=SimpleNamespace(pk=1),
                    ) as collect_product,
                ):
                    with self.assertRaisesRegex(
                        Exception,
                        "(?i)(pending|state)",
                    ):
                        task.apply(
                            args=[str(job.id)],
                            task_id=f"replayed-{status_value}",
                            throw=True,
                        )

                job.refresh_from_db()
                for field, expected_value in original_values.items():
                    self.assertEqual(getattr(job, field), expected_value)
                self.assertEqual(TrendSnapshot.objects.count(), snapshot_count)
                collector_class.assert_not_called()
                collect_product.assert_not_called()

    def test_soft_timeout_on_first_product_stops_batch_and_finalizes_job(self):
        tasks, task = trend_task()
        with (
            patch.object(tasks, "GoogleTrendsCollector"),
            patch.object(
                tasks,
                "collect_product_trend",
                side_effect=SoftTimeLimitExceeded("soft time limit reached"),
            ) as collect_product,
        ):
            with self.assertRaises(SoftTimeLimitExceeded):
                task.apply(
                    args=[str(self.job.id)],
                    task_id="trends-timeout-first",
                    throw=True,
                )

        self.job.refresh_from_db()
        self.assertEqual(self.job.status, JobRun.Status.FAILED)
        self.assertEqual(self.job.total_items, 2)
        self.assertEqual(self.job.processed_items, 0)
        self.assertEqual(self.job.failed_items, 1)
        self.assertIsNotNone(self.job.started_at)
        self.assertIsNotNone(self.job.finished_at)
        self.assertIn("timeout", self.job.error_message.lower())
        self.assertEqual(collect_product.call_count, 1)

    def test_soft_timeout_preserves_success_before_stopping_batch(self):
        tasks, task = trend_task()
        with (
            patch.object(tasks, "GoogleTrendsCollector"),
            patch.object(
                tasks,
                "collect_product_trend",
                side_effect=[
                    SimpleNamespace(pk=1),
                    SoftTimeLimitExceeded("soft time limit reached"),
                ],
            ) as collect_product,
        ):
            with self.assertRaises(SoftTimeLimitExceeded):
                task.apply(
                    args=[str(self.job.id)],
                    task_id="trends-timeout-after-success",
                    throw=True,
                )

        self.job.refresh_from_db()
        self.assertEqual(self.job.status, JobRun.Status.FAILED)
        self.assertEqual(self.job.total_items, 2)
        self.assertEqual(self.job.processed_items, 1)
        self.assertEqual(self.job.failed_items, 1)
        self.assertIsNotNone(self.job.started_at)
        self.assertIsNotNone(self.job.finished_at)
        self.assertIn("timeout", self.job.error_message.lower())
        self.assertEqual(collect_product.call_count, 2)

    def test_soft_timeout_entering_collector_finalizes_job_before_reraising(self):
        tasks, task = trend_task()
        with (
            patch.object(tasks, "GoogleTrendsCollector") as collector_class,
            patch.object(tasks, "collect_product_trend") as collect_product,
        ):
            collector_class.return_value.__enter__.side_effect = (
                SoftTimeLimitExceeded("soft time limit reached during collector startup")
            )

            with self.assertRaises(SoftTimeLimitExceeded):
                task.apply(
                    args=[str(self.job.id)],
                    task_id="trends-timeout-enter",
                    throw=True,
                )

        self.job.refresh_from_db()
        self.assertEqual(self.job.status, JobRun.Status.FAILED)
        self.assertEqual(self.job.total_items, 2)
        self.assertEqual(self.job.processed_items, 0)
        self.assertEqual(self.job.failed_items, 0)
        self.assertIsNotNone(self.job.started_at)
        self.assertIsNotNone(self.job.finished_at)
        self.assertIn("timeout", self.job.error_message.lower())
        collect_product.assert_not_called()

    def test_soft_timeout_exiting_collector_preserves_processed_items(self):
        tasks, task = trend_task()
        with (
            patch.object(tasks, "GoogleTrendsCollector") as collector_class,
            patch.object(tasks, "collect_product_trend") as collect_product,
        ):
            collect_product.return_value = SimpleNamespace(pk=1)
            collector_class.return_value.__exit__.side_effect = (
                SoftTimeLimitExceeded("soft time limit reached during collector cleanup")
            )

            with self.assertRaises(SoftTimeLimitExceeded):
                task.apply(
                    args=[str(self.job.id)],
                    task_id="trends-timeout-exit",
                    throw=True,
                )

        self.job.refresh_from_db()
        self.assertEqual(self.job.status, JobRun.Status.FAILED)
        self.assertEqual(self.job.total_items, 2)
        self.assertEqual(self.job.processed_items, 2)
        self.assertEqual(self.job.failed_items, 0)
        self.assertIsNotNone(self.job.started_at)
        self.assertIsNotNone(self.job.finished_at)
        self.assertIn("timeout", self.job.error_message.lower())
        self.assertEqual(collect_product.call_count, 2)

    @tag("rate_limit_regression")
    def test_rate_limit_on_first_product_stops_batch_and_marks_remaining_failed(self):
        tasks, task = trend_task()
        error_type = rate_limit_error_type()
        with (
            patch.object(tasks, "GoogleTrendsCollector"),
            patch.object(
                tasks,
                "collect_product_trend",
                side_effect=error_type("Google Trends rate limit reached"),
            ) as collect_product,
        ):
            with self.assertRaises(error_type):
                task.apply(
                    args=[str(self.job.id)],
                    task_id="trends-rate-limit-first",
                    throw=True,
                )

        self.job.refresh_from_db()
        self.assertEqual(self.job.status, JobRun.Status.FAILED)
        self.assertEqual(self.job.total_items, 2)
        self.assertEqual(self.job.processed_items, 0)
        self.assertEqual(self.job.failed_items, self.job.total_items)
        self.assertEqual(
            self.job.processed_items + self.job.failed_items,
            self.job.total_items,
        )
        self.assertIsNotNone(self.job.started_at)
        self.assertIsNotNone(self.job.finished_at)
        self.assertIn("rate limit", self.job.error_message.lower())
        self.assertIs(self.job.details.get("rate_limited"), True)
        self.assertEqual(self.job.details.get("processed_items"), 0)
        self.assertEqual(self.job.details.get("skipped_items"), 2)
        self.assertEqual(collect_product.call_count, 1)

    @tag("rate_limit_regression")
    def test_rate_limit_after_success_preserves_progress_and_stops_batch(self):
        create_product("B099999999")
        tasks, task = trend_task()
        error_type = rate_limit_error_type()
        call_number = 0

        def collect_side_effect(*args, **kwargs):
            nonlocal call_number
            call_number += 1
            if call_number == 1:
                return SimpleNamespace(pk=1)
            raise error_type("Google Trends rate limit reached")

        with (
            patch.object(tasks, "GoogleTrendsCollector"),
            patch.object(
                tasks,
                "collect_product_trend",
                side_effect=collect_side_effect,
            ) as collect_product,
        ):
            with self.assertRaises(error_type):
                task.apply(
                    args=[str(self.job.id)],
                    task_id="trends-rate-limit-after-success",
                    throw=True,
                )

        self.job.refresh_from_db()
        self.assertEqual(self.job.status, JobRun.Status.FAILED)
        self.assertEqual(self.job.total_items, 3)
        self.assertEqual(self.job.processed_items, 1)
        self.assertEqual(self.job.failed_items, self.job.total_items - 1)
        self.assertEqual(
            self.job.processed_items + self.job.failed_items,
            self.job.total_items,
        )
        self.assertIsNotNone(self.job.started_at)
        self.assertIsNotNone(self.job.finished_at)
        self.assertIn("rate limit", self.job.error_message.lower())
        self.assertIs(self.job.details.get("rate_limited"), True)
        self.assertEqual(self.job.details.get("processed_items"), 1)
        self.assertEqual(self.job.details.get("skipped_items"), 2)
        self.assertEqual(collect_product.call_count, 2)
