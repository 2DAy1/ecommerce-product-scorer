import importlib.util
from decimal import Decimal
from pathlib import Path
from unittest import skipUnless

from django.test import SimpleTestCase

from analytics.services.amazon_scraper import AmazonBestSellersScraper


PLAYWRIGHT_AVAILABLE = importlib.util.find_spec("playwright") is not None


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
