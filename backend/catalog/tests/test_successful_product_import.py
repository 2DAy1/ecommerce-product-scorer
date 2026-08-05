from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase

from catalog.models import SuccessfulProduct
from catalog.services.successful_product_import import (
    SuccessfulProductImportError,
    import_successful_products,
    parse_successful_products_csv,
)


def csv_upload(content: str | bytes, *, name: str = "products.csv"):
    payload = content.encode("utf-8") if isinstance(content, str) else content
    return SimpleUploadedFile(name, payload, content_type="text/csv")


class SuccessfulProductCsvParserTests(SimpleTestCase):
    def test_parser_trims_fields_and_normalizes_keywords(self):
        rows = parse_successful_products_csv(
            csv_upload(
                "title,category,keywords\n"
                "  Winning   Product  ,  Home & Kitchen  , mug ; Travel   Mug ;MUG\n"
            )
        )

        self.assertEqual(
            rows,
            [
                {
                    "title": "Winning Product",
                    "normalized_title": "winning product",
                    "category": "Home & Kitchen",
                    "keywords": ["mug", "Travel Mug"],
                }
            ],
        )

    def test_parser_accepts_utf8_bom(self):
        rows = parse_successful_products_csv(
            csv_upload(
                b"\xef\xbb\xbftitle,category,keywords\n"
                b"Earbuds,Electronics,audio;earbuds\n"
            )
        )

        self.assertEqual(rows[0]["title"], "Earbuds")

    def test_parser_reports_row_number_for_invalid_row(self):
        with self.assertRaises(SuccessfulProductImportError) as raised:
            parse_successful_products_csv(
                csv_upload(
                    "title,category,keywords\n"
                    "Valid Product,Home,home\n"
                    ",Electronics,audio\n"
                )
            )

        self.assertIn("Row 3: title is required.", raised.exception.errors)

    def test_parser_reports_malformed_quoted_header(self):
        with self.assertRaises(SuccessfulProductImportError) as raised:
            parse_successful_products_csv(
                csv_upload('title,category,"keywords\nProduct,Home,home\n')
            )

        self.assertIn("Malformed CSV", raised.exception.errors[0])


class SuccessfulProductImportTests(TestCase):
    def test_invalid_csv_creates_no_records(self):
        with self.assertRaises(SuccessfulProductImportError):
            import_successful_products(
                csv_upload(
                    "title,category,keywords\n"
                    "Valid Product,Home,home\n"
                    ",Electronics,audio\n"
                )
            )

        self.assertEqual(SuccessfulProduct.objects.count(), 0)

    def test_repeated_import_upserts_by_normalized_title_and_category(self):
        content = (
            "title,category,keywords\n"
            "Winning Product,Home,home;popular\n"
            "Travel Mug,Kitchen,mug;travel\n"
        )

        first_result = import_successful_products(csv_upload(content))
        second_result = import_successful_products(csv_upload(content))

        self.assertEqual(
            first_result,
            {"created_count": 2, "updated_count": 0, "total_count": 2},
        )
        self.assertEqual(
            second_result,
            {"created_count": 0, "updated_count": 2, "total_count": 2},
        )
        self.assertEqual(SuccessfulProduct.objects.count(), 2)
