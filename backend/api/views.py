from rest_framework import filters, generics, status
from rest_framework.exceptions import APIException
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from analytics.models import JobRun
from analytics.tasks import collect_amazon_products
from api.serializers import JobRunSerializer, ProductSerializer
from catalog.models import Product


class QueueUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "The background job queue is temporarily unavailable."


class ProductCollectionJobCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request) -> Response:
        job = JobRun.objects.create(job_type=JobRun.JobType.PRODUCT_COLLECTION)
        try:
            result = collect_amazon_products.delay(str(job.id))
        except Exception as exc:
            job.status = JobRun.Status.FAILED
            job.error_message = str(exc)
            job.save(update_fields=["status", "error_message"])
            raise QueueUnavailable() from exc

        job.celery_task_id = result.id
        job.save(update_fields=["celery_task_id"])
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
