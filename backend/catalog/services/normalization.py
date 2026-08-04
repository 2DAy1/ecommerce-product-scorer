import html
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin, urlsplit, urlunsplit


AMAZON_ORIGIN = "https://www.amazon.com"
ASIN_PATTERN = re.compile(r"^[A-Z0-9]{10}$")
ASIN_URL_PATTERN = re.compile(
    r"/(?:dp|gp/product|gp/aw/d|product)/([A-Z0-9]{10})(?:[/?]|$)",
    re.IGNORECASE,
)
NUMBER_PATTERN = re.compile(r"\d+(?:[.,]\d+)?")
REVIEW_PATTERN = re.compile(r"([\d.,]+)\s*([KM])?", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ScrapedProduct:
    asin: str
    title: str
    category: str
    price: Decimal | None
    rating: Decimal | None
    reviews_count: int
    product_url: str
    image_url: str


def extract_asin(data_asin: str | None = None, url: str | None = None) -> str | None:
    candidate = (data_asin or "").strip().upper()
    if ASIN_PATTERN.fullmatch(candidate):
        return candidate

    match = ASIN_URL_PATTERN.search(html.unescape(url or ""))
    if not match:
        return None
    return match.group(1).upper()


def parse_price(value: str | None) -> Decimal | None:
    if not value:
        return None
    match = NUMBER_PATTERN.search(value.replace(",", ""))
    if not match:
        return None
    try:
        parsed = Decimal(match.group(0).replace(",", "."))
    except InvalidOperation:
        return None
    return parsed if parsed >= 0 else None


def parse_rating(value: str | None) -> Decimal | None:
    if not value:
        return None
    match = NUMBER_PATTERN.search(value)
    if not match:
        return None
    try:
        parsed = Decimal(match.group(0).replace(",", "."))
    except InvalidOperation:
        return None
    return parsed if Decimal("0") <= parsed <= Decimal("5") else None


def parse_reviews_count(value: str | None) -> int:
    if not value:
        return 0
    match = REVIEW_PATTERN.search(value.replace(" ", ""))
    if not match:
        return 0
    try:
        number = Decimal(match.group(1).replace(",", ""))
    except InvalidOperation:
        return 0
    multiplier = {"K": 1_000, "M": 1_000_000}.get(
        (match.group(2) or "").upper(),
        1,
    )
    return max(0, int(number * multiplier))


def normalize_title(value: str) -> str:
    normalized = value.casefold().replace("_", " ")
    normalized = re.sub(r"[^\w\s]", " ", normalized, flags=re.UNICODE)
    return " ".join(normalized.split())


def clean_product_url(value: str | None, asin: str | None = None) -> str:
    if not value:
        return f"{AMAZON_ORIGIN}/dp/{asin}" if asin else ""

    absolute_url = urljoin(AMAZON_ORIGIN, html.unescape(value or ""))
    resolved_asin = asin or extract_asin(url=absolute_url)
    if resolved_asin:
        return f"{AMAZON_ORIGIN}/dp/{resolved_asin}"
    if not absolute_url:
        return ""
    parts = urlsplit(absolute_url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
