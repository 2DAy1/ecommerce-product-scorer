from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from catalog.models import SuccessfulProduct
from catalog.services.successful_product_import import MAX_CSV_FILE_SIZE


def csv_upload(content: str | bytes, *, name: str = "products.csv"):
    payload = content.encode("utf-8") if isinstance(content, str) else content
    return SimpleUploadedFile(name, payload, content_type="text/csv")


class SalesBoostApiTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="sales-boost-user",
            password="test-password",
        )
        self.list_url = reverse("sales-boost-list-create")
        self.import_url = reverse("sales-boost-import")

    def test_endpoints_require_authentication(self):
        responses = [
            self.client.get(self.list_url),
            self.client.post(
                self.list_url,
                {"title": "Product", "category": "Home", "keywords": []},
                format="json",
            ),
            self.client.post(
                self.import_url,
                {"file": csv_upload("title,category,keywords\nP,Home,home\n")},
                format="multipart",
            ),
        ]

        for response in responses:
            with self.subTest(path=response.request["PATH_INFO"]):
                self.assertIn(
                    response.status_code,
                    [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN],
                )

    def test_authenticated_list_is_paginated_and_deterministically_ordered(self):
        SuccessfulProduct.objects.create(
            title="Zulu",
            normalized_title="zulu",
            category="B Category",
        )
        SuccessfulProduct.objects.create(
            title="Alpha",
            normalized_title="alpha",
            category="A Category",
        )
        self.client.force_authenticate(self.user)

        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(
            [item["title"] for item in response.data["results"]],
            ["Alpha", "Zulu"],
        )

    def test_manual_create_trims_and_upserts_natural_key(self):
        self.client.force_authenticate(self.user)

        first_response = self.client.post(
            self.list_url,
            {
                "title": "  Winning   Product!  ",
                "category": "  Home  ",
                "keywords": [" popular ", "POPULAR", "travel   item"],
            },
            format="json",
        )
        second_response = self.client.post(
            self.list_url,
            {
                "title": "Winning_Product",
                "category": "Home",
                "keywords": ["updated"],
            },
            format="json",
        )

        self.assertEqual(first_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(SuccessfulProduct.objects.count(), 1)
        product = SuccessfulProduct.objects.get()
        self.assertEqual(product.title, "Winning_Product")
        self.assertEqual(product.normalized_title, "winning product")
        self.assertEqual(product.category, "Home")
        self.assertEqual(product.keywords, ["updated"])

    def test_invalid_manual_input_returns_structured_errors(self):
        self.client.force_authenticate(self.user)

        response = self.client.post(
            self.list_url,
            {"title": "!!!", "category": " ", "keywords": "not-a-list"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("title", response.data)
        self.assertIn("category", response.data)
        self.assertIn("keywords", response.data)
        self.assertEqual(SuccessfulProduct.objects.count(), 0)

    def test_valid_csv_import_and_repeat_are_deterministic(self):
        self.client.force_authenticate(self.user)
        content = (
            "title,category,keywords\n"
            "Earbuds,Electronics,audio;earbuds\n"
            "Travel Mug,Kitchen,mug;travel\n"
        )

        first_response = self.client.post(
            self.import_url,
            {"file": csv_upload(content)},
            format="multipart",
        )
        count_after_first = SuccessfulProduct.objects.count()
        second_response = self.client.post(
            self.import_url,
            {"file": csv_upload(content)},
            format="multipart",
        )

        self.assertEqual(first_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            first_response.data,
            {"created_count": 2, "updated_count": 0, "total_count": 2},
        )
        self.assertEqual(second_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            second_response.data,
            {"created_count": 0, "updated_count": 2, "total_count": 2},
        )
        self.assertEqual(count_after_first, 2)
        self.assertEqual(SuccessfulProduct.objects.count(), 2)

    def test_missing_file_returns_400(self):
        self.client.force_authenticate(self.user)

        response = self.client.post(self.import_url, {}, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("file", response.data)

    def test_empty_csv_returns_400(self):
        self.client.force_authenticate(self.user)

        response = self.client.post(
            self.import_url,
            {"file": csv_upload(b"")},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("empty", " ".join(response.data["file"]).lower())

    def test_missing_required_headers_returns_400(self):
        self.client.force_authenticate(self.user)

        response = self.client.post(
            self.import_url,
            {"file": csv_upload("title,category\nProduct,Home\n")},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("keywords", " ".join(response.data["file"]))

    def test_invalid_row_is_row_specific_and_atomic(self):
        self.client.force_authenticate(self.user)
        content = (
            "title,category,keywords\n"
            "Valid Product,Home,home\n"
            ",,\n"
        )

        response = self.client.post(
            self.import_url,
            {"file": csv_upload(content)},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Row 3", " ".join(response.data["file"]))
        self.assertEqual(SuccessfulProduct.objects.count(), 0)

    def test_non_csv_and_oversized_files_are_rejected(self):
        self.client.force_authenticate(self.user)

        cases = [
            csv_upload(
                "title,category,keywords\nProduct,Home,home\n",
                name="products.txt",
            ),
            csv_upload(b"x" * (MAX_CSV_FILE_SIZE + 1)),
        ]
        for uploaded_file in cases:
            with self.subTest(filename=uploaded_file.name, size=uploaded_file.size):
                response = self.client.post(
                    self.import_url,
                    {"file": uploaded_file},
                    format="multipart",
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(SuccessfulProduct.objects.count(), 0)
