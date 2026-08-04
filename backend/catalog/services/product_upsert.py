from collections.abc import Iterable

from django.utils import timezone

from catalog.models import Product
from catalog.services.normalization import ScrapedProduct, normalize_title


UPSERT_UPDATE_FIELDS = [
    "title",
    "normalized_title",
    "category",
    "price",
    "rating",
    "reviews_count",
    "product_url",
    "image_url",
    "last_seen_at",
    "updated_at",
]


def upsert_products(products: Iterable[ScrapedProduct]) -> int:
    unique_products = {product.asin: product for product in products}
    if not unique_products:
        return 0

    now = timezone.now()
    objects = [
        Product(
            asin=item.asin,
            title=item.title,
            normalized_title=normalize_title(item.title),
            category=item.category,
            price=item.price,
            rating=item.rating,
            reviews_count=item.reviews_count,
            product_url=item.product_url,
            image_url=item.image_url,
            search_keyword="",
            first_seen_at=now,
            last_seen_at=now,
            created_at=now,
            updated_at=now,
        )
        for item in unique_products.values()
    ]
    Product.objects.bulk_create(
        objects,
        update_conflicts=True,
        unique_fields=["asin"],
        update_fields=UPSERT_UPDATE_FIELDS,
    )
    return len(objects)
