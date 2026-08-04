import logging
from urllib.parse import urljoin, urlsplit

from catalog.services.normalization import (
    ScrapedProduct,
    clean_product_url,
    extract_asin,
    parse_price,
    parse_rating,
    parse_reviews_count,
)


logger = logging.getLogger(__name__)

CARD_SELECTORS = (
    "#gridItemRoot",
    ".zg-grid-general-faceout",
    "div.p13n-sc-uncoverable-faceout",
    "[data-asin]",
)
TITLE_SELECTORS = (
    "._cDEzb_p13n-sc-css-line-clamp-3_g3dy1",
    "._cDEzb_p13n-sc-css-line-clamp-2_EWgCb",
    "a[href*='/dp/'] span",
    "a[href*='/gp/product/'] span",
)
PRICE_SELECTORS = (".p13n-sc-price", ".a-price .a-offscreen")
RATING_SELECTORS = (".a-icon-alt", "[aria-label*='out of 5']")
REVIEWS_SELECTORS = (
    "a[href*='customerReviews'] span",
    "a[href*='#customerReviews'] span",
    "span.a-size-small",
)
LINK_SELECTORS = ("a[href*='/dp/']", "a[href*='/gp/product/']")
IMAGE_SELECTORS = ("img[src]",)
CATEGORY_SELECTORS = ("h1", ".zg-banner-text", "#zg_banner_text")


class AmazonScraperError(RuntimeError):
    """Base error for a complete scraper run."""


class BrowserStartupError(AmazonScraperError):
    """The Playwright browser process could not start."""


class TemporaryNetworkError(AmazonScraperError):
    """Amazon could not be reached because of a potentially temporary error."""


class NoProductsFoundError(AmazonScraperError):
    """No valid product cards were found in any configured category."""


class AmazonBestSellersScraper:
    def __init__(
        self,
        *,
        base_url: str,
        categories: list[str],
        products_per_category: int,
        request_timeout_seconds: int,
        headless: bool,
    ) -> None:
        self.base_url = base_url
        self.categories = categories
        self.products_per_category = max(1, products_per_category)
        self.request_timeout_ms = max(1, request_timeout_seconds) * 1_000
        self.headless = headless
        self.failed_items = 0
        self.categories_processed = 0

    @staticmethod
    def _first_text(scope, selectors: tuple[str, ...]) -> str:
        for selector in selectors:
            locator = scope.locator(selector)
            if locator.count():
                value = locator.first.text_content()
                if value and value.strip():
                    return value.strip()
        return ""

    @staticmethod
    def _first_attribute(scope, selectors: tuple[str, ...], name: str) -> str:
        for selector in selectors:
            locator = scope.locator(selector)
            if locator.count():
                value = locator.first.get_attribute(name)
                if value and value.strip():
                    return value.strip()
        return ""

    @staticmethod
    def _cards(page):
        for selector in CARD_SELECTORS:
            cards = page.locator(selector)
            if cards.count():
                return cards
        return page.locator(".__amazon_card_selector_did_not_match__")

    def parse_page(
        self,
        page,
        *,
        category: str,
        limit: int | None = None,
    ) -> list[ScrapedProduct]:
        products: list[ScrapedProduct] = []
        limit = limit or self.products_per_category
        cards = self._cards(page)

        for index in range(cards.count()):
            if len(products) >= limit:
                break
            card = cards.nth(index)
            asin_for_log = "unknown"
            try:
                href = self._first_attribute(card, LINK_SELECTORS, "href")
                asin = extract_asin(card.get_attribute("data-asin"), href)
                asin_for_log = asin or "missing"
                title = self._first_text(card, TITLE_SELECTORS)
                image_url = self._first_attribute(card, IMAGE_SELECTORS, "src")
                if not title:
                    title = self._first_attribute(card, IMAGE_SELECTORS, "alt")
                if not asin or not title:
                    logger.warning(
                        "Amazon card skipped category=%s asin=%s stage=validation",
                        category,
                        asin_for_log,
                    )
                    self.failed_items += 1
                    continue

                products.append(
                    ScrapedProduct(
                        asin=asin,
                        title=title,
                        category=category,
                        price=parse_price(self._first_text(card, PRICE_SELECTORS)),
                        rating=parse_rating(
                            self._first_text(card, RATING_SELECTORS)
                            or self._first_attribute(
                                card,
                                RATING_SELECTORS,
                                "aria-label",
                            )
                        ),
                        reviews_count=parse_reviews_count(
                            self._first_text(card, REVIEWS_SELECTORS)
                        ),
                        product_url=clean_product_url(href, asin),
                        image_url=image_url,
                    )
                )
                logger.info(
                    "Amazon product parsed category=%s asin=%s stage=parsed",
                    category,
                    asin,
                )
            except Exception:
                self.failed_items += 1
                logger.exception(
                    "Amazon card failed category=%s asin=%s stage=parse",
                    category,
                    asin_for_log,
                )
        return products

    def _navigate(self, page, url: str) -> None:
        try:
            response = page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=self.request_timeout_ms,
            )
        except Exception as exc:
            raise TemporaryNetworkError(f"Could not load Amazon page: {url}") from exc
        if response and response.status >= 500:
            raise TemporaryNetworkError(
                f"Amazon returned HTTP {response.status} for {url}"
            )

    def _category_name(self, page, fallback: str) -> str:
        return self._first_text(page, CATEGORY_SELECTORS) or fallback

    def _resolve_target(self, page, configured_category: str) -> tuple[str, str]:
        if configured_category.startswith(("http://", "https://")):
            path_name = urlsplit(configured_category).path.rstrip("/").split("/")[-1]
            return configured_category, path_name or "Best Sellers"

        self._navigate(page, self.base_url)
        link = page.get_by_role("link", name=configured_category, exact=True)
        if not link.count():
            link = page.get_by_role("link", name=configured_category)
        if not link.count():
            raise NoProductsFoundError(
                f"Amazon category link was not found: {configured_category}"
            )
        href = link.first.get_attribute("href") or ""
        return urljoin(self.base_url, href), configured_category

    def scrape(self) -> list[ScrapedProduct]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserStartupError(
                "Playwright is not installed in this container; run the task in worker"
            ) from exc

        self.failed_items = 0
        self.categories_processed = 0
        configured_targets = self.categories or [""]
        collected: dict[str, ScrapedProduct] = {}

        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(headless=self.headless)
            except Exception as exc:
                raise BrowserStartupError("Playwright Chromium could not start") from exc

            try:
                page = browser.new_page()
                page.set_default_timeout(self.request_timeout_ms)
                for configured_category in configured_targets:
                    if configured_category:
                        url, fallback_category = self._resolve_target(
                            page,
                            configured_category,
                        )
                    else:
                        url, fallback_category = self.base_url, "Best Sellers"

                    logger.info(
                        "Amazon category started category=%s stage=navigate",
                        fallback_category,
                    )
                    self._navigate(page, url)
                    category = self._category_name(page, fallback_category)
                    parsed = self.parse_page(page, category=category)
                    self.categories_processed += 1
                    for product in parsed:
                        collected[product.asin] = product
                    logger.info(
                        "Amazon category finished category=%s stage=complete products=%s",
                        category,
                        len(parsed),
                    )
            finally:
                browser.close()

        if not collected:
            raise NoProductsFoundError(
                "Amazon Best Sellers returned zero valid products; page layout or access may have changed"
            )
        return list(collected.values())
