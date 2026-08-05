import json
import logging
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from billiard.exceptions import SoftTimeLimitExceeded

from analytics.services.trend_metrics import TrendMetrics, calculate_trend_metrics


logger = logging.getLogger(__name__)

GOOGLE_TRENDS_EXPLORE_URL = "https://trends.google.com/trends/explore"
TIMELINE_RESPONSE_PATH = "/trends/api/widgetdata/multiline"
ANTI_XSSI_PREFIX = ")]}'"
GOOGLE_TRENDS_RATE_LIMIT_MESSAGE = "Google Trends rate limit exceeded"


class GoogleTrendsError(RuntimeError):
    """Base error for Google Trends collection."""


class GoogleTrendsParseError(GoogleTrendsError):
    """The Google Trends response is malformed."""


class GoogleTrendsNoDataError(GoogleTrendsParseError):
    """The response contains no valid timeline interest points."""


class GoogleTrendsTimeoutError(GoogleTrendsError):
    """Google Trends did not provide timeline data before the timeout."""


class GoogleTrendsBrowserError(GoogleTrendsError):
    """Playwright Chromium could not be started."""


class GoogleTrendsNetworkError(GoogleTrendsError):
    """A browser or network error interrupted collection."""


class GoogleTrendsRateLimitError(GoogleTrendsError):
    """Google Trends rejected collection because of rate limiting."""


def _decode_response(payload: str | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(payload, Mapping):
        return payload
    if not isinstance(payload, str):
        raise GoogleTrendsParseError("Google Trends response must be JSON text")

    response_text = payload.lstrip()
    if response_text.startswith(ANTI_XSSI_PREFIX):
        response_text = response_text[len(ANTI_XSSI_PREFIX) :].lstrip(",\r\n ")
    try:
        decoded = json.loads(response_text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise GoogleTrendsParseError("Google Trends response is not valid JSON") from exc
    if not isinstance(decoded, Mapping):
        raise GoogleTrendsParseError("Google Trends response root must be an object")
    return decoded


def parse_google_trends_response(
    payload: str | Mapping[str, Any],
) -> list[int]:
    decoded = _decode_response(payload)
    default = decoded.get("default")
    timeline = default.get("timelineData") if isinstance(default, Mapping) else None
    if not isinstance(timeline, list):
        raise GoogleTrendsNoDataError(
            "Google Trends response does not contain default.timelineData"
        )

    series: list[int] = []
    for point in timeline:
        if not isinstance(point, Mapping):
            continue
        values = point.get("value")
        if not isinstance(values, list) or not values:
            continue
        interest = values[0]
        if (
            not isinstance(interest, int)
            or isinstance(interest, bool)
            or not 0 <= interest <= 100
        ):
            continue
        series.append(interest)

    if not series:
        raise GoogleTrendsNoDataError(
            "Google Trends timeline contains no valid interest points"
        )
    return series


def is_matching_timeline_response(
    response_url: str,
    *,
    keyword: str,
    geo: str,
    period: str,
) -> bool:
    try:
        parsed_url = urlsplit(response_url)
        if (
            parsed_url.scheme != "https"
            or parsed_url.hostname != "trends.google.com"
            or parsed_url.path != TIMELINE_RESPONSE_PATH
        ):
            return False

        req_values = parse_qs(parsed_url.query).get("req", [])
        if len(req_values) != 1:
            return False
        request_data = json.loads(req_values[0])
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        return False

    if not isinstance(request_data, Mapping):
        return False
    if request_data.get("time") != period:
        return False
    if not isinstance(request_data.get("requestOptions"), Mapping):
        return False

    comparison_items = request_data.get("comparisonItem")
    if not isinstance(comparison_items, list) or len(comparison_items) != 1:
        return False
    comparison_item = comparison_items[0]
    if not isinstance(comparison_item, Mapping):
        return False

    request_geo = comparison_item.get("geo")
    if not isinstance(request_geo, Mapping) or request_geo.get("country") != geo:
        return False

    restrictions = comparison_item.get("complexKeywordsRestriction")
    if not isinstance(restrictions, Mapping):
        return False
    keywords = restrictions.get("keyword")
    if not isinstance(keywords, list) or len(keywords) != 1:
        return False
    keyword_item = keywords[0]
    return isinstance(keyword_item, Mapping) and keyword_item.get("value") == keyword


class GoogleTrendsCollector:
    def __init__(
        self,
        *,
        headless: bool = True,
        request_timeout_seconds: int = 30,
    ) -> None:
        self.headless = headless
        self.request_timeout_ms = max(1, request_timeout_seconds) * 1_000
        self._playwright = None
        self._browser = None

    def __enter__(self):
        self._start()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def _start(self) -> None:
        if self._browser is not None:
            return
        try:
            from playwright.sync_api import sync_playwright

            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=self.headless)
        except SoftTimeLimitExceeded:
            self.close()
            raise
        except Exception as exc:
            self.close()
            raise GoogleTrendsBrowserError(
                "Playwright Chromium could not start for Google Trends"
            ) from exc

    def close(self) -> None:
        browser = self._browser
        playwright = self._playwright
        self._browser = None
        self._playwright = None
        timeout_error = None

        if browser is not None:
            try:
                browser.close()
            except SoftTimeLimitExceeded as exc:
                timeout_error = exc
            except Exception:
                logger.warning("Google Trends Chromium cleanup failed", exc_info=True)
        if playwright is not None:
            try:
                playwright.stop()
            except SoftTimeLimitExceeded as exc:
                if timeout_error is None:
                    timeout_error = exc
            except Exception:
                logger.warning("Google Trends Playwright cleanup failed", exc_info=True)

        if timeout_error is not None:
            raise timeout_error

    def collect(self, *, keyword: str, geo: str, period: str) -> TrendMetrics:
        if not keyword.strip():
            raise GoogleTrendsNoDataError("Google Trends keyword is empty")

        started_here = self._browser is None
        if started_here:
            self._start()
        try:
            return self._collect_response(
                keyword=keyword.strip(),
                geo=geo,
                period=period,
            )
        finally:
            if started_here:
                self.close()

    def _collect_response(self, *, keyword: str, geo: str, period: str) -> TrendMetrics:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        query = urlencode({"q": keyword, "geo": geo, "date": period})
        explore_url = f"{GOOGLE_TRENDS_EXPLORE_URL}?{query}"
        page = None
        logger.info(
            "Google Trends request started keyword=%s geo=%s period=%s stage=navigate",
            keyword,
            geo,
            period,
        )
        try:
            page = self._browser.new_page()
            page.set_default_timeout(self.request_timeout_ms)
            with page.expect_response(
                lambda response: is_matching_timeline_response(
                    response.url,
                    keyword=keyword,
                    geo=geo,
                    period=period,
                ),
                timeout=self.request_timeout_ms,
            ) as response_info:
                navigation_response = page.goto(
                    explore_url,
                    wait_until="domcontentloaded",
                    timeout=self.request_timeout_ms,
                )
                if (
                    navigation_response is not None
                    and navigation_response.status == 429
                ):
                    raise GoogleTrendsRateLimitError(
                        GOOGLE_TRENDS_RATE_LIMIT_MESSAGE
                    )
            response = response_info.value
            if response.status >= 400:
                raise GoogleTrendsNetworkError(
                    f"Google Trends returned HTTP {response.status}"
                )
            try:
                response_text = response.text()
            except SoftTimeLimitExceeded:
                raise
            except Exception as exc:
                raise GoogleTrendsNetworkError(
                    "Could not read the Google Trends timeline response"
                ) from exc
            series = parse_google_trends_response(response_text)
            metrics = calculate_trend_metrics(series)
            logger.info(
                "Google Trends request finished keyword=%s geo=%s period=%s stage=parsed points=%s",
                keyword,
                geo,
                period,
                len(series),
            )
            return metrics
        except SoftTimeLimitExceeded:
            raise
        except PlaywrightTimeoutError as exc:
            raise GoogleTrendsTimeoutError(
                "Timed out waiting for Google Trends timeline data"
            ) from exc
        except GoogleTrendsError:
            raise
        except Exception as exc:
            raise GoogleTrendsNetworkError(
                "Google Trends browser navigation failed"
            ) from exc
        finally:
            if page is not None:
                try:
                    page.close()
                except SoftTimeLimitExceeded:
                    raise
                except Exception:
                    logger.warning("Google Trends page cleanup failed", exc_info=True)


def collect_product_trend(product, *, collector, geo: str, period: str):
    from analytics.services.trend_persistence import collect_product_trend as persist

    return persist(product, collector=collector, geo=geo, period=period)
