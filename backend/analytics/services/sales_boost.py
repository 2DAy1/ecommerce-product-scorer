from dataclasses import dataclass
from decimal import Decimal

from catalog.services.normalization import normalize_title


MAX_BOOST = Decimal("10.00")
EXACT_TITLE_BOOST = Decimal("7.50")
CATEGORY_KEYWORD_BONUS = Decimal("2.00")
KEYWORD_TOKEN_BOOST = Decimal("1.25")
MAX_KEYWORD_BOOST = Decimal("4.00")


@dataclass(frozen=True, slots=True)
class SalesBoostResult:
    score: Decimal
    reason: str
    successful_product_id: int | None = None
    matched_tokens: tuple[str, ...] = ()


def _normalized(value: object) -> str:
    return normalize_title(value) if isinstance(value, str) else ""


def _tokens(*values: object) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        tokens.update(
            token
            for token in _normalized(value).split()
            if len(token) > 1
        )
    return tokens


def _candidate_result(product, successful_product) -> SalesBoostResult:
    product_title = _normalized(product.normalized_title or product.title)
    successful_title = _normalized(
        successful_product.normalized_title or successful_product.title
    )
    product_category = _normalized(product.category)
    successful_category = _normalized(successful_product.category)
    title_matches = bool(product_title and product_title == successful_title)
    category_matches = bool(
        product_category and product_category == successful_category
    )

    if title_matches and category_matches:
        return SalesBoostResult(
            score=MAX_BOOST,
            reason="Exact normalized title and category match",
            successful_product_id=successful_product.pk,
        )
    if title_matches:
        return SalesBoostResult(
            score=EXACT_TITLE_BOOST,
            reason="Exact normalized title match",
            successful_product_id=successful_product.pk,
        )

    product_tokens = _tokens(product_title, product.search_keyword)
    successful_tokens = _tokens(
        successful_title,
        *(successful_product.keywords or []),
    )
    matched_tokens = tuple(sorted(product_tokens & successful_tokens))
    if not matched_tokens:
        return SalesBoostResult(score=Decimal("0.00"), reason="No historical match")

    keyword_score = min(
        Decimal(len(matched_tokens)) * KEYWORD_TOKEN_BOOST,
        MAX_KEYWORD_BOOST,
    )
    score = keyword_score + (
        CATEGORY_KEYWORD_BONUS if category_matches else Decimal("0.00")
    )
    return SalesBoostResult(
        score=min(score, EXACT_TITLE_BOOST),
        reason=(
            "Category and keyword-token match: "
            if category_matches
            else "Keyword-token match: "
        )
        + ", ".join(matched_tokens),
        successful_product_id=successful_product.pk,
        matched_tokens=matched_tokens,
    )


def calculate_sales_boost(product, successful_products) -> SalesBoostResult:
    best = SalesBoostResult(score=Decimal("0.00"), reason="No historical match")
    for successful_product in successful_products:
        candidate = _candidate_result(product, successful_product)
        if candidate.score > best.score:
            best = candidate
    return best
