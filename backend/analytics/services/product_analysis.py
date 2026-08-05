from billiard.exceptions import SoftTimeLimitExceeded
from django.db import transaction

from analytics.models import ProductAnalysis
from analytics.services.llm_analysis import validate_llm_explanation
from analytics.services.product_scoring import (
    build_fallback_explanation,
    calculate_product_score,
)


DETERMINISTIC_PROVIDER = "deterministic"
DETERMINISTIC_MODEL = "fallback-v1"


def create_product_analysis(
    product,
    *,
    trend_snapshot=None,
    successful_products=(),
    llm_client=None,
) -> ProductAnalysis:
    score = calculate_product_score(
        product,
        trend_snapshot=trend_snapshot,
        successful_products=successful_products,
    )
    reasoning = build_fallback_explanation(product, score)
    provider = DETERMINISTIC_PROVIDER
    model_name = DETERMINISTIC_MODEL
    llm_status = "not_configured"
    if llm_client is not None:
        try:
            llm_reasoning = llm_client.generate_explanation(
                product=product,
                score=score,
            )
            reasoning = validate_llm_explanation(llm_reasoning)
            provider = llm_client.provider
            model_name = llm_client.model
            llm_status = "succeeded"
        except SoftTimeLimitExceeded:
            raise
        except Exception:
            llm_status = "fallback_after_error"

    input_snapshot = dict(score.input_snapshot)
    input_snapshot["explanation"] = {
        "source": provider,
        "model": model_name,
        "llm_status": llm_status,
    }
    with transaction.atomic():
        return ProductAnalysis.objects.create(
            product=product,
            trend_score=score.trend_score,
            boost_score=score.boost_score,
            baseline_score=score.baseline_score,
            final_score=score.final_score,
            provider=provider,
            model_name=model_name,
            reasoning=reasoning,
            input_snapshot=input_snapshot,
        )
