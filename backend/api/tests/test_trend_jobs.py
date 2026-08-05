from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import tag
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from analytics.models import JobRun


class TrendCollectionJobApiTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="trend-api-user",
            password="test-password",
        )

    def test_endpoint_requires_authentication(self):
        response = self.client.post(reverse("trend-collection-job-create"))

        self.assertIn(
            response.status_code,
            [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN],
        )

    def test_authenticated_post_creates_pending_job_and_queues_task(self):
        url = reverse("trend-collection-job-create")
        self.client.force_authenticate(self.user)

        with patch("api.views.collect_google_trends.delay") as delay:
            delay.return_value = SimpleNamespace(id="trend-celery-task-id")
            response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        job = JobRun.objects.get(pk=response.data["id"])
        self.assertEqual(job.job_type, JobRun.JobType.TREND_COLLECTION)
        self.assertEqual(job.status, JobRun.Status.PENDING)
        self.assertEqual(response.data["status"], JobRun.Status.PENDING)
        delay.assert_called_once_with(str(job.id))

    def test_get_method_is_not_allowed(self):
        self.client.force_authenticate(self.user)

        response = self.client.get(reverse("trend-collection-job-create"))

        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    @tag("trend_lifecycle_regression")
    def test_queue_failure_finalizes_job_and_truncates_error_message(self):
        url = reverse("trend-collection-job-create")
        self.client.force_authenticate(self.user)
        short_reason = "Redis broker unavailable"
        long_message = f"{short_reason}: " + ("x" * 2000)

        with patch(
            "api.views.collect_google_trends.delay",
            side_effect=RuntimeError(long_message),
        ) as delay:
            response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(
            str(response.data["detail"]),
            "The background job queue is temporarily unavailable.",
        )
        job = JobRun.objects.get(job_type=JobRun.JobType.TREND_COLLECTION)
        delay.assert_called_once_with(str(job.id))
        self.assertEqual(job.status, JobRun.Status.FAILED)
        self.assertIsNone(job.started_at)
        self.assertEqual(job.processed_items, 0)
        self.assertEqual(job.failed_items, 0)
        self.assertEqual(job.celery_task_id, "")
        self.assertIn(short_reason, job.error_message)
        with self.subTest(check="finished lifecycle"):
            self.assertIsNotNone(job.finished_at)
        with self.subTest(check="reasonable error limit"):
            self.assertLessEqual(len(job.error_message), 200)
        with self.subTest(check="full exception is not stored"):
            self.assertNotEqual(job.error_message, long_message)
