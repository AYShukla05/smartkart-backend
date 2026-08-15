import logging
from decimal import Decimal, ROUND_HALF_UP

import requests
from django.core.cache import cache

logger = logging.getLogger(__name__)

FRANKFURTER_URL = "https://api.frankfurter.dev/v1/latest"
CACHE_KEY = "currency:rates:INR"
CACHE_TTL = 60 * 60 * 24        # 24h - Frankfurter (ECB) updates ~daily
FALLBACK_CACHE_TTL = 60 * 5     # re-attempt soon after an outage

SUPPORTED_CURRENCIES = ["INR", "USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CNY"]
CURRENCY_CHOICES = [(code, code) for code in SUPPORTED_CURRENCIES]

FALLBACK_RATES = {  # 1 INR = X; used only if the live fetch fails
    "INR": 1.0, "USD": 0.012, "EUR": 0.011, "GBP": 0.0095,
    "JPY": 1.8, "AUD": 0.018, "CAD": 0.016, "CNY": 0.086,
}


def _fetch_live_rates():
    try:
        response = requests.get(
            FRANKFURTER_URL,
            params={"base": "INR", "symbols": ",".join(SUPPORTED_CURRENCIES)},
            timeout=15,
        )
        response.raise_for_status()
        rates = dict(response.json()["rates"])
        rates["INR"] = 1.0
        return {"base": "INR", "rates": rates, "is_fallback": False}
    except (requests.RequestException, KeyError, ValueError):
        logger.error("Frankfurter rate fetch failed", exc_info=True)
        return None


def get_rates():
    """INR-pivot conversion rates, cached 24h. Never raises."""
    cached = cache.get(CACHE_KEY)
    if cached is not None:
        return cached

    data = _fetch_live_rates()
    if data is None:
        data = {"base": "INR", "rates": dict(FALLBACK_RATES), "is_fallback": True}
        cache.set(CACHE_KEY, data, timeout=FALLBACK_CACHE_TTL)
        return data

    cache.set(CACHE_KEY, data, timeout=CACHE_TTL)
    return data


def convert(amount, source_currency, target_currency, rates=None):
    """Decimal-precise conversion, pivoting through INR. Money math stays
    Decimal throughout (not float) since this feeds Order.total_amount."""
    rates = rates or get_rates()
    src_rate = rates["rates"].get(source_currency.upper())
    tgt_rate = rates["rates"].get(target_currency.upper())
    if src_rate is None or tgt_rate is None:
        bad = source_currency if src_rate is None else target_currency
        raise ValueError(f"Unsupported currency: {bad}")

    amount = Decimal(str(amount))
    amount_in_inr = amount / Decimal(str(src_rate))
    converted = amount_in_inr * Decimal(str(tgt_rate))
    return converted.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def to_inr(amount, source_currency, rates=None):
    return convert(amount, source_currency, "INR", rates=rates)
