from django.db import transaction

from analytics.models import TrendSnapshot
from analytics.services.trend_keywords import select_trend_keyword


class TrendKeywordValidationError(ValueError):
    """A Product does not have a usable Google Trends keyword."""


def collect_product_trend(product, *, collector, geo: str, period: str) -> TrendSnapshot:
    keyword = select_trend_keyword(product)
    if not keyword:
        raise TrendKeywordValidationError(
            f"Product {product.pk} does not have a usable trend keyword"
        )

    metrics = collector.collect(keyword=keyword, geo=geo, period=period)

    with transaction.atomic():
        if not (product.search_keyword or "").strip():
            product.search_keyword = keyword
            product.save(update_fields=["search_keyword"])

        return TrendSnapshot.objects.create(
            product=product,
            keyword=keyword,
            geo=geo,
            period=period,
            current_interest=metrics.current_interest,
            average_interest=metrics.average_interest,
            growth_percent=metrics.growth_percent,
            series=metrics.series,
        )
