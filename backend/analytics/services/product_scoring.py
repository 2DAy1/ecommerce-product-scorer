from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP, localcontext

from analytics.services.sales_boost import SalesBoostResult, calculate_sales_boost


TWO_PLACES = Decimal("0.01")
ZERO = Decimal("0")
ONE_HUNDRED = Decimal("100")
REVIEW_CAP = 10_000
AMAZON_WEIGHT = Decimal("0.55")
TRENDS_WEIGHT = Decimal("0.35")


@dataclass(frozen=True, slots=True)
class ProductScore:
    baseline_score: Decimal
    trend_score: Decimal
    boost_score: Decimal
    final_score: Decimal
    boost: SalesBoostResult
    input_snapshot: dict


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _clamp(value: Decimal, lower: Decimal, upper: Decimal) -> Decimal:
    return max(lower, min(value, upper))


def _decimal(value: object, default: Decimal = ZERO) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return default


def _amazon_component(product) -> tuple[Decimal, Decimal, Decimal]:
    if product.rating is None:
        rating_score = Decimal("35.00")
    else:
        rating = _clamp(_decimal(product.rating), ZERO, Decimal("5"))
        rating_score = rating * Decimal("14")

    reviews_count = max(0, int(product.reviews_count or 0))
    capped_reviews = min(reviews_count, REVIEW_CAP)
    if capped_reviews == 0:
        review_score = ZERO
    else:
        with localcontext() as context:
            context.prec = 28
            review_score = (
                Decimal(capped_reviews + 1).log10()
                / Decimal(REVIEW_CAP + 1).log10()
                * Decimal("30")
            )
    return (
        _quantize(rating_score + review_score),
        _quantize(rating_score),
        _quantize(review_score),
    )


def _trend_component(snapshot) -> Decimal:
    if snapshot is None:
        return Decimal("0.00")
    current = _clamp(_decimal(snapshot.current_interest), ZERO, ONE_HUNDRED)
    average = _clamp(_decimal(snapshot.average_interest), ZERO, ONE_HUNDRED)
    growth_score = ZERO
    if snapshot.series:
        growth = _clamp(
            _decimal(snapshot.growth_percent),
            Decimal("-100"),
            ONE_HUNDRED,
        )
        growth_score = (growth + ONE_HUNDRED) / Decimal("2")
    return _quantize(
        current * Decimal("0.40")
        + average * Decimal("0.40")
        + growth_score * Decimal("0.20")
    )


def calculate_product_score(
    product,
    *,
    trend_snapshot=None,
    successful_products=(),
) -> ProductScore:
    baseline_score, rating_score, review_score = _amazon_component(product)
    trend_score = _trend_component(trend_snapshot)
    boost = calculate_sales_boost(product, successful_products)
    final_score = _quantize(
        baseline_score * AMAZON_WEIGHT
        + trend_score * TRENDS_WEIGHT
        + boost.score
    )
    final_score = _clamp(final_score, ZERO, ONE_HUNDRED)

    trend_input = None
    if trend_snapshot is not None:
        trend_input = {
            "snapshot_id": trend_snapshot.pk,
            "current_interest": trend_snapshot.current_interest,
            "average_interest": trend_snapshot.average_interest,
            "growth_percent": str(trend_snapshot.growth_percent),
            "series_points": len(trend_snapshot.series or []),
        }
    input_snapshot = {
        "formula_version": "scoring-v1",
        "weights": {"amazon": "0.55", "trends": "0.35", "boost_max": "10.00"},
        "amazon": {
            "rating": str(product.rating) if product.rating is not None else None,
            "reviews_count": max(0, int(product.reviews_count or 0)),
            "rating_score": str(rating_score),
            "review_score": str(review_score),
            "rank_available": False,
        },
        "trends": trend_input,
        "sales_boost": {
            "score": str(boost.score),
            "reason": boost.reason,
            "successful_product_id": boost.successful_product_id,
            "matched_tokens": list(boost.matched_tokens),
        },
    }
    return ProductScore(
        baseline_score=baseline_score,
        trend_score=trend_score,
        boost_score=boost.score,
        final_score=final_score,
        boost=boost,
        input_snapshot=input_snapshot,
    )


def build_fallback_explanation(product, score: ProductScore) -> str:
    rating_text = "missing rating" if product.rating is None else f"rating {product.rating}/5"
    trend_text = (
        f"trend score {score.trend_score}/100 from the latest snapshot"
        if score.input_snapshot["trends"] is not None
        else "no Google Trends snapshot; the trend contribution is reduced to zero"
    )
    boost_text = (
        f"Sales Boost {score.boost_score}/10 ({score.boost.reason})"
        if score.boost_score > ZERO
        else "no matching historically successful product"
    )
    if score.final_score >= Decimal("70"):
        recommendation = "strong candidate for further validation"
    elif score.final_score >= Decimal("45"):
        recommendation = "mixed candidate; validate the weaker signals"
    else:
        recommendation = "weak candidate; gather stronger demand evidence first"
    return (
        f"Amazon signal {score.baseline_score}/100 from {rating_text} and "
        f"{max(0, int(product.reviews_count or 0))} reviews; bestseller rank is not "
        f"stored. Google Trends: {trend_text}. Historical signal: {boost_text}. "
        f"Final score {score.final_score}/100: {recommendation}."
    )
