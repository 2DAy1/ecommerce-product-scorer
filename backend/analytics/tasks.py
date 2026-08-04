from celery import shared_task
from django.conf import settings
from django.utils import timezone

from analytics.models import JobRun
from analytics.services.amazon_scraper import (
    AmazonBestSellersScraper,
    BrowserStartupError,
    TemporaryNetworkError,
)
from catalog.services.product_upsert import upsert_products


RETRYABLE_SCRAPER_ERRORS = (BrowserStartupError, TemporaryNetworkError)


def _mark_failed(job: JobRun, exc: Exception) -> None:
    job.status = JobRun.Status.FAILED
    job.error_message = str(exc)
    job.finished_at = timezone.now()
    job.save(
        update_fields=[
            "status",
            "error_message",
            "finished_at",
        ]
    )


@shared_task(
    bind=True,
    name="analytics.collect_amazon_products",
    max_retries=2,
    soft_time_limit=300,
    time_limit=330,
)
def collect_amazon_products(self, job_id: str) -> dict[str, int]:
    job = JobRun.objects.get(pk=job_id)
    job.status = JobRun.Status.RUNNING
    job.celery_task_id = self.request.id or job.celery_task_id
    job.started_at = job.started_at or timezone.now()
    job.finished_at = None
    job.error_message = ""
    job.save(
        update_fields=[
            "status",
            "celery_task_id",
            "started_at",
            "finished_at",
            "error_message",
        ]
    )

    scraper = AmazonBestSellersScraper(
        base_url=settings.AMAZON_BEST_SELLERS_URL,
        categories=settings.AMAZON_CATEGORIES,
        products_per_category=settings.AMAZON_PRODUCTS_PER_CATEGORY,
        request_timeout_seconds=settings.AMAZON_REQUEST_TIMEOUT_SECONDS,
        headless=settings.AMAZON_HEADLESS,
    )

    try:
        products = scraper.scrape()
        processed_items = upsert_products(products)
    except RETRYABLE_SCRAPER_ERRORS as exc:
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=15)
        _mark_failed(job, exc)
        raise
    except Exception as exc:
        _mark_failed(job, exc)
        raise

    job.status = JobRun.Status.SUCCEEDED
    job.total_items = len(products) + scraper.failed_items
    job.processed_items = processed_items
    job.failed_items = scraper.failed_items
    job.details = {
        "categories_processed": scraper.categories_processed,
        "products_collected": len(products),
    }
    job.finished_at = timezone.now()
    job.save(
        update_fields=[
            "status",
            "total_items",
            "processed_items",
            "failed_items",
            "details",
            "finished_at",
        ]
    )
    return {
        "total_items": job.total_items,
        "processed_items": job.processed_items,
        "failed_items": job.failed_items,
    }
