from decimal import Decimal

from apps.ui.templatetags.formatos import moeda, normalize_decimal, pct


def test_moeda_pt_br():
    assert moeda(Decimal("1234.5"), "USD") == "US$ 1.234,50"
    assert moeda(Decimal("-0.015"), "BRL") == "-R$ 0,02"
    assert moeda(None, "USD") == "indisponível"


def test_pct():
    assert pct(Decimal("9.0909")) == "+9,09%"
    assert pct(Decimal("-10")) == "-10,00%"
    assert pct(None) == "indisponível"


def test_normalize_decimal():
    assert normalize_decimal(Decimal("10.0000000000")) == "10"
    assert normalize_decimal(Decimal("0.0123456789")) == "0,0123456789"
    assert normalize_decimal(Decimal("1234567.5")) == "1.234.567,5"
    assert normalize_decimal(None) == "—"
