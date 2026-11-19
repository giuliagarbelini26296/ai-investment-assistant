"""Sinaliza cadastros que merecem revisão, sem alterar compras ou estimar valores."""
import datetime as dt
from decimal import Decimal

from apps.marketdata.models import DailyBar

FAIXA_CAMBIO = (1, 15)
DIVERGENCIA_MAXIMA = 3


def conferir(asset, preco, data, cambio):
    problemas = {}
    if asset.native_currency == "USD" and cambio is not None and not (
        FAIXA_CAMBIO[0] <= cambio <= FAIXA_CAMBIO[1]
    ):
        problemas["fx_rate"] = (
            f"Câmbio USD/BRL {cambio.normalize():f} fora da faixa de conferência (1 a 15). "
            "Confira a digitação; use vírgula para decimais, como 5,42."
        )
    if preco is None or data is None:
        return problemas
    barra = DailyBar.objects.filter(
        asset=asset, session_date__gte=data - dt.timedelta(days=4), session_date__lte=data
    ).order_by("-session_date").first()
    if barra and barra.close > 0:
        razao = preco / barra.close
        if razao > DIVERGENCIA_MAXIMA or razao < Decimal(1) / DIVERGENCIA_MAXIMA:
            problemas["unit_price"] = (
                f"Preço {preco.normalize():f} muito diferente do fechamento de "
                f"{barra.session_date:%d/%m/%Y} ({barra.close.normalize():f}, fonte {barra.source}). "
                "Confira a digitação e possíveis eventos do ativo. O ponto separa milhares: 76.590 = 76590."
            )
    return problemas


def compras_para_conferir(lotes=None):
    from .models import Lot
    if lotes is None:
        lotes = Lot.objects.filter(cancelado_em__isnull=True, asset__is_active=True).select_related("asset")
    saida = []
    for lot in lotes:
        if lot.is_cancelled:
            continue
        problemas = conferir(lot.asset, lot.unit_price, lot.trade_date, lot.fx_rate)
        if problemas:
            saida.append({"lot": lot, "problemas": list(problemas.values())})
    return saida
