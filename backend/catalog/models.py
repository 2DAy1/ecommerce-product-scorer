from django.db import models
from django.db.models import Q
from django.utils import timezone


class Product(models.Model):
    asin = models.CharField(max_length=10, unique=True)
    title = models.CharField(max_length=500)
    normalized_title = models.CharField(max_length=500, db_index=True)
    category = models.CharField(max_length=255, db_index=True)
    price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    rating = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        null=True,
        blank=True,
    )
    reviews_count = models.BigIntegerField(default=0)
    product_url = models.URLField(max_length=2048)
    image_url = models.URLField(max_length=2048)
    search_keyword = models.CharField(max_length=255, blank=True, default="")
    first_seen_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-last_seen_at", "title"]
        constraints = [
            models.CheckConstraint(
                condition=Q(price__gte=0) | Q(price__isnull=True),
                name="catalog_product_price_gte_0",
            ),
            models.CheckConstraint(
                condition=(Q(rating__gte=0) & Q(rating__lte=5))
                | Q(rating__isnull=True),
                name="catalog_product_rating_between_0_5",
            ),
            models.CheckConstraint(
                condition=Q(reviews_count__gte=0),
                name="catalog_product_reviews_count_gte_0",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.asin} — {self.title}"


class SuccessfulProduct(models.Model):
    title = models.CharField(max_length=500)
    normalized_title = models.CharField(max_length=500, db_index=True)
    category = models.CharField(max_length=255, db_index=True)
    keywords = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["category", "title"]
        constraints = [
            models.UniqueConstraint(
                fields=["normalized_title", "category"],
                name="catalog_successful_title_category_unique",
            )
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.category})"
