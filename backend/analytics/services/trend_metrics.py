from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


TWO_DECIMAL_PLACES = Decimal("0.01")


class TrendMetricsError(ValueError):
    """Trend metrics cannot be calculated from the supplied series."""


@dataclass(frozen=True, slots=True)
class TrendMetrics:
    series: list[int]
    current_interest: int
    average_interest: int
    growth_percent: Decimal


def calculate_trend_metrics(series: list[int]) -> TrendMetrics:
    if not series:
        raise TrendMetricsError("Trend interest series is empty")

    baseline = next((value for value in series if value != 0), None)
    if baseline is None:
        growth_percent = Decimal("0.00")
    else:
        growth_percent = (
            (Decimal(series[-1]) - Decimal(baseline))
            / Decimal(baseline)
            * Decimal("100")
        ).quantize(TWO_DECIMAL_PLACES, rounding=ROUND_HALF_UP)

    return TrendMetrics(
        series=list(series),
        current_interest=series[-1],
        average_interest=round(sum(series) / len(series)),
        growth_percent=growth_percent,
    )
