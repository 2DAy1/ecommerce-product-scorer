import uuid

from django.db import models
from django.db.models import Q
from django.utils import timezone

from catalog.models import Product


class TrendSnapshot(models.Model):
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="trend_snapshots",
    )
    keyword = models.CharField(max_length=255)
    geo = models.CharField(max_length=16)
    period = models.CharField(max_length=64)
    current_interest = models.SmallIntegerField()
    average_interest = models.SmallIntegerField()
    growth_percent = models.DecimalField(max_digits=10, decimal_places=2)
    series = models.JSONField(default=list, blank=True)
    collected_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-collected_at"]
        indexes = [
            models.Index(
                fields=["product", "-collected_at"],
                name="an_trend_product_collected_idx",
            ),
            models.Index(
                fields=["keyword", "geo", "period"],
                name="an_trend_query_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(current_interest__gte=0)
                & Q(current_interest__lte=100),
                name="analytics_trend_current_between_0_100",
            ),
            models.CheckConstraint(
                condition=Q(average_interest__gte=0)
                & Q(average_interest__lte=100),
                name="analytics_trend_average_between_0_100",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.product.asin}: {self.keyword} ({self.geo}, {self.period})"


class ProductAnalysis(models.Model):
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="analyses",
    )
    trend_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    boost_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    baseline_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
    )
    final_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    provider = models.CharField(max_length=64, blank=True, default="")
    model_name = models.CharField(max_length=128, blank=True, default="")
    reasoning = models.TextField(blank=True, default="")
    input_snapshot = models.JSONField(default=dict, blank=True)
    calculated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-calculated_at", "-pk"]
        indexes = [
            models.Index(
                fields=["product", "-calculated_at"],
                name="an_analysis_product_calc_idx",
            )
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(final_score__gte=0) & Q(final_score__lte=100),
                name="analytics_analysis_final_between_0_100",
            )
        ]

    def __str__(self) -> str:
        return f"Analysis for {self.product.asin}: {self.final_score}"


class JobRun(models.Model):
    class JobType(models.TextChoices):
        PRODUCT_COLLECTION = "product_collection", "Product collection"
        TREND_COLLECTION = "trend_collection", "Trend collection"
        PRODUCT_ANALYSIS = "product_analysis", "Product analysis"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job_type = models.CharField(max_length=32, choices=JobType.choices)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )
    celery_task_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        db_index=True,
    )
    total_items = models.PositiveIntegerField(default=0)
    processed_items = models.PositiveIntegerField(default=0)
    failed_items = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True, default="")
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["job_type", "status", "-created_at"],
                name="an_job_type_status_created_idx",
            )
        ]

    def __str__(self) -> str:
        return f"{self.get_job_type_display()} — {self.get_status_display()}"
