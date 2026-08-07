import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from billiard.exceptions import SoftTimeLimitExceeded


ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
MAX_LLM_RESPONSE_BYTES = 65_536
MAX_REASONING_LENGTH = 2_000


class LLMAnalysisError(RuntimeError):
    """The optional explanation provider did not return usable text."""


class AnthropicAnalysisClient:
    provider = "anthropic"

    def __init__(self, *, api_key: str, model: str, timeout_seconds: int):
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _build_prompt(*, product, score) -> dict:
        return {
            "product": {
                "title": product.title,
                "category": product.category,
                "rating": str(product.rating) if product.rating is not None else None,
                "reviews_count": max(0, int(product.reviews_count or 0)),
            },
            "scores": {
                "amazon": str(score.baseline_score),
                "trends": str(score.trend_score),
                "sales_boost": str(score.boost_score),
                "final": str(score.final_score),
            },
            "trend": score.input_snapshot["trends"],
            "historical_match": score.boost.reason,
        }

    def _build_request_payload(self, *, product, score) -> bytes:
        prompt = self._build_prompt(product=product, score=score)
        return json.dumps(
            {
                "model": self.model,
                "max_tokens": 350,
                "temperature": 0,
                "system": (
                    "Explain this deterministic ecommerce product score. Return a "
                    "concise plain-text assessment with strengths, risks, and a "
                    "recommendation. Do not change or invent the numeric score."
                ),
                "messages": [
                    {
                        "role": "user",
                        "content": json.dumps(prompt, ensure_ascii=False),
                    }
                ],
            }
        ).encode("utf-8")

    def _build_request(self, payload: bytes) -> Request:
        return Request(
            ANTHROPIC_MESSAGES_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": ANTHROPIC_VERSION,
            },
            method="POST",
        )

    def _send_request(self, request: Request) -> bytes:
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw_response = response.read(MAX_LLM_RESPONSE_BYTES + 1)
        except SoftTimeLimitExceeded:
            raise
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise LLMAnalysisError("LLM explanation request failed") from exc

        if len(raw_response) > MAX_LLM_RESPONSE_BYTES:
            raise LLMAnalysisError("LLM explanation response is too large")
        return raw_response

    @staticmethod
    def _extract_response_text(raw_response: bytes) -> str:
        try:
            document = json.loads(raw_response.decode("utf-8"))
            content = document.get("content")
            text = next(
                item.get("text", "")
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            )
        except (
            AttributeError,
            json.JSONDecodeError,
            StopIteration,
            TypeError,
            UnicodeError,
        ) as exc:
            raise LLMAnalysisError("LLM explanation response is malformed") from exc

        return text

    def generate_explanation(self, *, product, score) -> str:
        payload = self._build_request_payload(product=product, score=score)
        request = self._build_request(payload)
        raw_response = self._send_request(request)
        text = self._extract_response_text(raw_response)
        return validate_llm_explanation(text)


def validate_llm_explanation(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LLMAnalysisError("LLM explanation response is empty")
    return value.strip()[:MAX_REASONING_LENGTH]


def build_llm_client(settings):
    provider = (settings.LLM_PROVIDER or "").strip().casefold()
    api_key = (settings.LLM_API_KEY or "").strip()
    if provider not in {"anthropic", "claude"} or not api_key:
        return None
    return AnthropicAnalysisClient(
        api_key=api_key,
        model=settings.LLM_MODEL,
        timeout_seconds=settings.LLM_TIMEOUT_SECONDS,
    )
