from typing import Protocol


MAX_KEYWORD_WORDS = 6
MAX_KEYWORD_LENGTH = 100


class KeywordSource(Protocol):
    search_keyword: str
    normalized_title: str


def select_trend_keyword(product: KeywordSource) -> str:
    existing_keyword = (product.search_keyword or "").strip()
    if existing_keyword:
        return existing_keyword

    tokens = [
        token
        for token in (product.normalized_title or "").split()
        if not token.isdigit()
    ][:MAX_KEYWORD_WORDS]
    return " ".join(tokens)[:MAX_KEYWORD_LENGTH].rstrip()
