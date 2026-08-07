import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from billiard.exceptions import SoftTimeLimitExceeded
from django.test import SimpleTestCase

from analytics.services import llm_analysis


class AnthropicAnalysisClientCharacterizationTests(SimpleTestCase):
    def setUp(self):
        self.client = llm_analysis.AnthropicAnalysisClient(
            api_key="test-api-key",
            model="claude-test-model",
            timeout_seconds=17,
        )
        self.product = SimpleNamespace(
            title="Café Headphones",
            category="Electronics",
            rating=Decimal("4.50"),
            reviews_count=-12,
        )
        self.score = SimpleNamespace(
            baseline_score=Decimal("71.25"),
            trend_score=Decimal("18.50"),
            boost_score=Decimal("6.75"),
            final_score=Decimal("96.50"),
            input_snapshot={
                "trends": {
                    "current_interest": 82,
                    "growth_percent": "25.00",
                }
            },
            boost=SimpleNamespace(reason="Matched a successful audio product"),
        )

    def response_context(self, raw_response):
        response = MagicMock()
        response.read.return_value = raw_response
        context = MagicMock()
        context.__enter__.return_value = response
        context.__exit__.return_value = False
        return context, response

    def generate_with_response(self, document):
        raw_response = json.dumps(document).encode("utf-8")
        context, response = self.response_context(raw_response)
        with patch.object(llm_analysis, "urlopen", return_value=context) as urlopen:
            result = self.client.generate_explanation(
                product=self.product,
                score=self.score,
            )
        return result, urlopen, response

    def test_request_and_prompt_contract_are_preserved(self):
        result, urlopen, response = self.generate_with_response(
            {"content": [{"type": "text", "text": "  Strong opportunity.  "}]}
        )

        self.assertEqual(result, "Strong opportunity.")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, llm_analysis.ANTHROPIC_MESSAGES_URL)
        self.assertEqual(request.full_url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(
            {key.lower(): value for key, value in request.header_items()},
            {
                "content-type": "application/json",
                "x-api-key": "test-api-key",
                "anthropic-version": "2023-06-01",
            },
        )
        self.assertEqual(urlopen.call_args.kwargs, {"timeout": 17})
        response.read.assert_called_once_with(llm_analysis.MAX_LLM_RESPONSE_BYTES + 1)

        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["model"], "claude-test-model")
        self.assertEqual(payload["max_tokens"], 350)
        self.assertEqual(payload["temperature"], 0)
        self.assertEqual(
            payload["system"],
            "Explain this deterministic ecommerce product score. Return a "
            "concise plain-text assessment with strengths, risks, and a "
            "recommendation. Do not change or invent the numeric score.",
        )
        self.assertEqual(len(payload["messages"]), 1)
        self.assertEqual(payload["messages"][0]["role"], "user")

        embedded_prompt = payload["messages"][0]["content"]
        self.assertIn("Café Headphones", embedded_prompt)
        self.assertEqual(
            json.loads(embedded_prompt),
            {
                "product": {
                    "title": "Café Headphones",
                    "category": "Electronics",
                    "rating": "4.50",
                    "reviews_count": 0,
                },
                "scores": {
                    "amazon": "71.25",
                    "trends": "18.50",
                    "sales_boost": "6.75",
                    "final": "96.50",
                },
                "trend": {
                    "current_interest": 82,
                    "growth_percent": "25.00",
                },
                "historical_match": "Matched a successful audio product",
            },
        )

    def test_first_text_content_item_is_selected_from_multiple_blocks(self):
        result, _, _ = self.generate_with_response(
            {
                "content": [
                    {"type": "tool_use", "name": "ignored"},
                    {"type": "text", "text": "First explanation."},
                    {"type": "text", "text": "Second explanation."},
                ]
            }
        )

        self.assertEqual(result, "First explanation.")

    def test_transport_errors_preserve_exact_translation(self):
        failures = (
            HTTPError(
                llm_analysis.ANTHROPIC_MESSAGES_URL,
                503,
                "unavailable",
                None,
                None,
            ),
            URLError("connection refused"),
            TimeoutError("request timed out"),
            OSError("socket failed"),
        )

        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                with (
                    patch.object(llm_analysis, "urlopen", side_effect=failure),
                    self.assertRaises(llm_analysis.LLMAnalysisError) as raised,
                ):
                    self.client.generate_explanation(
                        product=self.product,
                        score=self.score,
                    )

                self.assertEqual(
                    str(raised.exception),
                    "LLM explanation request failed",
                )
                self.assertIs(raised.exception.__cause__, failure)

    def test_soft_timeout_propagates_unchanged(self):
        timeout = SoftTimeLimitExceeded("LLM soft timeout")

        with (
            patch.object(llm_analysis, "urlopen", side_effect=timeout),
            self.assertRaises(SoftTimeLimitExceeded) as raised,
        ):
            self.client.generate_explanation(
                product=self.product,
                score=self.score,
            )

        self.assertIs(raised.exception, timeout)

    def test_oversized_response_preserves_exact_error(self):
        raw_response = b"x" * (llm_analysis.MAX_LLM_RESPONSE_BYTES + 1)
        context, response = self.response_context(raw_response)

        with (
            patch.object(llm_analysis, "urlopen", return_value=context),
            self.assertRaises(llm_analysis.LLMAnalysisError) as raised,
        ):
            self.client.generate_explanation(
                product=self.product,
                score=self.score,
            )

        self.assertEqual(
            str(raised.exception),
            "LLM explanation response is too large",
        )
        response.read.assert_called_once_with(llm_analysis.MAX_LLM_RESPONSE_BYTES + 1)

    def test_malformed_responses_preserve_exact_error(self):
        malformed_responses = {
            "invalid JSON": b"{not-json",
            "invalid UTF-8": b"\xff",
            "missing content": json.dumps({}).encode("utf-8"),
            "invalid content": json.dumps({"content": None}).encode("utf-8"),
            "no text item": json.dumps(
                {"content": [{"type": "tool_use", "name": "ignored"}]}
            ).encode("utf-8"),
        }

        for label, raw_response in malformed_responses.items():
            with self.subTest(label=label):
                context, _ = self.response_context(raw_response)
                with (
                    patch.object(llm_analysis, "urlopen", return_value=context),
                    self.assertRaises(llm_analysis.LLMAnalysisError) as raised,
                ):
                    self.client.generate_explanation(
                        product=self.product,
                        score=self.score,
                    )

                self.assertEqual(
                    str(raised.exception),
                    "LLM explanation response is malformed",
                )

    def test_empty_text_reaches_final_validation(self):
        with self.assertRaises(llm_analysis.LLMAnalysisError) as raised:
            self.generate_with_response(
                {"content": [{"type": "text", "text": "   "}]}
            )

        self.assertEqual(
            str(raised.exception),
            "LLM explanation response is empty",
        )

    def test_valid_explanation_is_trimmed_and_truncated_to_current_limit(self):
        explanation = "  " + ("x" * (llm_analysis.MAX_REASONING_LENGTH + 50)) + "  "

        result, _, _ = self.generate_with_response(
            {"content": [{"type": "text", "text": explanation}]}
        )

        self.assertEqual(result, "x" * llm_analysis.MAX_REASONING_LENGTH)
        self.assertEqual(len(result), 2_000)
