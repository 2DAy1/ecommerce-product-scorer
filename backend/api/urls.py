from django.urls import path

from api.views import JobRunDetailView, ProductCollectionJobCreateView, ProductListView


urlpatterns = [
    path(
        "jobs/product-collection/",
        ProductCollectionJobCreateView.as_view(),
        name="product-collection-job-create",
    ),
    path("jobs/<uuid:job_id>/", JobRunDetailView.as_view(), name="job-run-detail"),
    path("products/", ProductListView.as_view(), name="product-list"),
]
