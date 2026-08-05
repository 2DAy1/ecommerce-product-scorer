from django.utils import timezone
from rest_framework import filters, generics, status
from rest_framework.exceptions import APIException
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from analytics.models import JobRun
from analytics.tasks import collect_amazon_products, collect_google_trends
from api.serializers import JobRunSerializer, ProductSerializer
from catalog.models import Product


class QueueUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "The background job queue is temporarily unavailable."


def _create_and_queue_job(job_type: str, task) -> JobRun:
    job = JobRun.objects.create(job_type=job_type)
    try:
        result = task.delay(str(job.id))
    except Exception as exc:
        job.status = JobRun.Status.FAILED
        if job_type == JobRun.JobType.TREND_COLLECTION:
            job.error_message = (
                f"Failed to enqueue Google Trends collection: {exc}"
            )[:200]
        else:
            job.error_message = str(exc)[:200]
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "error_message", "finished_at"])
        raise QueueUnavailable() from exc

    job.celery_task_id = result.id
    job.save(update_fields=["celery_task_id"])
    return job


class ProductCollectionJobCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request) -> Response:
        job = _create_and_queue_job(
            JobRun.JobType.PRODUCT_COLLECTION,
            collect_amazon_products,
        )
        return Response(JobRunSerializer(job).data, status=status.HTTP_202_ACCEPTED)


class TrendCollectionJobCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request) -> Response:
        job = _create_and_queue_job(
            JobRun.JobType.TREND_COLLECTION,
            collect_google_trends,
        )
        return Response(JobRunSerializer(job).data, status=status.HTTP_202_ACCEPTED)


class JobRunDetailView(generics.RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = JobRunSerializer
    queryset = JobRun.objects.all()
    lookup_url_kwarg = "job_id"


class ProductListView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = ProductSerializer
    queryset = Product.objects.all()
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["title", "category"]
    ordering_fields = ["last_seen_at", "rating", "reviews_count", "price"]
    ordering = ["-last_seen_at"]
