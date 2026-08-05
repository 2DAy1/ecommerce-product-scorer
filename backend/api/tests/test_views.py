from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import tag
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from analytics.models import JobRun


class JobAndProductApiTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="api-user",
            password="test-password",
        )

    def test_unauthenticated_requests_are_rejected(self):
        responses = [
            self.client.post(reverse("product-collection-job-create")),
            self.client.get(reverse("product-list")),
        ]
        for response in responses:
            self.assertIn(
                response.status_code,
                [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN],
            )

    @patch("api.views.collect_amazon_products.delay")
    def test_authenticated_post_creates_and_queues_job(self, delay):
        delay.return_value = SimpleNamespace(id="celery-task-id")
        self.client.force_authenticate(self.user)

        response = self.client.post(reverse("product-collection-job-create"))

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        job = JobRun.objects.get(pk=response.data["id"])
        self.assertEqual(job.job_type, JobRun.JobType.PRODUCT_COLLECTION)
        self.assertEqual(job.status, JobRun.Status.PENDING)
        self.assertEqual(job.celery_task_id, "celery-task-id")
        delay.assert_called_once_with(str(job.id))

    @tag("trend_commit_hardening")
    def test_amazon_queue_failure_finalizes_job_and_truncates_error(self):
        self.client.force_authenticate(self.user)
        short_reason = "Redis broker unavailable"
        long_message = f"{short_reason}: " + ("x" * 2000)

        with patch(
            "api.views.collect_amazon_products.delay",
            side_effect=RuntimeError(long_message),
        ) as delay:
            response = self.client.post(reverse("product-collection-job-create"))

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(
            str(response.data["detail"]),
            "The background job queue is temporarily unavailable.",
        )
        job = JobRun.objects.get(job_type=JobRun.JobType.PRODUCT_COLLECTION)
        delay.assert_called_once_with(str(job.id))
        self.assertEqual(job.status, JobRun.Status.FAILED)
        self.assertIsNotNone(job.finished_at)
        self.assertIsNone(job.started_at)
        self.assertEqual(job.processed_items, 0)
        self.assertEqual(job.failed_items, 0)
        self.assertEqual(job.celery_task_id, "")
        self.assertLessEqual(len(job.error_message), 200)
        self.assertNotIn("Google Trends collection", job.error_message)
        self.assertNotEqual(job.error_message, long_message)
        self.assertIn(short_reason, job.error_message)

    def test_authenticated_job_detail_returns_counters(self):
        self.client.force_authenticate(self.user)
        job = JobRun.objects.create(
            job_type=JobRun.JobType.PRODUCT_COLLECTION,
            total_items=3,
            processed_items=2,
            failed_items=1,
        )

        response = self.client.get(reverse("job-run-detail", args=[job.id]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["processed_items"], 2)
