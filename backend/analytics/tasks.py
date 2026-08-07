import logging
from dataclasses import dataclass, field

from billiard.exceptions import SoftTimeLimitExceeded
from celery import shared_task
from django.conf import settings
from django.utils import timezone

from analytics.models import JobRun, TrendSnapshot
from analytics.services.amazon_scraper import (
    AmazonBestSellersScraper,
    BrowserStartupError,
    TemporaryNetworkError,
)
from analytics.services.google_trends import (
    GOOGLE_TRENDS_RATE_LIMIT_MESSAGE,
    GoogleTrendsCollector,
    GoogleTrendsRateLimitError,
)
from analytics.services.llm_analysis import build_llm_client
from analytics.services.product_analysis import create_product_analysis
from analytics.services.trend_keywords import select_trend_keyword
from analytics.services.trend_persistence import collect_product_trend
from catalog.models import Product, SuccessfulProduct
from catalog.services.product_upsert import upsert_products


logger = logging.getLogger(__name__)

RETRYABLE_SCRAPER_ERRORS = (BrowserStartupError, TemporaryNetworkError)
TRENDS_SOFT_TIMEOUT_MESSAGE = "Google Trends collection stopped by soft timeout"
ANALYSIS_SOFT_TIMEOUT_MESSAGE = "Product analysis stopped by soft timeout"


@dataclass
class _BatchProgress:
    processed_items: int = 0
    failed_items: int = 0
    errors: list[dict[str, str | int]] = field(default_factory=list)


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


def _start_batch_job(job: JobRun, *, task_id: str | None, total_items: int) -> None:
    job.status = JobRun.Status.RUNNING
    job.celery_task_id = task_id or job.celery_task_id
    job.total_items = total_items
    job.processed_items = 0
    job.failed_items = 0
    job.error_message = ""
    job.details = {}
    job.started_at = job.started_at or timezone.now()
    job.finished_at = None
    job.save(
        update_fields=[
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
    )


def _finish_empty_job(job: JobRun, message: str) -> None:
    job.status = JobRun.Status.FAILED
    job.error_message = message
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "error_message", "finished_at"])


def _finalize_batch_job(
    job: JobRun,
    *,
    status: str,
    progress: _BatchProgress,
    error_message: str,
    details: dict[str, object],
) -> None:
    job.status = status
    job.processed_items = progress.processed_items
    job.failed_items = progress.failed_items
    job.error_message = error_message
    job.details = details
    job.finished_at = timezone.now()
    job.save(
        update_fields=[
            "status",
            "processed_items",
            "failed_items",
            "error_message",
            "details",
            "finished_at",
        ]
    )


def _job_result(job: JobRun) -> dict[str, int]:
    return {
        "total_items": job.total_items,
        "processed_items": job.processed_items,
        "failed_items": job.failed_items,
    }


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


@shared_task(name="analytics.schedule_amazon_collection")
def schedule_amazon_collection() -> dict[str, str]:
    job = JobRun.objects.create(job_type=JobRun.JobType.PRODUCT_COLLECTION)
    try:
        result = collect_amazon_products.delay(str(job.id))
    except Exception as exc:
        job.status = JobRun.Status.FAILED
        job.error_message = (
            f"Failed to enqueue scheduled Amazon collection: {exc}"
        )[:200]
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "error_message", "finished_at"])
        raise

    job.celery_task_id = result.id
    job.save(update_fields=["celery_task_id"])
    return {"job_id": str(job.id)}


@shared_task(
    bind=True,
    name="analytics.collect_google_trends",
    soft_time_limit=600,
    time_limit=660,
)
def collect_google_trends(self, job_id: str) -> dict[str, int]:
    job = JobRun.objects.get(
        pk=job_id,
        job_type=JobRun.JobType.TREND_COLLECTION,
    )
    if job.status != JobRun.Status.PENDING:
        raise ValueError(
            f"Trend collection JobRun must be pending, got {job.status}"
        )

    products = list(Product.objects.order_by("pk"))
    job.status = JobRun.Status.RUNNING
    job.celery_task_id = self.request.id or job.celery_task_id
    job.total_items = len(products)
    job.processed_items = 0
    job.failed_items = 0
    job.error_message = ""
    job.details = {}
    job.started_at = job.started_at or timezone.now()
    job.finished_at = None
    job.save(
        update_fields=[
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
    )

    if not products:
        job.status = JobRun.Status.FAILED
        job.error_message = "No products available for Google Trends collection"
        job.finished_at = timezone.now()
        job.save(
            update_fields=[
                "status",
                "error_message",
                "finished_at",
            ]
        )
        return {
            "total_items": job.total_items,
            "processed_items": job.processed_items,
            "failed_items": job.failed_items,
        }

    processed_items = 0
    failed_items = 0
    errors: list[dict[str, str | int]] = []

    try:
        with GoogleTrendsCollector(
            headless=settings.TRENDS_HEADLESS,
            request_timeout_seconds=settings.TRENDS_REQUEST_TIMEOUT_SECONDS,
        ) as collector:
            for product in products:
                keyword = ""
                try:
                    keyword = select_trend_keyword(product)
                    logger.info(
                        "Trend collection started product_id=%s asin=%s keyword=%s stage=collect",
                        product.pk,
                        product.asin,
                        keyword,
                    )
                    collect_product_trend(
                        product,
                        collector=collector,
                        geo=settings.TRENDS_GEO,
                        period=settings.TRENDS_PERIOD,
                    )
                    processed_items += 1
                    logger.info(
                        "Trend collection finished product_id=%s asin=%s keyword=%s stage=saved",
                        product.pk,
                        product.asin,
                        keyword,
                    )
                except SoftTimeLimitExceeded:
                    failed_items += 1
                    errors.append(
                        {
                            "product_id": product.pk,
                            "asin": product.asin,
                            "error": TRENDS_SOFT_TIMEOUT_MESSAGE,
                        }
                    )
                    logger.warning(
                        "Trend collection timed out product_id=%s asin=%s keyword=%s stage=timeout",
                        product.pk,
                        product.asin,
                        keyword,
                    )
                    raise
                except GoogleTrendsRateLimitError:
                    raise
                except Exception as exc:
                    failed_items += 1
                    errors.append(
                        {
                            "product_id": product.pk,
                            "asin": product.asin,
                            "error": str(exc)[:200],
                        }
                    )
                    logger.warning(
                        "Trend collection failed product_id=%s asin=%s keyword=%s stage=failed error=%s",
                        product.pk,
                        product.asin,
                        keyword,
                        str(exc)[:200],
                    )
    except SoftTimeLimitExceeded:
        job.status = JobRun.Status.FAILED
        job.processed_items = processed_items
        job.failed_items = failed_items
        job.error_message = TRENDS_SOFT_TIMEOUT_MESSAGE
        job.details = {
            "errors_count": failed_items,
            "errors": errors[:10],
            "timeout": True,
        }
        job.finished_at = timezone.now()
        job.save(
            update_fields=[
                "status",
                "processed_items",
                "failed_items",
                "error_message",
                "details",
                "finished_at",
            ]
        )
        raise
    except GoogleTrendsRateLimitError:
        previous_failed_items = failed_items
        skipped_items = max(
            0,
            len(products) - processed_items - previous_failed_items,
        )
        failed_items = previous_failed_items + skipped_items
        job.status = JobRun.Status.FAILED
        job.processed_items = processed_items
        job.failed_items = failed_items
        job.error_message = GOOGLE_TRENDS_RATE_LIMIT_MESSAGE
        job.details = {
            "rate_limited": True,
            "processed_items": processed_items,
            "skipped_items": skipped_items,
            "errors": errors[:10],
        }
        job.finished_at = timezone.now()
        job.save(
            update_fields=[
                "status",
                "processed_items",
                "failed_items",
                "error_message",
                "details",
                "finished_at",
            ]
        )
        raise
    except Exception as exc:
        unprocessed_items = len(products) - processed_items - failed_items
        failed_items += max(0, unprocessed_items)
        errors.append({"product_id": 0, "asin": "", "error": str(exc)[:200]})
        logger.exception("Trend collection batch failed stage=collector")

    if processed_items:
        status_value = JobRun.Status.SUCCEEDED
        error_message = (
            f"{failed_items} of {len(products)} products failed trend collection"
            if failed_items
            else ""
        )
    else:
        status_value = JobRun.Status.FAILED
        error_message = (
            f"All {failed_items} products failed trend collection"
            if products
            else "No products available for trend collection"
        )

    job.status = status_value
    job.processed_items = processed_items
    job.failed_items = failed_items
    job.error_message = error_message
    job.details = {
        "errors_count": failed_items,
        "errors": errors[:10],
    }
    job.finished_at = timezone.now()
    job.save(
        update_fields=[
            "status",
            "processed_items",
            "failed_items",
            "error_message",
            "details",
            "finished_at",
        ]
    )
    return {
        "total_items": job.total_items,
        "processed_items": job.processed_items,
        "failed_items": job.failed_items,
    }


def _load_analysis_context(products: list[Product]):
    product_ids = [product.pk for product in products]
    latest_snapshots = {
        snapshot.product_id: snapshot
        for snapshot in TrendSnapshot.objects.filter(product_id__in=product_ids)
        .order_by("product_id", "-collected_at", "-pk")
        .distinct("product_id")
    }
    successful_products = list(SuccessfulProduct.objects.order_by("pk"))
    llm_client = build_llm_client(settings)
    return latest_snapshots, successful_products, llm_client


def _analyze_product(
    product: Product,
    *,
    latest_snapshots: dict[int, TrendSnapshot],
    successful_products: list[SuccessfulProduct],
    llm_client,
    progress: _BatchProgress,
) -> None:
    try:
        create_product_analysis(
            product,
            trend_snapshot=latest_snapshots.get(product.pk),
            successful_products=successful_products,
            llm_client=llm_client,
        )
        progress.processed_items += 1
    except SoftTimeLimitExceeded:
        progress.failed_items += 1
        progress.errors.append(
            {
                "product_id": product.pk,
                "asin": product.asin,
                "error": ANALYSIS_SOFT_TIMEOUT_MESSAGE,
            }
        )
        raise
    except Exception as exc:
        error_message = str(exc)[:200]
        progress.failed_items += 1
        progress.errors.append(
            {
                "product_id": product.pk,
                "asin": product.asin,
                "error": error_message,
            }
        )
        logger.warning(
            "Product analysis failed product_id=%s asin=%s error=%s",
            product.pk,
            product.asin,
            error_message,
        )


def _run_product_analysis(
    products: list[Product],
    progress: _BatchProgress,
) -> None:
    latest_snapshots, successful_products, llm_client = _load_analysis_context(
        products
    )
    for product in products:
        _analyze_product(
            product,
            latest_snapshots=latest_snapshots,
            successful_products=successful_products,
            llm_client=llm_client,
            progress=progress,
        )


def _finalize_analysis_timeout(
    job: JobRun,
    *,
    total_items: int,
    progress: _BatchProgress,
) -> None:
    skipped_items = max(
        0,
        total_items - progress.processed_items - progress.failed_items,
    )
    progress.failed_items += skipped_items
    _finalize_batch_job(
        job,
        status=JobRun.Status.FAILED,
        progress=progress,
        error_message=ANALYSIS_SOFT_TIMEOUT_MESSAGE,
        details={
            "errors_count": progress.failed_items,
            "errors": progress.errors[:10],
            "timeout": True,
            "skipped_items": skipped_items,
        },
    )


def _finalize_analysis_batch_failure(
    job: JobRun,
    *,
    total_items: int,
    progress: _BatchProgress,
    exc: Exception,
) -> None:
    skipped_items = max(
        0,
        total_items - progress.processed_items - progress.failed_items,
    )
    progress.failed_items += skipped_items
    progress.errors.append(
        {"product_id": 0, "asin": "", "error": str(exc)[:200]}
    )
    _finalize_batch_job(
        job,
        status=JobRun.Status.FAILED,
        progress=progress,
        error_message="Product analysis batch failed",
        details={
            "errors_count": progress.failed_items,
            "errors": progress.errors[:10],
            "skipped_items": skipped_items,
        },
    )


def _finalize_analysis_completion(
    job: JobRun,
    *,
    total_items: int,
    progress: _BatchProgress,
) -> None:
    status = (
        JobRun.Status.SUCCEEDED
        if progress.processed_items
        else JobRun.Status.FAILED
    )
    error_message = (
        f"{progress.failed_items} of {total_items} products failed product analysis"
        if progress.failed_items
        else ""
    )
    _finalize_batch_job(
        job,
        status=status,
        progress=progress,
        error_message=error_message,
        details={
            "errors_count": progress.failed_items,
            "errors": progress.errors[:10],
        },
    )


@shared_task(
    bind=True,
    name="analytics.analyze_products",
    soft_time_limit=300,
    time_limit=330,
)
def analyze_products(self, job_id: str) -> dict[str, int]:
    job = JobRun.objects.get(
        pk=job_id,
        job_type=JobRun.JobType.PRODUCT_ANALYSIS,
    )
    if job.status != JobRun.Status.PENDING:
        raise ValueError(f"Product analysis JobRun must be pending, got {job.status}")

    products = list(Product.objects.order_by("pk"))
    _start_batch_job(
        job,
        task_id=self.request.id,
        total_items=len(products),
    )

    if not products:
        _finish_empty_job(job, "No products available for product analysis")
        return _job_result(job)

    progress = _BatchProgress()
    try:
        _run_product_analysis(products, progress)
    except SoftTimeLimitExceeded:
        _finalize_analysis_timeout(
            job,
            total_items=len(products),
            progress=progress,
        )
        raise
    except Exception as exc:
        _finalize_analysis_batch_failure(
            job,
            total_items=len(products),
            progress=progress,
            exc=exc,
        )
        raise

    _finalize_analysis_completion(
        job,
        total_items=len(products),
        progress=progress,
    )
    return _job_result(job)
