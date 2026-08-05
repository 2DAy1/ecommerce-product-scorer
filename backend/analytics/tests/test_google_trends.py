import json
from decimal import Decimal
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode
from unittest.mock import MagicMock, patch

from billiard.exceptions import SoftTimeLimitExceeded
from django.test import SimpleTestCase, tag


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "google_trends_timeline.json"
TIMELINE_API_PATH = "/trends/api/widgetdata/multiline"
KEYWORD = "wireless headphones"
GEO = "US"
PERIOD = "today 3-m"


def google_trends_module():
    return import_module("analytics.services.google_trends")


def trend_metrics_module():
    return import_module("analytics.services.trend_metrics")


class _MissingGoogleTrendsRateLimitError(RuntimeError):
    pass


def rate_limit_error_type(module):
    return getattr(
        module,
        "GoogleTrendsRateLimitError",
        _MissingGoogleTrendsRateLimitError,
    )


class _FailFastResponseInfo:
    def __init__(self):
        self.entered = False
        self.exit_exception_type = None
        self.value_read = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.exit_exception_type = exc_type
        if exc_type is None:
            raise AssertionError("HTTP 429 waited for a timeline response")
        return False

    @property
    def value(self):
        self.value_read = True
        raise AssertionError("HTTP 429 must not read response_info.value")


class _NavigationResponseInfo:
    def __init__(self, page, predicate):
        self.page = page
        self.predicate = predicate

    def __enter__(self):
        self.page.expectation_active = True
        self.page.response_predicate = self.predicate
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.page.expectation_active = False
        return False

    @property
    def value(self):
        if self.page.captured_response is None:
            raise AssertionError(
                "Timeline response emitted during navigation was missed"
            )
        return self.page.captured_response


class _NavigationTimelinePage:
    def __init__(self, navigation_response, timeline_response):
        self.navigation_response = navigation_response
        self.timeline_response = timeline_response
        self.expectation_active = False
        self.response_predicate = None
        self.captured_response = None
        self.response_emitted_during_navigation = False
        self.expect_response_calls = 0
        self.goto_calls = 0
        self.closed = False

    def set_default_timeout(self, timeout):
        self.default_timeout = timeout

    def expect_response(self, predicate, *, timeout):
        self.expect_response_calls += 1
        self.expect_response_timeout = timeout
        return _NavigationResponseInfo(self, predicate)

    def goto(self, url, *, wait_until, timeout):
        self.goto_calls += 1
        self.response_emitted_during_navigation = True
        if self.expectation_active and self.response_predicate(
            self.timeline_response
        ):
            self.captured_response = self.timeline_response
        return self.navigation_response

    def close(self):
        self.closed = True


def timeline_request(keyword=KEYWORD, geo=GEO, period=PERIOD):
    return {
        "time": period,
        "resolution": "WEEK",
        "locale": "en-US",
        "comparisonItem": [
            {
                "geo": {"country": geo},
                "complexKeywordsRestriction": {
                    "keyword": [{"type": "BROAD", "value": keyword}]
                },
            }
        ],
        "requestOptions": {
            "property": "",
            "backend": "IZG",
            "category": 0,
        },
    }


def timeline_response_url(
    *,
    scheme="https",
    hostname="trends.google.com",
    path=TIMELINE_API_PATH,
    keyword=KEYWORD,
    geo=GEO,
    period=PERIOD,
    include_req=True,
    req_value=None,
):
    query = {"token": "fixture-token"}
    if include_req:
        query["req"] = (
            json.dumps(timeline_request(keyword, geo, period))
            if req_value is None
            else req_value
        )
    return f"{scheme}://{hostname}{path}?{urlencode(query)}"


class GoogleTrendsResponseTests(SimpleTestCase):
    def test_fixture_parser_returns_interest_series(self):
        payload = FIXTURE_PATH.read_text(encoding="utf-8")

        series = google_trends_module().parse_google_trends_response(payload)

        self.assertEqual(series, [40, 50, 70])

    def test_metrics_use_last_value_rounded_average_and_growth(self):
        payload = FIXTURE_PATH.read_text(encoding="utf-8")
        series = google_trends_module().parse_google_trends_response(payload)

        metrics = trend_metrics_module().calculate_trend_metrics(series)

        self.assertEqual(metrics.series, [40, 50, 70])
        self.assertEqual(metrics.current_interest, 70)
        self.assertEqual(metrics.average_interest, 53)
        self.assertEqual(metrics.growth_percent, Decimal("75.00"))

    def test_invalid_points_are_filtered_when_a_valid_point_remains(self):
        payload = json.dumps(
            {
                "default": {
                    "timelineData": [
                        {},
                        {"value": []},
                        {"value": [-1]},
                        {"value": [101]},
                        {"value": ["invalid"]},
                        {"value": [60]},
                    ]
                }
            }
        )

        series = google_trends_module().parse_google_trends_response(payload)

        self.assertEqual(series, [60])
        self.assertTrue(all(0 <= value <= 100 for value in series))

    @tag("trend_commit_hardening")
    def test_integral_float_and_bool_interest_values_are_rejected(self):
        module = google_trends_module()

        for invalid_interest in (50.0, True):
            with self.subTest(invalid_interest=invalid_interest):
                payload = json.dumps(
                    {
                        "default": {
                            "timelineData": [{"value": [invalid_interest]}]
                        }
                    }
                )

                with self.assertRaises(module.GoogleTrendsNoDataError):
                    module.parse_google_trends_response(payload)

    @tag("trend_commit_hardening")
    def test_real_anti_xssi_prefix_is_removed_before_json_decoding(self):
        payload = ")]}',\n" + json.dumps(
            {
                "default": {
                    "timelineData": [
                        {"time": "1", "value": [20]},
                        {"time": "2", "value": [40]},
                    ]
                }
            }
        )

        self.assertTrue(payload.startswith(")]}',\n{"))
        series = google_trends_module().parse_google_trends_response(payload)
        metrics = trend_metrics_module().calculate_trend_metrics(series)

        self.assertEqual(series, [20, 40])
        self.assertEqual(metrics.current_interest, 40)
        self.assertEqual(metrics.average_interest, 30)
        self.assertEqual(metrics.growth_percent, Decimal("100.00"))

    def test_empty_timeline_raises_clear_parse_error(self):
        module = google_trends_module()
        payload = json.dumps({"default": {"timelineData": []}})

        with self.assertRaises(module.GoogleTrendsParseError):
            module.parse_google_trends_response(payload)

    def test_malformed_json_raises_clear_parse_error(self):
        module = google_trends_module()

        with self.assertRaises(module.GoogleTrendsParseError):
            module.parse_google_trends_response("{not valid JSON")

    def test_growth_uses_first_nonzero_baseline_after_leading_zero(self):
        metrics = trend_metrics_module().calculate_trend_metrics([0, 10, 20])

        self.assertEqual(metrics.growth_percent, Decimal("100.00"))

    def test_all_zero_series_has_zero_growth(self):
        metrics = trend_metrics_module().calculate_trend_metrics([0, 0, 0])

        self.assertEqual(metrics.growth_percent, Decimal("0.00"))

    def test_timeline_response_matcher_accepts_current_request(self):
        module = google_trends_module()
        response_url = timeline_response_url()

        self.assertTrue(
            module.is_matching_timeline_response(
                response_url,
                keyword=KEYWORD,
                geo=GEO,
                period=PERIOD,
            )
        )

    def test_timeline_response_matcher_rejects_unrelated_responses(self):
        module = google_trends_module()
        cases = {
            "insecure scheme": timeline_response_url(scheme="http"),
            "different hostname": timeline_response_url(
                hostname="trends.google.com.evil.example"
            ),
            "similar path": timeline_response_url(
                path=f"{TIMELINE_API_PATH}-preview"
            ),
            "different keyword": timeline_response_url(keyword="gaming mouse"),
            "different geo": timeline_response_url(geo="CA"),
            "different period": timeline_response_url(period="today 12-m"),
            "missing req": timeline_response_url(include_req=False),
            "malformed req": timeline_response_url(req_value="{not-json"),
            "URL-invalid req": timeline_response_url(req_value=b"\xff"),
            "foreign URL containing timeline path text": (
                "https://example.com/callback?"
                + urlencode(
                    {
                        "next": TIMELINE_API_PATH,
                        "req": json.dumps(timeline_request()),
                    }
                )
            ),
        }

        for label, response_url in cases.items():
            with self.subTest(label=label):
                self.assertFalse(
                    module.is_matching_timeline_response(
                        response_url,
                        keyword=KEYWORD,
                        geo=GEO,
                        period=PERIOD,
                    )
                )

    @tag("collector_navigation_regression")
    @tag("rate_limit_regression")
    def test_rate_limit_navigation_response_fails_fast_and_closes_page(self):
        module = google_trends_module()
        collector = module.GoogleTrendsCollector()
        page = MagicMock()
        collector._browser = MagicMock()
        collector._browser.new_page.return_value = page

        response_body_secret = "sensitive-429-response-body"
        cookie_secret = "sensitive-cookie-value"
        authorization_secret = "sensitive-authorization-value"
        navigation_response = MagicMock()
        navigation_response.status = 429
        navigation_response.text.return_value = response_body_secret
        navigation_response.headers = {
            "set-cookie": cookie_secret,
            "authorization": authorization_secret,
        }
        page.goto.return_value = navigation_response

        timeline_response = MagicMock()
        timeline_response.status = 200
        timeline_response.text.return_value = "timeline-response-body"
        response_info = _FailFastResponseInfo()
        page.expect_response.return_value = response_info

        with (
            patch.object(module, "parse_google_trends_response") as parse_response,
            patch.object(module, "calculate_trend_metrics"),
            self.assertRaises(rate_limit_error_type(module)) as raised,
        ):
            collector._collect_response(keyword=KEYWORD, geo=GEO, period=PERIOD)

        message = str(raised.exception)
        self.assertIn("rate limit", message.lower())
        self.assertNotIn(response_body_secret, message)
        self.assertNotIn(cookie_secret, message)
        self.assertNotIn(authorization_secret, message)
        self.assertFalse(response_info.value_read)
        if response_info.entered:
            self.assertIs(
                response_info.exit_exception_type,
                rate_limit_error_type(module),
            )
        parse_response.assert_not_called()
        page.close.assert_called_once()

    @tag("collector_navigation_regression")
    @tag("timeline_navigation_order_regression")
    def test_timeline_response_emitted_during_navigation_is_captured(self):
        module = google_trends_module()
        collector = module.GoogleTrendsCollector()

        navigation_response = MagicMock()
        navigation_response.status = 200
        timeline_response = MagicMock()
        timeline_response.url = timeline_response_url()
        timeline_response.status = 200
        timeline_response.text.return_value = "timeline-response-body"
        page = _NavigationTimelinePage(navigation_response, timeline_response)
        collector._browser = MagicMock()
        collector._browser.new_page.return_value = page
        expected_metrics = SimpleNamespace(current_interest=70)

        with (
            patch.object(
                module,
                "parse_google_trends_response",
                return_value=[40, 50, 70],
            ) as parse_response,
            patch.object(
                module,
                "calculate_trend_metrics",
                return_value=expected_metrics,
            ),
        ):
            result = collector._collect_response(
                keyword=KEYWORD,
                geo=GEO,
                period=PERIOD,
            )

        self.assertIs(result, expected_metrics)
        self.assertTrue(page.response_emitted_during_navigation)
        self.assertIs(page.captured_response, timeline_response)
        self.assertEqual(page.goto_calls, 1)
        self.assertEqual(page.expect_response_calls, 1)
        parse_response.assert_called_once_with("timeline-response-body")
        self.assertTrue(page.closed)

    @tag("rate_limit_regression")
    def test_successful_navigation_continues_waiting_for_timeline_response(self):
        module = google_trends_module()
        collector = module.GoogleTrendsCollector()
        page = MagicMock()
        collector._browser = MagicMock()
        collector._browser.new_page.return_value = page

        navigation_response = MagicMock()
        navigation_response.status = 200
        page.goto.return_value = navigation_response

        timeline_response = MagicMock()
        timeline_response.status = 200
        timeline_response.text.return_value = "timeline-response-body"
        response_info = SimpleNamespace(value=timeline_response)
        page.expect_response.return_value.__enter__.return_value = response_info
        expected_metrics = SimpleNamespace(current_interest=70)

        with (
            patch.object(
                module,
                "parse_google_trends_response",
                return_value=[40, 50, 70],
            ) as parse_response,
            patch.object(
                module,
                "calculate_trend_metrics",
                return_value=expected_metrics,
            ),
        ):
            result = collector._collect_response(
                keyword=KEYWORD,
                geo=GEO,
                period=PERIOD,
            )

        self.assertIs(result, expected_metrics)
        page.goto.assert_called_once()
        page.expect_response.assert_called_once()
        parse_response.assert_called_once_with("timeline-response-body")
        page.close.assert_called_once()

    @tag("rate_limit_regression")
    def test_matching_timeline_rate_limit_stops_collection(self):
        module = google_trends_module()
        collector = module.GoogleTrendsCollector()
        page = MagicMock()
        collector._browser = MagicMock()
        collector._browser.new_page.return_value = page

        navigation_response = MagicMock()
        navigation_response.status = 200
        page.goto.return_value = navigation_response

        timeline_response = MagicMock()
        timeline_response.status = 429
        timeline_response.text.return_value = "sensitive-rate-limit-body"
        response_info = SimpleNamespace(value=timeline_response)
        page.expect_response.return_value.__enter__.return_value = response_info

        with (
            patch.object(module, "parse_google_trends_response") as parse_response,
            self.assertRaises(rate_limit_error_type(module)) as raised,
        ):
            collector._collect_response(keyword=KEYWORD, geo=GEO, period=PERIOD)

        self.assertEqual(
            str(raised.exception),
            module.GOOGLE_TRENDS_RATE_LIMIT_MESSAGE,
        )
        timeline_response.text.assert_not_called()
        parse_response.assert_not_called()
        page.close.assert_called_once()


class GoogleTrendsSoftTimeoutPassthroughTests(SimpleTestCase):
    @tag("soft_timeout_passthrough_regression")
    def test_startup_soft_timeout_is_not_wrapped(self):
        module = google_trends_module()

        for boundary in ("playwright start", "browser launch"):
            with self.subTest(boundary=boundary):
                collector = module.GoogleTrendsCollector()
                sync_manager = MagicMock()
                playwright = MagicMock()
                if boundary == "playwright start":
                    sync_manager.start.side_effect = SoftTimeLimitExceeded(
                        "startup soft timeout"
                    )
                else:
                    sync_manager.start.return_value = playwright
                    playwright.chromium.launch.side_effect = SoftTimeLimitExceeded(
                        "launch soft timeout"
                    )

                with (
                    patch(
                        "playwright.sync_api.sync_playwright",
                        return_value=sync_manager,
                    ),
                    patch.object(collector, "_collect_response") as collect_response,
                    self.assertRaises(SoftTimeLimitExceeded) as raised,
                ):
                    collector._start()

                self.assertIs(type(raised.exception), SoftTimeLimitExceeded)
                collect_response.assert_not_called()
                self.assertIsNone(collector._browser)
                self.assertIsNone(collector._playwright)

    @tag("soft_timeout_passthrough_regression")
    def test_goto_soft_timeout_is_not_wrapped(self):
        module = google_trends_module()
        collector = module.GoogleTrendsCollector()
        page = MagicMock()
        collector._browser = MagicMock()
        collector._browser.new_page.return_value = page
        response_info = _FailFastResponseInfo()
        page.expect_response.return_value = response_info
        page.goto.side_effect = SoftTimeLimitExceeded("navigation soft timeout")

        with (
            patch.object(module, "parse_google_trends_response") as parse_response,
            self.assertRaises(SoftTimeLimitExceeded) as raised,
        ):
            collector._collect_response(keyword=KEYWORD, geo=GEO, period=PERIOD)

        self.assertIs(type(raised.exception), SoftTimeLimitExceeded)
        self.assertTrue(response_info.entered)
        self.assertFalse(response_info.value_read)
        parse_response.assert_not_called()
        page.close.assert_called_once()

    @tag("soft_timeout_passthrough_regression")
    def test_response_text_soft_timeout_is_not_wrapped(self):
        module = google_trends_module()
        collector = module.GoogleTrendsCollector()
        page = MagicMock()
        collector._browser = MagicMock()
        collector._browser.new_page.return_value = page
        page.goto.return_value.status = 200
        response = MagicMock(status=200)
        response.text.side_effect = SoftTimeLimitExceeded("response soft timeout")
        response_info = SimpleNamespace(value=response)
        page.expect_response.return_value.__enter__.return_value = response_info

        with (
            patch.object(module, "parse_google_trends_response") as parse_response,
            self.assertRaises(SoftTimeLimitExceeded) as raised,
        ):
            collector._collect_response(keyword=KEYWORD, geo=GEO, period=PERIOD)

        self.assertIs(type(raised.exception), SoftTimeLimitExceeded)
        parse_response.assert_not_called()
        page.close.assert_called_once()

    @tag("soft_timeout_passthrough_regression")
    def test_page_close_soft_timeout_is_not_swallowed(self):
        module = google_trends_module()
        collector = module.GoogleTrendsCollector()
        page = MagicMock()
        collector._browser = MagicMock()
        collector._browser.new_page.return_value = page
        page.goto.return_value.status = 200
        response = MagicMock(status=200)
        response.text.return_value = "timeline-response-body"
        response_info = SimpleNamespace(value=response)
        page.expect_response.return_value.__enter__.return_value = response_info
        page.close.side_effect = SoftTimeLimitExceeded("page cleanup soft timeout")

        with (
            patch.object(
                module,
                "parse_google_trends_response",
                return_value=[40, 50, 70],
            ),
            patch.object(
                module,
                "calculate_trend_metrics",
                return_value=SimpleNamespace(current_interest=70),
            ),
            self.assertRaises(SoftTimeLimitExceeded) as raised,
        ):
            collector._collect_response(keyword=KEYWORD, geo=GEO, period=PERIOD)

        self.assertIs(type(raised.exception), SoftTimeLimitExceeded)
        page.close.assert_called_once()

    @tag("soft_timeout_passthrough_regression")
    def test_browser_close_soft_timeout_is_not_swallowed(self):
        module = google_trends_module()
        collector = module.GoogleTrendsCollector()
        browser = MagicMock()
        playwright = MagicMock()
        browser.close.side_effect = SoftTimeLimitExceeded(
            "browser cleanup soft timeout"
        )
        collector._browser = browser
        collector._playwright = playwright

        with self.assertRaises(SoftTimeLimitExceeded) as raised:
            with collector:
                pass

        self.assertIs(type(raised.exception), SoftTimeLimitExceeded)
        browser.close.assert_called_once()
        playwright.stop.assert_called_once()
        self.assertIsNone(collector._browser)
        self.assertIsNone(collector._playwright)

    @tag("soft_timeout_passthrough_regression")
    def test_playwright_stop_soft_timeout_is_not_swallowed(self):
        module = google_trends_module()
        collector = module.GoogleTrendsCollector()
        browser = MagicMock()
        playwright = MagicMock()
        playwright.stop.side_effect = SoftTimeLimitExceeded(
            "playwright cleanup soft timeout"
        )
        collector._browser = browser
        collector._playwright = playwright

        with self.assertRaises(SoftTimeLimitExceeded) as raised:
            with collector:
                pass

        self.assertIs(type(raised.exception), SoftTimeLimitExceeded)
        browser.close.assert_called_once()
        playwright.stop.assert_called_once()
        self.assertIsNone(collector._browser)
        self.assertIsNone(collector._playwright)
