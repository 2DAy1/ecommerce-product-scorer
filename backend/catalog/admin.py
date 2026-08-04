from django.contrib import admin

from .models import Product, SuccessfulProduct


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "asin",
        "title",
        "category",
        "price",
        "rating",
        "reviews_count",
        "last_seen_at",
    )
    list_filter = ("category", "first_seen_at", "last_seen_at")
    search_fields = (
        "asin",
        "title",
        "normalized_title",
        "category",
        "search_keyword",
    )
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "last_seen_at"
    ordering = ("-last_seen_at",)


@admin.register(SuccessfulProduct)
class SuccessfulProductAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "updated_at")
    list_filter = ("category", "created_at", "updated_at")
    search_fields = ("title", "normalized_title", "category")
    readonly_fields = ("created_at", "updated_at")
    ordering = ("category", "title")
