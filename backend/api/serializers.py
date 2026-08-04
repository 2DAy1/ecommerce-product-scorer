from rest_framework import serializers

from analytics.models import JobRun
from catalog.models import Product


class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = [
            "id",
            "asin",
            "title",
            "normalized_title",
            "category",
            "price",
            "rating",
            "reviews_count",
            "product_url",
            "image_url",
            "search_keyword",
            "first_seen_at",
            "last_seen_at",
            "created_at",
            "updated_at",
        ]


class JobRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = JobRun
        fields = [
            "id",
            "job_type",
            "status",
            "celery_task_id",
            "total_items",
            "processed_items",
            "failed_items",
            "error_message",
            "details",
            "created_at",
            "started_at",
            "finished_at",
        ]
