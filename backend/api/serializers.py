from rest_framework import serializers

from analytics.models import JobRun
from catalog.models import Product, SuccessfulProduct
from catalog.services.normalization import normalize_title
from catalog.services.successful_product_import import (
    collapse_whitespace,
    normalize_keywords,
)


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


class SuccessfulProductSerializer(serializers.ModelSerializer):
    title = serializers.CharField(max_length=500, trim_whitespace=True)
    category = serializers.CharField(max_length=255, trim_whitespace=True)
    keywords = serializers.ListField(
        child=serializers.CharField(trim_whitespace=True),
        required=False,
        default=list,
        allow_empty=True,
    )

    class Meta:
        model = SuccessfulProduct
        fields = [
            "id",
            "title",
            "normalized_title",
            "category",
            "keywords",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "normalized_title",
            "created_at",
            "updated_at",
        ]
        validators = []

    def validate_title(self, value: str) -> str:
        title = collapse_whitespace(value)
        if not normalize_title(title):
            raise serializers.ValidationError(
                "Title must contain letters or numbers."
            )
        return title

    def validate_category(self, value: str) -> str:
        return collapse_whitespace(value)

    def validate_keywords(self, value: list[str]) -> list[str]:
        try:
            return normalize_keywords(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def create(self, validated_data: dict) -> SuccessfulProduct:
        title = validated_data["title"]
        category = validated_data["category"]
        keywords = validated_data.get("keywords", [])
        product, _ = SuccessfulProduct.objects.update_or_create(
            normalized_title=normalize_title(title),
            category=category,
            defaults={
                "title": title,
                "keywords": keywords,
            },
        )
        return product


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
