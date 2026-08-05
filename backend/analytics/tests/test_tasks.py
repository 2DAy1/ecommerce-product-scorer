from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase

from analytics.models import JobRun
from analytics.tasks import collect_amazon_products, schedule_amazon_collection
from catalog.tests.test_product_upsert import scraped_product


class CollectAmazonProductsTaskTests(TestCase):
    @patch("analytics.tasks.upsert_products", return_value=1)
    @patch("analytics.tasks.AmazonBestSellersScraper")
    def test_task_updates_job_run_on_success(self, scraper_class, upsert):
        scraper = scraper_class.return_value
        scraper.scrape.return_value = [scraped_product()]
        scraper.failed_items = 2
        scraper.categories_processed = 1
        job = JobRun.objects.create(job_type=JobRun.JobType.PRODUCT_COLLECTION)

        result = collect_amazon_products.apply(
            args=[str(job.id)],
            task_id="task-success",
            throw=True,
        ).get()

        job.refresh_from_db()
        self.assertEqual(job.status, JobRun.Status.SUCCEEDED)
        self.assertEqual(job.celery_task_id, "task-success")
        self.assertEqual(job.total_items, 3)
        self.assertEqual(job.processed_items, 1)
        self.assertEqual(job.failed_items, 2)
        self.assertIsNotNone(job.started_at)
        self.assertIsNotNone(job.finished_at)
        self.assertEqual(result["processed_items"], 1)
        upsert.assert_called_once()

    @patch("analytics.tasks.AmazonBestSellersScraper")
    def test_non_retryable_failure_marks_job_failed(self, scraper_class):
        scraper_class.return_value.scrape.side_effect = RuntimeError("bad page")
        job = JobRun.objects.create(job_type=JobRun.JobType.PRODUCT_COLLECTION)

        with self.assertRaises(RuntimeError):
            collect_amazon_products.apply(
                args=[str(job.id)],
                task_id="task-failure",
                throw=True,
            )

        job.refresh_from_db()
        self.assertEqual(job.status, JobRun.Status.FAILED)
        self.assertEqual(job.error_message, "bad page")
        self.assertIsNotNone(job.finished_at)


class AmazonCollectionScheduleTests(TestCase):
    def test_beat_schedules_amazon_collection_every_six_hours(self):
        schedule = settings.CELERY_BEAT_SCHEDULE[
            "amazon-products-every-six-hours"
        ]

        self.assertEqual(
            schedule["task"],
            "analytics.schedule_amazon_collection",
        )
        self.assertEqual(schedule["schedule"], 6 * 60 * 60)

    @patch("analytics.tasks.collect_amazon_products.delay")
    def test_scheduled_dispatch_creates_visible_job_run(self, delay):
        delay.return_value = SimpleNamespace(id="scheduled-collection-task")

        result = schedule_amazon_collection.apply(throw=True).get()

        job = JobRun.objects.get(pk=result["job_id"])
        self.assertEqual(job.job_type, JobRun.JobType.PRODUCT_COLLECTION)
        self.assertEqual(job.status, JobRun.Status.PENDING)
        self.assertEqual(job.celery_task_id, "scheduled-collection-task")
        delay.assert_called_once_with(str(job.id))

    @patch(
        "analytics.tasks.collect_amazon_products.delay",
        side_effect=RuntimeError("Redis broker unavailable"),
    )
    def test_scheduled_dispatch_failure_finalizes_job_run(self, delay):
        with self.assertRaises(RuntimeError):
            schedule_amazon_collection.apply(throw=True)

        job = JobRun.objects.get(job_type=JobRun.JobType.PRODUCT_COLLECTION)
        self.assertEqual(job.status, JobRun.Status.FAILED)
        self.assertIsNone(job.started_at)
        self.assertIsNotNone(job.finished_at)
        self.assertIn("Redis broker unavailable", job.error_message)
        self.assertLessEqual(len(job.error_message), 200)
        delay.assert_called_once_with(str(job.id))
