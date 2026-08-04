from unittest.mock import patch

from django.test import TestCase

from analytics.models import JobRun
from analytics.tasks import collect_amazon_products
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
