from django.contrib import admin

from .models import JobRun, ProductAnalysis, TrendSnapshot


@admin.register(TrendSnapshot)
class TrendSnapshotAdmin(admin.ModelAdmin):
    list_display = (
        "product",
        "keyword",
        "geo",
        "period",
        "current_interest",
        "average_interest",
        "growth_percent",
        "collected_at",
    )
    list_filter = ("geo", "period", "collected_at")
    search_fields = ("product__asin", "product__title", "keyword")
    autocomplete_fields = ("product",)
    date_hierarchy = "collected_at"
    list_select_related = ("product",)
    ordering = ("-collected_at",)


@admin.register(ProductAnalysis)
class ProductAnalysisAdmin(admin.ModelAdmin):
    list_display = (
        "product",
        "final_score",
        "trend_score",
        "boost_score",
        "baseline_score",
        "provider",
        "calculated_at",
    )
    list_filter = ("provider", "calculated_at")
    search_fields = ("product__asin", "product__title", "provider", "model_name")
    autocomplete_fields = ("product",)
    list_select_related = ("product",)
    date_hierarchy = "calculated_at"
    ordering = ("-calculated_at",)


@admin.register(JobRun)
class JobRunAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "job_type",
        "status",
        "processed_items",
        "failed_items",
        "total_items",
        "created_at",
        "finished_at",
    )
    list_filter = ("job_type", "status", "created_at")
    search_fields = ("id", "celery_task_id", "error_message")
    readonly_fields = ("id", "created_at")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
