from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from catalog.models import Product
from catalog.services.normalization import ScrapedProduct
from catalog.services.product_upsert import upsert_products


def scraped_product(**overrides) -> ScrapedProduct:
    values = {
        "asin": "B012345678",
        "title": "First Product",
        "category": "Home",
        "price": Decimal("10.00"),
        "rating": Decimal("4.50"),
        "reviews_count": 100,
        "product_url": "https://www.amazon.com/dp/B012345678",
        "image_url": "https://example.com/product.jpg",
    }
    values.update(overrides)
    return ScrapedProduct(**values)


class ProductUpsertTests(TestCase):
    def test_repeat_updates_same_asin_and_preserves_first_seen(self):
        first_now = timezone.now() - timedelta(hours=1)
        second_now = timezone.now()

        with patch("catalog.services.product_upsert.timezone.now", return_value=first_now):
            self.assertEqual(upsert_products([scraped_product()]), 1)

        with patch("catalog.services.product_upsert.timezone.now", return_value=second_now):
            self.assertEqual(
                upsert_products(
                    [
                        scraped_product(
                            title="Updated Product",
                            price=Decimal("12.50"),
                            reviews_count=150,
                        )
                    ]
                ),
                1,
            )

        self.assertEqual(Product.objects.count(), 1)
        product = Product.objects.get(asin="B012345678")
        self.assertEqual(product.title, "Updated Product")
        self.assertEqual(product.normalized_title, "updated product")
        self.assertEqual(product.price, Decimal("12.50"))
        self.assertEqual(product.reviews_count, 150)
        self.assertEqual(product.first_seen_at, first_now)
        self.assertEqual(product.last_seen_at, second_now)

    def test_duplicate_asins_in_one_batch_are_deduplicated(self):
        count = upsert_products(
            [scraped_product(), scraped_product(title="Last version wins")]
        )
        self.assertEqual(count, 1)
        self.assertEqual(Product.objects.count(), 1)
        self.assertEqual(Product.objects.get().title, "Last version wins")

    def test_repeat_upsert_preserves_search_keyword(self):
        first_now = timezone.now() - timedelta(hours=1)
        second_now = timezone.now()

        with patch("catalog.services.product_upsert.timezone.now", return_value=first_now):
            upsert_products([scraped_product()])

        product = Product.objects.get(asin="B012345678")
        product.search_keyword = "wireless headphones"
        product.save(update_fields=["search_keyword"])

        with patch("catalog.services.product_upsert.timezone.now", return_value=second_now):
            upsert_products(
                [
                    scraped_product(
                        title="Updated Amazon Product",
                        price=Decimal("19.99"),
                    )
                ]
            )

        self.assertEqual(Product.objects.count(), 1)
        product.refresh_from_db()
        self.assertEqual(product.title, "Updated Amazon Product")
        self.assertEqual(product.price, Decimal("19.99"))
        self.assertEqual(product.last_seen_at, second_now)
        self.assertEqual(product.first_seen_at, first_now)
        self.assertEqual(product.search_keyword, "wireless headphones")
