from decimal import Decimal
from unittest.mock import MagicMock, patch

import requests
from django.core.cache import cache
from django.test import TestCase

from currency.services import (
    FALLBACK_RATES,
    convert,
    get_rates,
    to_inr,
)


class GetRatesTests(TestCase):
    def setUp(self):
        cache.clear()

    def _mock_response(self, rates):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"rates": rates}
        return response

    @patch("currency.services.requests.get")
    def test_live_fetch_success_is_cached_and_not_refetched(self, mock_get):
        mock_get.return_value = self._mock_response({"USD": 0.012, "EUR": 0.011})

        first = get_rates()
        second = get_rates()

        self.assertEqual(mock_get.call_count, 1)
        self.assertFalse(first["is_fallback"])
        self.assertEqual(first["rates"]["USD"], 0.012)
        self.assertEqual(first["rates"]["INR"], 1.0)
        self.assertEqual(second, first)

    @patch("currency.services.requests.get")
    def test_falls_back_to_hardcoded_rates_when_request_raises(self, mock_get):
        mock_get.side_effect = requests.RequestException("boom")

        data = get_rates()

        self.assertTrue(data["is_fallback"])
        self.assertEqual(data["rates"], FALLBACK_RATES)

    @patch("currency.services.requests.get")
    def test_falls_back_when_response_missing_rates_key(self, mock_get):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"unexpected": "shape"}
        mock_get.return_value = response

        data = get_rates()

        self.assertTrue(data["is_fallback"])
        self.assertEqual(data["rates"], FALLBACK_RATES)


class ConvertTests(TestCase):
    def setUp(self):
        self.rates = {
            "base": "INR",
            "rates": {"INR": 1.0, "USD": 0.012, "EUR": 0.011},
            "is_fallback": False,
        }

    def test_convert_applies_rate(self):
        result = convert(1000, "INR", "USD", rates=self.rates)
        self.assertEqual(result, Decimal("12.00"))

    def test_convert_pivots_through_inr_for_non_inr_source(self):
        # 100 USD -> INR -> EUR
        result = convert(100, "USD", "EUR", rates=self.rates)
        expected = (Decimal("100") / Decimal("0.012")) * Decimal("0.011")
        expected = expected.quantize(Decimal("0.01"))
        self.assertEqual(result, expected)

    def test_convert_same_currency_is_identity(self):
        result = convert(50, "USD", "USD", rates=self.rates)
        self.assertEqual(result, Decimal("50.00"))

    def test_convert_raises_for_unsupported_source_currency(self):
        with self.assertRaises(ValueError):
            convert(1000, "XYZ", "USD", rates=self.rates)

    def test_convert_raises_for_unsupported_target_currency(self):
        with self.assertRaises(ValueError):
            convert(1000, "USD", "XYZ", rates=self.rates)

    def test_to_inr_is_shortcut_for_convert_to_inr(self):
        result = to_inr(12, "USD", rates=self.rates)
        self.assertEqual(result, convert(12, "USD", "INR", rates=self.rates))


class CurrencyRatesViewTests(TestCase):
    @patch("currency.views.get_rates")
    def test_endpoint_is_public_and_returns_rates_shape(self, mock_get_rates):
        mock_get_rates.return_value = {
            "base": "INR",
            "rates": {"INR": 1.0, "USD": 0.012},
            "is_fallback": False,
        }

        response = self.client.get("/api/currency/rates/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["base"], "INR")
        self.assertIn("USD", response.data["rates"])
        self.assertFalse(response.data["is_fallback"])
