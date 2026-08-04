from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase

from catalog.models import Product, SuccessfulProduct


def product_data(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "asin": "B000000001",
        "title": "Test Product",
        "normalized_title": "test product",
        "category": "Home",
        "product_url": "https://www.amazon.com/dp/B000000001",
        "image_url": "https://images.example.com/B000000001.jpg",
    }
    data.update(overrides)
    return data


class ProductModelTests(TestCase):
    def test_defaults_and_nullable_values(self) -> None:
        product = Product.objects.create(**product_data())

        self.assertIsNone(product.price)
        self.assertIsNone(product.rating)
        self.assertEqual(product.reviews_count, 0)
        self.assertEqual(product.search_keyword, "")
        self.assertIsNotNone(product.first_seen_at)
        self.assertIsNotNone(product.last_seen_at)
        self.assertIsNotNone(product.created_at)
        self.assertIsNotNone(product.updated_at)

    def test_asin_is_unique(self) -> None:
        Product.objects.create(**product_data())

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Product.objects.create(
                    **product_data(title="Duplicate", normalized_title="duplicate")
                )

    def test_price_must_not_be_negative(self) -> None:
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Product.objects.create(
                    **product_data(price=Decimal("-0.01"))
                )

    def test_rating_must_be_between_zero_and_five(self) -> None:
        for index, rating in enumerate((Decimal("-0.01"), Decimal("5.01")), 1):
            with self.subTest(rating=rating):
                with self.assertRaises(IntegrityError):
                    with transaction.atomic():
                        Product.objects.create(
                            **product_data(
                                asin=f"B00000000{index}",
                                rating=rating,
                            )
                        )

        Product.objects.create(
            **product_data(asin="B000000003", rating=Decimal("0.00"))
        )
        Product.objects.create(
            **product_data(asin="B000000004", rating=Decimal("5.00"))
        )

    def test_reviews_count_must_not_be_negative(self) -> None:
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Product.objects.create(**product_data(reviews_count=-1))


class SuccessfulProductModelTests(TestCase):
    def test_keywords_default_to_independent_lists(self) -> None:
        first = SuccessfulProduct.objects.create(
            title="First",
            normalized_title="first",
            category="Home",
        )
        second = SuccessfulProduct.objects.create(
            title="Second",
            normalized_title="second",
            category="Home",
        )

        self.assertEqual(first.keywords, [])
        self.assertEqual(second.keywords, [])
        self.assertIsNot(first.keywords, second.keywords)

    def test_normalized_title_and_category_are_unique_together(self) -> None:
        SuccessfulProduct.objects.create(
            title="Winning Product",
            normalized_title="winning product",
            category="Home",
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SuccessfulProduct.objects.create(
                    title="Same Product",
                    normalized_title="winning product",
                    category="Home",
                )

        SuccessfulProduct.objects.create(
            title="Winning Product in another category",
            normalized_title="winning product",
            category="Garden",
        )
