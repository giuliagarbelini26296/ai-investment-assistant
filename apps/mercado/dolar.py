"""
Dólar comercial (USD/BRL): pseudo-ativo para alertas e favoritos, e dados do painel de câmbio.

Fontes e rótulos:
- cotação atual, máxima e mínima do dia e variação do dia: câmbio intradiário (AwesomeAPI), "comercial";
- variação em 7 e 30 dias, gráfico histórico e tendência: PTAX de fechamento (BCB);
- expectativas: boletim Focus (BCB) — mediana das projeções de mercado, não previsão do portal.
As séries não são misturadas num mesmo número: cada variação informa a sua base.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from django.utils import timezone

from apps.marketdata.models import FxDailyRate, FxRate, FxRateType
from apps.portfolio.models import SIMBOLO_DOLAR, Asset, Custody, Market


def ativo_dolar() -> Asset:
    asset, _ = Asset.objects.get_or_create(
        symbol=SIMBOLO_DOLAR,
        market=Market.FX,
        defaults={
            "name": "Dólar comercial (USD/BRL)",
            "custody": Custody.NONE,
            "acompanhamento": True,
            "sector": "Câmbio",
        },
    )
    return asset


def _dec(v) -> Decimal | None:
    try:
        return Decimal(str(v)) if v not in (None, "") else None
    except ArithmeticError:
        return None


@dataclass
class PainelDolar:
    atual: Decimal | None = None
    venda: Decimal | None = None
    maxima_dia: Decimal | None = None
    minima_dia: Decimal | None = None
    variacao_dia_pct: Decimal | None = None
    horario: dt.datetime | None = None  # horário de referência informado pela fonte
    coletado_em: dt.datetime | None = None
    fonte_atual: str = ""
    ptax: Decimal | None = None
    ptax_data: dt.date | None = None
    var_7d_pct: Decimal | None = None
    var_30d_pct: Decimal | None = None
    base_7d: dt.date | None = None
    base_30d: dt.date | None = None
    mm20: Decimal | None = None
    mm60: Decimal | None = None
    tendencia: str = ""
    serie: list[tuple[dt.date, Decimal]] = field(default_factory=list)
    intradia: list[tuple[dt.datetime, Decimal]] = field(default_factory=list)
    focus: list = field(default_factory=list)  # últimos boletins por ano de referência
    focus_historico: list = field(default_factory=list)  # mediana do ano corrente nas últimas semanas
    noticias: list = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)


def _ptax_em_ou_antes(serie: list[tuple[dt.date, Decimal]], data: dt.date):
    candidato = None
    for d, v in serie:
        if d <= data:
            candidato = (d, v)
        else:
            break
    return candidato


def painel(agora: dt.datetime | None = None, dias_grafico: int = 365) -> PainelDolar:
    from .models import FocusExpectativa, NewsItem

    agora = agora or timezone.now()
    p = PainelDolar()
    intra = (
        FxRate.objects.filter(pair="USD-BRL", rate_type=FxRateType.INTRADAY, status="valida")
        .order_by("-collected_at")
        .first()
    )
    if intra is not None:
        bruto = intra.raw or {}
        p.atual, p.venda = intra.bid, intra.ask
        p.maxima_dia, p.minima_dia = _dec(bruto.get("high")), _dec(bruto.get("low"))
        p.variacao_dia_pct = _dec(bruto.get("pctChange"))
        p.horario, p.coletado_em, p.fonte_atual = intra.rate_time, intra.collected_at, intra.source
        hoje_local = timezone.localtime(agora).date()
        inicio = timezone.make_aware(dt.datetime.combine(hoje_local, dt.time.min))
        p.intradia = list(
            FxRate.objects.filter(pair="USD-BRL", rate_type=FxRateType.INTRADAY, collected_at__gte=inicio)
            .order_by("collected_at")
            .values_list("collected_at", "bid")
        )
    else:
        p.avisos.append("sem câmbio intradiário coletado (AwesomeAPI)")

    inicio_serie = agora.date() - dt.timedelta(days=max(dias_grafico, 60) + 10)
    serie = list(
        FxDailyRate.objects.filter(pair="USD-BRL", rate_type=FxRateType.PTAX_CLOSE, date__gte=inicio_serie)
        .order_by("date")
        .values_list("date", "bid")
    )
    p.serie = [(d, v) for d, v in serie if d >= agora.date() - dt.timedelta(days=dias_grafico)]
    if serie:
        p.ptax_data, p.ptax = serie[-1]
        for dias, attr_v, attr_b in ((7, "var_7d_pct", "base_7d"), (30, "var_30d_pct", "base_30d")):
            base = _ptax_em_ou_antes(serie, p.ptax_data - dt.timedelta(days=dias))
            if base:
                setattr(p, attr_b, base[0])
                setattr(p, attr_v, (p.ptax / base[1] - 1) * 100)
        valores = [v for _, v in serie]
        if len(valores) >= 20:
            p.mm20 = sum(valores[-20:]) / 20
        if len(valores) >= 60:
            p.mm60 = sum(valores[-60:]) / 60
        if p.mm20 and p.mm60:
            dif = (p.mm20 / p.mm60 - 1) * 100
            if dif > Decimal("0.5"):
                p.tendencia = "alta"
            elif dif < Decimal("-0.5"):
                p.tendencia = "baixa"
            else:
                p.tendencia = "lateral"
    else:
        p.avisos.append("sem histórico PTAX carregado")

    ultima = FocusExpectativa.objects.filter(indicador="Câmbio", base_calculo=0).order_by("-data").first()
    if ultima is not None:
        p.focus = list(
            FocusExpectativa.objects.filter(indicador="Câmbio", base_calculo=0, data=ultima.data).order_by(
                "data_referencia"
            )
        )
        ano = str(agora.year)
        p.focus_historico = list(
            FocusExpectativa.objects.filter(indicador="Câmbio", base_calculo=0, data_referencia=ano).order_by(
                "-data"
            )[:16]
        )[::-1]
    else:
        from .estado import estado_coleta
        p.avisos.append("Focus: " + estado_coleta("FOCUS")["mensagem"])
    p.noticias = list(
        NewsItem.objects.filter(categoria=NewsItem.Categoria.CAMBIO).order_by("-published_at")[:8]
    )
    return p
