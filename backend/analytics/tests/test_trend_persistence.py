from decimal import Decimal
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import TestCase, tag

from analytics.models import TrendSnapshot
from catalog.models import Product


def create_product(**overrides) -> Product:
    values = {
        "asin": "B012345678",
        "title": "Wireless Bluetooth Headphones",
        "normalized_title": "wireless bluetooth headphones",
        "category": "Electronics",
        "price": Decimal("29.99"),
        "rating": Decimal("4.50"),
        "reviews_count": 100,
        "product_url": "https://www.amazon.com/dp/B012345678",
        "image_url": "https://example.com/product.jpg",
        "search_keyword": "wireless headphones",
    }
    values.update(overrides)
    return Product.objects.create(**values)


def trend_result():
    return SimpleNamespace(
        series=[40, 50, 70],
        current_interest=70,
        average_interest=53,
        growth_percent=Decimal("75.00"),
    )


def collect_product(product: Product, collector: Mock):
    module = import_module("analytics.services.google_trends")
    return module.collect_product_trend(
        product,
        collector=collector,
        geo="US",
        period="today 3-m",
    )


class TrendPersistenceTests(TestCase):
    def test_collection_creates_snapshot_with_all_metrics(self):
        product = create_product()
        collector = Mock()
        collector.collect.return_value = trend_result()

        snapshot = collect_product(product, collector)

        self.assertEqual(TrendSnapshot.objects.count(), 1)
        self.assertEqual(snapshot.product, product)
        self.assertEqual(snapshot.keyword, "wireless headphones")
        self.assertEqual(snapshot.geo, "US")
        self.assertEqual(snapshot.period, "today 3-m")
        self.assertEqual(snapshot.current_interest, 70)
        self.assertEqual(snapshot.average_interest, 53)
        self.assertEqual(snapshot.growth_percent, Decimal("75.00"))
        self.assertEqual(snapshot.series, [40, 50, 70])
        self.assertIsNotNone(snapshot.collected_at)
        collector.collect.assert_called_once_with(
            keyword="wireless headphones",
            geo="US",
            period="today 3-m",
        )

    def test_repeated_collection_creates_a_new_snapshot(self):
        product = create_product()
        collector = Mock()
        collector.collect.return_value = trend_result()

        first = collect_product(product, collector)
        second = collect_product(product, collector)

        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(TrendSnapshot.objects.filter(product=product).count(), 2)

    def test_generated_keyword_is_saved_when_product_keyword_is_empty(self):
        product = create_product(search_keyword="")
        collector = Mock()
        collector.collect.return_value = trend_result()

        collect_product(product, collector)

        product.refresh_from_db()
        self.assertEqual(product.search_keyword, "wireless bluetooth headphones")

    def test_existing_product_keyword_is_not_changed(self):
        product = create_product(
            search_keyword="wireless headphones",
            normalized_title="different normalized title",
        )
        collector = Mock()
        collector.collect.return_value = trend_result()

        collect_product(product, collector)

        product.refresh_from_db()
        self.assertEqual(product.search_keyword, "wireless headphones")

    @tag("trend_commit_hardening")
    def test_snapshot_failure_rolls_back_generated_keyword(self):
        product = create_product(search_keyword="")
        collector = Mock()
        collector.collect.return_value = trend_result()

        with (
            patch(
                "analytics.services.trend_persistence.TrendSnapshot.objects.create",
                side_effect=RuntimeError("snapshot insert failed"),
            ),
            self.assertRaises(RuntimeError),
        ):
            collect_product(product, collector)

        product.refresh_from_db()
        self.assertEqual(product.search_keyword, "")
        self.assertFalse(TrendSnapshot.objects.exists())
