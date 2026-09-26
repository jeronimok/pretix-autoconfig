from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from pretix_autoconfig.signals_fee import (
    _calculate_autoconfig_fee,
    get_fee_min,
    get_fee_percent,
)


class TestGetFeePercent:
    def test_returns_none_when_unset(self, monkeypatch):
        monkeypatch.delenv("PRETIX_AUTOCONFIG_FEE_PERCENT", raising=False)
        with patch("pretix.settings.config.get", return_value=None):
            assert get_fee_percent() is None

    def test_reads_from_env(self, monkeypatch):
        monkeypatch.setenv("PRETIX_AUTOCONFIG_FEE_PERCENT", "2.9")
        assert get_fee_percent() == Decimal("2.9")

    def test_invalid_value_returns_none(self, monkeypatch):
        monkeypatch.setenv("PRETIX_AUTOCONFIG_FEE_PERCENT", "not-a-number")
        assert get_fee_percent() is None


class TestGetFeeMin:
    def test_returns_none_when_unset(self, monkeypatch):
        monkeypatch.delenv("PRETIX_AUTOCONFIG_FEE_MIN", raising=False)
        with patch("pretix.settings.config.get", return_value=None):
            assert get_fee_min() is None

    def test_reads_from_env(self, monkeypatch):
        monkeypatch.setenv("PRETIX_AUTOCONFIG_FEE_MIN", "0.60")
        assert get_fee_min() == Decimal("0.60")

    def test_invalid_value_returns_none(self, monkeypatch):
        monkeypatch.setenv("PRETIX_AUTOCONFIG_FEE_MIN", "not-a-number")
        assert get_fee_min() is None


# ----------------------------------------------------------------------
# _calculate_autoconfig_fee — unit tests against a stub event.
#
# We avoid the Pretix DB layer entirely: TaxRule.zero() builds an
# in-memory rule and OrderFee is constructed (not saved). The event is
# a SimpleNamespace with the two attributes the function touches:
# `currency` and `cached_default_tax_rule`.
# ----------------------------------------------------------------------


@pytest.fixture
def event_eur():
    return SimpleNamespace(currency="EUR", cached_default_tax_rule=None)


def _set_fee_env(monkeypatch, percent=None, fee_min=None):
    for var in (
        "PRETIX_AUTOCONFIG_FEE_PERCENT",
        "PRETIX_AUTOCONFIG_FEE_MIN",
    ):
        monkeypatch.delenv(var, raising=False)
    if percent is not None:
        monkeypatch.setenv("PRETIX_AUTOCONFIG_FEE_PERCENT", str(percent))
    if fee_min is not None:
        monkeypatch.setenv("PRETIX_AUTOCONFIG_FEE_MIN", str(fee_min))


class TestCalculateAutoconfigFee:
    def test_zero_total_returns_empty(self, event_eur, monkeypatch):
        _set_fee_env(monkeypatch, percent="2.9", fee_min="0.60")
        assert _calculate_autoconfig_fee(event_eur, Decimal("0")) == []

    def test_negative_total_returns_empty(self, event_eur, monkeypatch):
        _set_fee_env(monkeypatch, percent="2.9", fee_min="0.60")
        assert _calculate_autoconfig_fee(event_eur, Decimal("-5.00")) == []

    def test_no_config_returns_empty(self, event_eur, monkeypatch):
        _set_fee_env(monkeypatch)
        with patch("pretix.settings.config.get", return_value=None):
            assert _calculate_autoconfig_fee(event_eur, Decimal("100.00")) == []

    def test_percent_only_no_positions(self, event_eur, monkeypatch):
        _set_fee_env(monkeypatch, percent="2.9")
        fees = _calculate_autoconfig_fee(event_eur, Decimal("100.00"))
        assert len(fees) == 1
        # 100 * 2.9% = 2.90 (rounded to currency precision)
        assert fees[0].value == Decimal("2.90")

    def test_percent_below_min_clamps_to_min_no_positions(self, event_eur, monkeypatch):
        _set_fee_env(monkeypatch, percent="2.9", fee_min="0.60")
        # 10.00 * 2.9% = 0.29 → clamped to 0.60
        fees = _calculate_autoconfig_fee(event_eur, Decimal("10.00"))
        assert fees[0].value == Decimal("0.60")

    def test_min_only_no_positions(self, event_eur, monkeypatch):
        _set_fee_env(monkeypatch, fee_min="0.60")
        fees = _calculate_autoconfig_fee(event_eur, Decimal("100.00"))
        # Percent unset → percent treated as 0; total fee starts at 0 then min clamps to 0.60.
        assert fees[0].value == Decimal("0.60")

    def test_positions_each_get_min_clamp(self, event_eur, monkeypatch):
        _set_fee_env(monkeypatch, percent="2.9", fee_min="0.60")
        # Two cheap positions: each ticket gets 0.60 minimum, summing to 1.20.
        positions = [
            SimpleNamespace(gross_price_before_rounding=Decimal("5.00")),
            SimpleNamespace(gross_price_before_rounding=Decimal("5.00")),
        ]
        fees = _calculate_autoconfig_fee(event_eur, Decimal("10.00"), positions=positions)
        assert fees[0].value == Decimal("1.20")

    def test_positions_above_min_use_percent(self, event_eur, monkeypatch):
        _set_fee_env(monkeypatch, percent="2.9", fee_min="0.60")
        # 30.00 * 2.9% = 0.87 → above the 0.60 min, so per-ticket fee = 0.87.
        positions = [SimpleNamespace(gross_price_before_rounding=Decimal("30.00"))]
        fees = _calculate_autoconfig_fee(event_eur, Decimal("30.00"), positions=positions)
        assert fees[0].value == Decimal("0.87")

    def test_positions_zero_priced_skipped(self, event_eur, monkeypatch):
        _set_fee_env(monkeypatch, percent="2.9", fee_min="0.60")
        positions = [
            SimpleNamespace(gross_price_before_rounding=Decimal("0.00")),
            SimpleNamespace(gross_price_before_rounding=Decimal("30.00")),
        ]
        fees = _calculate_autoconfig_fee(event_eur, Decimal("30.00"), positions=positions)
        # Only the paid position contributes; 30 * 2.9% = 0.87.
        assert fees[0].value == Decimal("0.87")

    def test_fee_is_service_type(self, event_eur, monkeypatch):
        from pretix.base.models.orders import OrderFee

        _set_fee_env(monkeypatch, percent="2.9")
        fees = _calculate_autoconfig_fee(event_eur, Decimal("100.00"))
        assert fees[0].fee_type == OrderFee.FEE_TYPE_SERVICE
