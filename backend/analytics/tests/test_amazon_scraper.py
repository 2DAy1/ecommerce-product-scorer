import builtins
import importlib.util
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import MagicMock, call, patch

from billiard.exceptions import SoftTimeLimitExceeded
from django.test import SimpleTestCase

from analytics.services.amazon_scraper import (
    CARD_SELECTORS,
    CATEGORY_SELECTORS,
    IMAGE_SELECTORS,
    LINK_SELECTORS,
    PRICE_SELECTORS,
    RATING_SELECTORS,
    REVIEWS_SELECTORS,
    TITLE_SELECTORS,
    AmazonBestSellersScraper,
    BrowserStartupError,
    NoProductsFoundError,
    TemporaryNetworkError,
)
from catalog.services.normalization import ScrapedProduct


PLAYWRIGHT_AVAILABLE = importlib.util.find_spec("playwright") is not None


class FakeElement:
    def __init__(self, *, text=None, attributes=None):
        self._text = text
        self._attributes = attributes or {}

    def text_content(self):
        return self._text

    def get_attribute(self, name):
        return self._attributes.get(name)


class FakeLocator:
    def __init__(self, elements=()):
        self._elements = list(elements)

    def count(self):
        return len(self._elements)

    @property
    def first(self):
        return self._elements[0]

    def nth(self, index):
        return self._elements[index]


class FakeCard(FakeElement):
    def __init__(self, *, data_asin=None, locators=None, failure=None):
        super().__init__(attributes={"data-asin": data_asin})
        self._locators = locators or {}
        self._failure = failure

    def locator(self, selector):
        if self._failure and selector == self._failure[0]:
            raise self._failure[1]
        return FakeLocator(self._locators.get(selector, ()))


class FakePage:
    def __init__(self, locators=None):
        self._locators = locators or {}

    def locator(self, selector):
        return FakeLocator(self._locators.get(selector, ()))


def element(*, text=None, **attributes):
    return FakeElement(text=text, attributes=attributes)


def product_card(
    asin,
    *,
    title="Example Product",
    href=None,
    price="$24.99",
    rating="4.7 out of 5 stars",
    reviews="1,234",
    image_alt="Example Product",
):
    href = href or f"/Example-Product/dp/{asin}"
    locators = {
        LINK_SELECTORS[0]: [element(href=href)],
        PRICE_SELECTORS[0]: [element(text=price)],
        RATING_SELECTORS[0]: [element(text=rating)],
        REVIEWS_SELECTORS[0]: [element(text=reviews)],
        IMAGE_SELECTORS[0]: [
            element(src="https://example.com/product.jpg", alt=image_alt)
        ],
    }
    if title is not None:
        locators[TITLE_SELECTORS[0]] = [element(text=title)]
    return FakeCard(data_asin=asin, locators=locators)


def page_with_cards(cards, *, selector=CARD_SELECTORS[0]):
    return FakePage({selector: cards})


def scraped_product(asin, *, title, category):
    return ScrapedProduct(
        asin=asin,
        title=title,
        category=category,
        price=None,
        rating=None,
        reviews_count=0,
        product_url=f"https://www.amazon.com/dp/{asin}",
        image_url="",
    )


def scrape_page(*, heading=None, status=200):
    page = MagicMock()
    page.goto.return_value = SimpleNamespace(status=status)
    heading_elements = [element(text=heading)] if heading else []
    page.locator.side_effect = lambda selector: FakeLocator(
        heading_elements if selector == CATEGORY_SELECTORS[0] else []
    )
    page.get_by_role.return_value = FakeLocator()
    return page


def playwright_runtime(page=None):
    page = page or scrape_page()
    browser = MagicMock()
    browser.new_page.return_value = page
    playwright = MagicMock()
    playwright.chromium.launch.return_value = browser
    manager = MagicMock()
    manager.__enter__.return_value = playwright
    manager.__exit__.return_value = False
    sync_playwright = MagicMock(return_value=manager)
    return sync_playwright, playwright, browser, page


class AmazonCardParserCharacterizationTests(SimpleTestCase):
    def make_scraper(self, *, products_per_category=2):
        return AmazonBestSellersScraper(
            base_url="https://www.amazon.com/Best-Sellers/zgbs",
            categories=[],
            products_per_category=products_per_category,
            request_timeout_seconds=30,
            headless=True,
        )

    def test_invalid_cards_do_not_consume_valid_product_limit(self):
        scraper = self.make_scraper(products_per_category=2)
        cards = [
            product_card(None, href="/not-a-product"),
            product_card("B000000001"),
            product_card("B000000002"),
            product_card("B000000003"),
        ]

        with self.assertLogs("analytics.services.amazon_scraper", level="WARNING"):
            products = scraper.parse_page(
                page_with_cards(cards),
                category="Home & Kitchen",
            )

        self.assertEqual(
            [product.asin for product in products],
            ["B000000001", "B000000002"],
        )
        self.assertEqual(scraper.failed_items, 1)

    def test_limit_none_uses_products_per_category(self):
        scraper = self.make_scraper(products_per_category=2)
        page = page_with_cards(
            [
                product_card("B000000001"),
                product_card("B000000002"),
                product_card("B000000003"),
            ]
        )

        products = scraper.parse_page(page, category="Home & Kitchen", limit=None)

        self.assertEqual(
            [product.asin for product in products],
            ["B000000001", "B000000002"],
        )

    def test_limit_zero_uses_products_per_category(self):
        scraper = self.make_scraper(products_per_category=2)
        page = page_with_cards(
            [
                product_card("B000000001"),
                product_card("B000000002"),
                product_card("B000000003"),
            ]
        )

        products = scraper.parse_page(page, category="Home & Kitchen", limit=0)

        self.assertEqual(
            [product.asin for product in products],
            ["B000000001", "B000000002"],
        )

    def test_invalid_data_asin_falls_back_to_asin_from_url(self):
        scraper = self.make_scraper()
        card = product_card(
            "invalid",
            href="/Example-Product/dp/B000000001/ref=zg_bs",
        )

        products = scraper.parse_page(
            page_with_cards([card]),
            category="Home & Kitchen",
        )

        self.assertEqual(products[0].asin, "B000000001")
        self.assertEqual(
            products[0].product_url,
            "https://www.amazon.com/dp/B000000001",
        )

    def test_missing_title_text_falls_back_to_image_alt(self):
        scraper = self.make_scraper()
        card = product_card(
            "B000000001",
            title=None,
            image_alt="Title from image",
        )

        products = scraper.parse_page(
            page_with_cards([card]),
            category="Home & Kitchen",
        )

        self.assertEqual(products[0].title, "Title from image")

    def test_rating_falls_back_to_aria_label(self):
        scraper = self.make_scraper()
        card = product_card("B000000001", rating=None)
        card._locators[RATING_SELECTORS[0]] = [element()]
        card._locators[RATING_SELECTORS[1]] = [
            element(**{"aria-label": "4.3 out of 5 stars"})
        ]

        products = scraper.parse_page(
            page_with_cards([card]),
            category="Home & Kitchen",
        )

        self.assertEqual(products[0].rating, Decimal("4.3"))

    def test_missing_asin_is_skipped_and_counted_once(self):
        scraper = self.make_scraper()
        card = product_card(None, href="/not-a-product")

        with self.assertLogs("analytics.services.amazon_scraper", level="WARNING"):
            products = scraper.parse_page(
                page_with_cards([card]),
                category="Home & Kitchen",
            )

        self.assertEqual(products, [])
        self.assertEqual(scraper.failed_items, 1)

    def test_missing_title_is_skipped_and_counted_once(self):
        scraper = self.make_scraper()
        card = product_card("B000000001", title=None, image_alt="")

        with self.assertLogs("analytics.services.amazon_scraper", level="WARNING"):
            products = scraper.parse_page(
                page_with_cards([card]),
                category="Home & Kitchen",
            )

        self.assertEqual(products, [])
        self.assertEqual(scraper.failed_items, 1)

    def test_card_exception_is_counted_logged_and_later_cards_continue(self):
        scraper = self.make_scraper()
        failing_card = product_card("B000000001")
        failing_card._failure = (
            TITLE_SELECTORS[0],
            RuntimeError("title locator failed"),
        )

        with self.assertLogs(
            "analytics.services.amazon_scraper",
            level="ERROR",
        ) as captured:
            products = scraper.parse_page(
                page_with_cards(
                    [failing_card, product_card("B000000002")]
                ),
                category="Home & Kitchen",
            )

        self.assertEqual([product.asin for product in products], ["B000000002"])
        self.assertEqual(scraper.failed_items, 1)
        self.assertIn("category=Home & Kitchen", captured.output[0])
        self.assertIn("asin=B000000001", captured.output[0])
        self.assertIn("stage=parse", captured.output[0])

    def test_card_and_field_selector_priority_is_unchanged(self):
        scraper = self.make_scraper()
        preferred_card = product_card(
            "invalid",
            href="/Preferred/dp/B000000001",
            title="Preferred title",
        )
        preferred_card._locators[LINK_SELECTORS[1]] = [
            element(href="/Secondary/gp/product/B000000002")
        ]
        preferred_card._locators[TITLE_SELECTORS[1]] = [
            element(text="Secondary title")
        ]
        preferred_card._locators[PRICE_SELECTORS[1]] = [element(text="$99.99")]
        preferred_card._locators[RATING_SELECTORS[1]] = [
            element(text="1.0 out of 5 stars")
        ]
        preferred_card._locators[REVIEWS_SELECTORS[1]] = [element(text="999")]
        secondary_card = product_card("B000000003")
        page = FakePage(
            {
                CARD_SELECTORS[0]: [preferred_card],
                CARD_SELECTORS[1]: [secondary_card],
            }
        )

        products = scraper.parse_page(page, category="Home & Kitchen")

        self.assertEqual(len(products), 1)
        self.assertEqual(products[0].asin, "B000000001")
        self.assertEqual(products[0].title, "Preferred title")
        self.assertEqual(products[0].price, Decimal("24.99"))
        self.assertEqual(products[0].rating, Decimal("4.7"))
        self.assertEqual(products[0].reviews_count, 1234)

    def test_soft_time_limit_during_card_parsing_is_counted_and_swallowed(self):
        scraper = self.make_scraper()
        timed_out_card = product_card("B000000001")
        timed_out_card._failure = (
            TITLE_SELECTORS[0],
            SoftTimeLimitExceeded("soft timeout"),
        )

        with self.assertLogs("analytics.services.amazon_scraper", level="ERROR"):
            products = scraper.parse_page(
                page_with_cards(
                    [timed_out_card, product_card("B000000002")]
                ),
                category="Home & Kitchen",
            )

        self.assertEqual([product.asin for product in products], ["B000000002"])
        self.assertEqual(scraper.failed_items, 1)


@skipUnless(PLAYWRIGHT_AVAILABLE, "Playwright is installed only in the worker image")
class AmazonScrapeCharacterizationTests(SimpleTestCase):
    base_url = "https://www.amazon.com/Best-Sellers/zgbs"

    def make_scraper(self, *, categories=None):
        return AmazonBestSellersScraper(
            base_url=self.base_url,
            categories=[] if categories is None else categories,
            products_per_category=10,
            request_timeout_seconds=30,
            headless=True,
        )

    def test_empty_categories_use_base_fallback_reset_counters_and_close(self):
        scraper = self.make_scraper()
        scraper.failed_items = 7
        scraper.categories_processed = 8
        product = scraped_product(
            "B000000001",
            title="Base product",
            category="Best Sellers",
        )
        sync_playwright, playwright, browser, page = playwright_runtime()

        with (
            patch("playwright.sync_api.sync_playwright", sync_playwright),
            patch.object(scraper, "parse_page", return_value=[product]) as parse_page,
            self.assertLogs("analytics.services.amazon_scraper", level="INFO") as logs,
        ):
            products = scraper.scrape()

        self.assertEqual(products, [product])
        playwright.chromium.launch.assert_called_once_with(headless=True)
        browser.new_page.assert_called_once_with()
        page.set_default_timeout.assert_called_once_with(30_000)
        page.goto.assert_called_once_with(
            self.base_url,
            wait_until="domcontentloaded",
            timeout=30_000,
        )
        parse_page.assert_called_once_with(page, category="Best Sellers")
        self.assertEqual(scraper.failed_items, 0)
        self.assertEqual(scraper.categories_processed, 1)
        browser.close.assert_called_once_with()
        self.assertIn(
            "Amazon category started category=Best Sellers stage=navigate",
            logs.output[0],
        )
        self.assertIn(
            "Amazon category finished category=Best Sellers "
            "stage=complete products=1",
            logs.output[1],
        )

    def test_direct_categories_preserve_order_and_replace_duplicates_in_place(self):
        first_url = "https://www.amazon.com/Best-Sellers/zgbs/category-one"
        second_url = "https://www.amazon.com/Best-Sellers/zgbs/category-two"
        scraper = self.make_scraper(categories=[first_url, second_url])
        first = scraped_product(
            "B000000001",
            title="First value",
            category="category-one",
        )
        stable = scraped_product(
            "B000000002",
            title="Stable value",
            category="category-one",
        )
        replacement = scraped_product(
            "B000000001",
            title="Replacement value",
            category="category-two",
        )
        later = scraped_product(
            "B000000003",
            title="Later value",
            category="category-two",
        )
        sync_playwright, _, browser, page = playwright_runtime()

        with (
            patch("playwright.sync_api.sync_playwright", sync_playwright),
            patch.object(
                scraper,
                "parse_page",
                side_effect=[[first, stable], [replacement, later]],
            ) as parse_page,
        ):
            products = scraper.scrape()

        self.assertEqual(products, [replacement, stable, later])
        self.assertEqual(
            page.goto.call_args_list,
            [
                call(first_url, wait_until="domcontentloaded", timeout=30_000),
                call(second_url, wait_until="domcontentloaded", timeout=30_000),
            ],
        )
        self.assertEqual(
            parse_page.call_args_list,
            [
                call(page, category="category-one"),
                call(page, category="category-two"),
            ],
        )
        page.get_by_role.assert_not_called()
        self.assertEqual(scraper.categories_processed, 2)
        browser.close.assert_called_once_with()

    def test_named_category_resolves_exact_link_and_uses_displayed_heading(self):
        scraper = self.make_scraper(categories=["Electronics"])
        page = scrape_page(heading="Displayed Electronics")
        page.get_by_role.return_value = FakeLocator(
            [element(href="/Best-Sellers-Electronics/zgbs/electronics")]
        )
        product = scraped_product(
            "B000000001",
            title="Electronics product",
            category="Displayed Electronics",
        )
        sync_playwright, _, browser, _ = playwright_runtime(page)

        with (
            patch("playwright.sync_api.sync_playwright", sync_playwright),
            patch.object(scraper, "parse_page", return_value=[product]) as parse_page,
        ):
            products = scraper.scrape()

        self.assertEqual(products, [product])
        self.assertEqual(
            page.goto.call_args_list,
            [
                call(
                    self.base_url,
                    wait_until="domcontentloaded",
                    timeout=30_000,
                ),
                call(
                    "https://www.amazon.com/Best-Sellers-Electronics/zgbs/electronics",
                    wait_until="domcontentloaded",
                    timeout=30_000,
                ),
            ],
        )
        page.get_by_role.assert_called_once_with(
            "link",
            name="Electronics",
            exact=True,
        )
        parse_page.assert_called_once_with(page, category="Displayed Electronics")
        browser.close.assert_called_once_with()

    def test_named_category_falls_back_to_partial_link_match(self):
        scraper = self.make_scraper(categories=["Home"])
        page = scrape_page()
        page.get_by_role.side_effect = [
            FakeLocator(),
            FakeLocator([element(href="/Best-Sellers-Home/zgbs/home")]),
        ]
        product = scraped_product(
            "B000000001",
            title="Home product",
            category="Home",
        )
        sync_playwright, _, _, _ = playwright_runtime(page)

        with (
            patch("playwright.sync_api.sync_playwright", sync_playwright),
            patch.object(scraper, "parse_page", return_value=[product]),
        ):
            products = scraper.scrape()

        self.assertEqual(products, [product])
        self.assertEqual(
            page.get_by_role.call_args_list,
            [
                call("link", name="Home", exact=True),
                call("link", name="Home"),
            ],
        )

    def test_category_count_increments_only_after_parse_completion(self):
        first_url = "https://www.amazon.com/Best-Sellers/zgbs/first"
        second_url = "https://www.amazon.com/Best-Sellers/zgbs/second"
        scraper = self.make_scraper(categories=[first_url, second_url])
        product = scraped_product(
            "B000000001",
            title="First product",
            category="first",
        )
        sync_playwright, _, browser, _ = playwright_runtime()

        with (
            patch("playwright.sync_api.sync_playwright", sync_playwright),
            patch.object(
                scraper,
                "parse_page",
                side_effect=[[product], RuntimeError("parse failed")],
            ),
            self.assertRaisesRegex(RuntimeError, "parse failed"),
        ):
            scraper.scrape()

        self.assertEqual(scraper.categories_processed, 1)
        browser.close.assert_called_once_with()

    def test_zero_products_raises_exact_error_after_closing_browser(self):
        scraper = self.make_scraper()
        sync_playwright, _, browser, _ = playwright_runtime()

        with (
            patch("playwright.sync_api.sync_playwright", sync_playwright),
            patch.object(scraper, "parse_page", return_value=[]),
            self.assertRaises(NoProductsFoundError) as raised,
        ):
            scraper.scrape()

        self.assertEqual(
            str(raised.exception),
            "Amazon Best Sellers returned zero valid products; "
            "page layout or access may have changed",
        )
        self.assertEqual(scraper.categories_processed, 1)
        browser.close.assert_called_once_with()

    def test_missing_named_category_raises_exact_error_and_closes_browser(self):
        scraper = self.make_scraper(categories=["Missing Category"])
        page = scrape_page()
        sync_playwright, _, browser, _ = playwright_runtime(page)

        with (
            patch("playwright.sync_api.sync_playwright", sync_playwright),
            self.assertRaises(NoProductsFoundError) as raised,
        ):
            scraper.scrape()

        self.assertEqual(
            str(raised.exception),
            "Amazon category link was not found: Missing Category",
        )
        self.assertEqual(scraper.categories_processed, 0)
        browser.close.assert_called_once_with()

    def test_navigation_failures_preserve_error_type_and_close_browser(self):
        url = "https://www.amazon.com/Best-Sellers/zgbs/electronics"

        for failure, expected_message in (
            (
                RuntimeError("network down"),
                f"Could not load Amazon page: {url}",
            ),
            (
                SimpleNamespace(status=503),
                f"Amazon returned HTTP 503 for {url}",
            ),
        ):
            with self.subTest(failure=failure):
                scraper = self.make_scraper(categories=[url])
                page = scrape_page()
                if isinstance(failure, Exception):
                    page.goto.side_effect = failure
                else:
                    page.goto.return_value = failure
                sync_playwright, _, browser, _ = playwright_runtime(page)

                with (
                    patch("playwright.sync_api.sync_playwright", sync_playwright),
                    self.assertRaises(TemporaryNetworkError) as raised,
                ):
                    scraper.scrape()

                self.assertEqual(str(raised.exception), expected_message)
                self.assertEqual(scraper.categories_processed, 0)
                browser.close.assert_called_once_with()

    def test_parse_failure_propagates_after_closing_browser(self):
        scraper = self.make_scraper()
        sync_playwright, _, browser, _ = playwright_runtime()

        with (
            patch("playwright.sync_api.sync_playwright", sync_playwright),
            patch.object(
                scraper,
                "parse_page",
                side_effect=RuntimeError("parse failed"),
            ),
            self.assertRaisesRegex(RuntimeError, "parse failed"),
        ):
            scraper.scrape()

        self.assertEqual(scraper.categories_processed, 0)
        browser.close.assert_called_once_with()

    def test_missing_playwright_import_preserves_browser_startup_error(self):
        scraper = self.make_scraper()
        scraper.failed_items = 4
        scraper.categories_processed = 5
        real_import = builtins.__import__

        def import_without_playwright(name, *args, **kwargs):
            if name == "playwright.sync_api":
                raise ImportError("playwright missing")
            return real_import(name, *args, **kwargs)

        with (
            patch("builtins.__import__", side_effect=import_without_playwright),
            self.assertRaises(BrowserStartupError) as raised,
        ):
            scraper.scrape()

        self.assertEqual(
            str(raised.exception),
            "Playwright is not installed in this container; run the task in worker",
        )
        self.assertIsInstance(raised.exception.__cause__, ImportError)
        self.assertEqual(scraper.failed_items, 4)
        self.assertEqual(scraper.categories_processed, 5)

    def test_browser_launch_failure_preserves_browser_startup_error(self):
        scraper = self.make_scraper()
        scraper.failed_items = 4
        scraper.categories_processed = 5
        sync_playwright, playwright, browser, _ = playwright_runtime()
        playwright.chromium.launch.side_effect = RuntimeError("launch failed")

        with (
            patch("playwright.sync_api.sync_playwright", sync_playwright),
            self.assertRaises(BrowserStartupError) as raised,
        ):
            scraper.scrape()

        self.assertEqual(str(raised.exception), "Playwright Chromium could not start")
        self.assertIsInstance(raised.exception.__cause__, RuntimeError)
        self.assertEqual(scraper.failed_items, 0)
        self.assertEqual(scraper.categories_processed, 0)
        browser.close.assert_not_called()

    def test_soft_timeout_during_launch_is_wrapped_as_browser_startup_error(self):
        scraper = self.make_scraper()
        timeout = SoftTimeLimitExceeded("launch timeout")
        sync_playwright, playwright, browser, _ = playwright_runtime()
        playwright.chromium.launch.side_effect = timeout

        with (
            patch("playwright.sync_api.sync_playwright", sync_playwright),
            self.assertRaises(BrowserStartupError) as raised,
        ):
            scraper.scrape()

        self.assertIs(raised.exception.__cause__, timeout)
        browser.close.assert_not_called()

    def test_soft_timeout_during_new_page_propagates_and_closes_browser(self):
        scraper = self.make_scraper()
        timeout = SoftTimeLimitExceeded("new page timeout")
        sync_playwright, _, browser, _ = playwright_runtime()
        browser.new_page.side_effect = timeout

        with (
            patch("playwright.sync_api.sync_playwright", sync_playwright),
            self.assertRaises(SoftTimeLimitExceeded) as raised,
        ):
            scraper.scrape()

        self.assertIs(raised.exception, timeout)
        browser.close.assert_called_once_with()

    def test_soft_timeout_during_navigation_is_wrapped_and_closes_browser(self):
        url = "https://www.amazon.com/Best-Sellers/zgbs/electronics"
        scraper = self.make_scraper(categories=[url])
        timeout = SoftTimeLimitExceeded("navigation timeout")
        page = scrape_page()
        page.goto.side_effect = timeout
        sync_playwright, _, browser, _ = playwright_runtime(page)

        with (
            patch("playwright.sync_api.sync_playwright", sync_playwright),
            self.assertRaises(TemporaryNetworkError) as raised,
        ):
            scraper.scrape()

        self.assertIs(raised.exception.__cause__, timeout)
        browser.close.assert_called_once_with()

    def test_soft_timeout_during_parse_propagates_and_closes_browser(self):
        scraper = self.make_scraper()
        timeout = SoftTimeLimitExceeded("category parse timeout")
        sync_playwright, _, browser, _ = playwright_runtime()

        with (
            patch("playwright.sync_api.sync_playwright", sync_playwright),
            patch.object(scraper, "parse_page", side_effect=timeout),
            self.assertRaises(SoftTimeLimitExceeded) as raised,
        ):
            scraper.scrape()

        self.assertIs(raised.exception, timeout)
        self.assertEqual(scraper.categories_processed, 0)
        browser.close.assert_called_once_with()

    def test_soft_timeout_during_browser_close_propagates(self):
        scraper = self.make_scraper()
        product = scraped_product(
            "B000000001",
            title="Base product",
            category="Best Sellers",
        )
        timeout = SoftTimeLimitExceeded("browser close timeout")
        sync_playwright, _, browser, _ = playwright_runtime()
        browser.close.side_effect = timeout

        with (
            patch("playwright.sync_api.sync_playwright", sync_playwright),
            patch.object(scraper, "parse_page", return_value=[product]),
            self.assertRaises(SoftTimeLimitExceeded) as raised,
        ):
            scraper.scrape()

        self.assertIs(raised.exception, timeout)
        self.assertEqual(scraper.categories_processed, 1)
        browser.close.assert_called_once_with()


@skipUnless(PLAYWRIGHT_AVAILABLE, "Playwright is installed only in the worker image")
class AmazonFixtureParserTests(SimpleTestCase):
    def test_parses_local_fixture_and_skips_invalid_card(self):
        from playwright.sync_api import sync_playwright

        html = (Path(__file__).parent / "fixtures" / "amazon_best_sellers.html").read_text(
            encoding="utf-8"
        )
        scraper = AmazonBestSellersScraper(
            base_url="https://www.amazon.com/Best-Sellers/zgbs",
            categories=[],
            products_per_category=10,
            request_timeout_seconds=30,
            headless=True,
        )

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.set_content(html)
                products = scraper.parse_page(page, category="Home & Kitchen")
            finally:
                browser.close()

        self.assertEqual(len(products), 1)
        self.assertEqual(products[0].asin, "B012345678")
        self.assertEqual(products[0].price, Decimal("24.99"))
        self.assertEqual(products[0].rating, Decimal("4.7"))
        self.assertEqual(products[0].reviews_count, 1234)
        self.assertEqual(
            products[0].product_url,
            "https://www.amazon.com/dp/B012345678",
        )
        self.assertEqual(scraper.failed_items, 1)
