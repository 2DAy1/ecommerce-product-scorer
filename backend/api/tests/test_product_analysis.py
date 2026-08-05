from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from analytics.models import JobRun, ProductAnalysis
from catalog.models import Product


def create_product(asin="B000000001"):
    return Product.objects.create(
        asin=asin,
        title=f"Product {asin}",
        normalized_title=f"product {asin.lower()}",
        category="Electronics",
        product_url=f"https://www.amazon.com/dp/{asin}",
        image_url=f"https://images.example.com/{asin}.jpg",
    )


class ProductAnalysisApiTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="analysis-api-user",
            password="test-password",
        )
        self.job_url = reverse("product-analysis-job-create")
        self.product_url = reverse("product-list")

    def test_analysis_endpoint_requires_authentication(self):
        response = self.client.post(self.job_url)

        self.assertIn(
            response.status_code,
            [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN],
        )

    @patch("api.views.analyze_products.delay")
    def test_post_creates_one_pending_job_and_queues_one_task(self, delay):
        delay.return_value = SimpleNamespace(id="analysis-celery-task-id")
        self.client.force_authenticate(self.user)

        response = self.client.post(self.job_url)

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(JobRun.objects.count(), 1)
        job = JobRun.objects.get(pk=response.data["id"])
        self.assertEqual(job.job_type, JobRun.JobType.PRODUCT_ANALYSIS)
        self.assertEqual(job.status, JobRun.Status.PENDING)
        self.assertEqual(job.celery_task_id, "analysis-celery-task-id")
        delay.assert_called_once_with(str(job.id))

    @patch(
        "api.views.analyze_products.delay",
        side_effect=RuntimeError("Redis unavailable: " + "x" * 2000),
    )
    def test_queue_failure_finalizes_exactly_one_job(self, delay):
        self.client.force_authenticate(self.user)

        response = self.client.post(self.job_url)

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(JobRun.objects.count(), 1)
        job = JobRun.objects.get()
        self.assertEqual(job.status, JobRun.Status.FAILED)
        self.assertIsNone(job.started_at)
        self.assertIsNotNone(job.finished_at)
        self.assertEqual(job.celery_task_id, "")
        self.assertLessEqual(len(job.error_message), 200)

    def test_product_list_exposes_only_latest_analysis(self):
        product = create_product()
        ProductAnalysis.objects.create(
            product=product,
            final_score=Decimal("20.00"),
            reasoning="Old explanation",
            provider="deterministic",
        )
        ProductAnalysis.objects.create(
            product=product,
            final_score=Decimal("80.00"),
            reasoning="Latest explanation",
            provider="anthropic",
            model_name="test-model",
        )
        self.client.force_authenticate(self.user)

        response = self.client.get(self.product_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        latest = response.data["results"][0]["latest_analysis"]
        self.assertEqual(latest["final_score"], "80.00")
        self.assertEqual(latest["reasoning"], "Latest explanation")
        self.assertEqual(latest["provider"], "anthropic")
        self.assertNotIn("Old explanation", str(response.data))

    def test_products_without_analysis_remain_valid_and_paginated(self):
        for index in range(21):
            create_product(f"B{index:09d}")
        self.client.force_authenticate(self.user)

        response = self.client.get(self.product_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 21)
        self.assertEqual(len(response.data["results"]), 20)
        self.assertIsNone(response.data["results"][0]["latest_analysis"])
