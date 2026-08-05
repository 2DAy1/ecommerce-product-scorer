from django.urls import path

from api.views import (
    JobRunDetailView,
    ProductCollectionJobCreateView,
    ProductListView,
    SuccessfulProductImportView,
    SuccessfulProductListCreateView,
    TrendCollectionJobCreateView,
)


urlpatterns = [
    path(
        "jobs/product-collection/",
        ProductCollectionJobCreateView.as_view(),
        name="product-collection-job-create",
    ),
    path(
        "jobs/trend-collection/",
        TrendCollectionJobCreateView.as_view(),
        name="trend-collection-job-create",
    ),
    path("jobs/<uuid:job_id>/", JobRunDetailView.as_view(), name="job-run-detail"),
    path("products/", ProductListView.as_view(), name="product-list"),
    path(
        "sales-boost/",
        SuccessfulProductListCreateView.as_view(),
        name="sales-boost-list-create",
    ),
    path(
        "sales-boost/import/",
        SuccessfulProductImportView.as_view(),
        name="sales-boost-import",
    ),
]
