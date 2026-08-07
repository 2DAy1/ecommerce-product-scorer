import importlib.util
from decimal import Decimal
from pathlib import Path
from unittest import skipUnless

from billiard.exceptions import SoftTimeLimitExceeded
from django.test import SimpleTestCase

from analytics.services.amazon_scraper import (
    CARD_SELECTORS,
    IMAGE_SELECTORS,
    LINK_SELECTORS,
    PRICE_SELECTORS,
    RATING_SELECTORS,
    REVIEWS_SELECTORS,
    TITLE_SELECTORS,
    AmazonBestSellersScraper,
)


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
