import io

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

    def test_parser_reports_header_errors_in_existing_order(self):
        with self.assertRaises(SuccessfulProductImportError) as raised:
            parse_successful_products_csv(
                csv_upload("title,title,unexpected\nProduct,Duplicate,extra\n")
            )

        self.assertEqual(
            raised.exception.errors,
            [
                "Missing required headers: category, keywords.",
                "Unexpected headers: unexpected.",
                "Duplicate headers: title.",
            ],
        )

    def test_parser_reports_duplicate_natural_key_row_numbers(self):
        with self.assertRaises(SuccessfulProductImportError) as raised:
            parse_successful_products_csv(
                csv_upload(
                    "title,category,keywords\n"
                    "Winning Product,Home,home\n"
                    " Winning   Product ,Home,popular\n"
                )
            )

        self.assertEqual(
            raised.exception.errors,
            [
                "Row 3: duplicates row 2 by normalized title and category.",
            ],
        )

    def test_parser_rejects_oversized_payload_despite_file_size_metadata(self):
        payload = b"x" * (1024 * 1024 + 1)

        for declared_size in (None, 1):
            with self.subTest(declared_size=declared_size):
                uploaded_file = io.BytesIO(payload)
                uploaded_file.name = "products.csv"
                if declared_size is not None:
                    uploaded_file.size = declared_size

                with self.assertRaises(SuccessfulProductImportError) as raised:
                    parse_successful_products_csv(uploaded_file)

                self.assertEqual(
                    raised.exception.errors,
                    ["CSV file must not exceed 1048576 bytes."],
                )

    def test_parser_reports_body_level_csv_error_exactly(self):
        with self.assertRaises(SuccessfulProductImportError) as raised:
            parse_successful_products_csv(
                csv_upload(
                    "title,category,keywords\n"
                    "Valid Product,Home,home\n"
                    '"Unterminated Product,Home,home\n'
                )
            )

        self.assertEqual(
            raised.exception.errors,
            ["Row 2: malformed CSV: unexpected end of data."],
        )


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
