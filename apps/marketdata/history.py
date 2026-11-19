"""
Séries históricas para gráficos: candles diários de UMA fonte por ativo, opcionalmente convertidos
pelo PTAX de cada data (ou do dia útil anterior). Lacunas não são interpoladas: um ponto sem PTAX
fica sem valor convertido e a cobertura informa.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from django.utils import timezone

from apps.portfolio.models import Asset

from .models import DailyBar, FxDailyRate, FxRateType

PERIODOS = {"1s": 7, "1m": 30, "6m": 182, "1a": 365, "compra": None}
RECUO_PTAX_DIAS = 7


@dataclass
class Ponto:
    data: dt.date
    close: Decimal
    convertido: Decimal | None = None
    provisorio: bool = False


@dataclass
class SerieHistorica:
    asset: Asset
    periodo: str
    fonte: str
    base_tempo: str
    moeda_nativa: str
    moeda_visao: str | None
    pontos: list[Ponto] = field(default_factory=list)
    inicio_pedido: dt.date | None = None
    avisos: list[str] = field(default_factory=list)

    @property
    def inicio_real(self) -> dt.date | None:
        return self.pontos[0].data if self.pontos else None

    @property
    def convertidos(self) -> int:
        return sum(1 for p in self.pontos if p.convertido is not None)


def fonte_principal(asset: Asset) -> str | None:
    """Fonte dos candles do ativo: a oficial do mercado quando houver candles dela; senão a mais recente."""
    from .adapters import FONTE_POR_MERCADO

    preferida = "demo" if _modo_demo() else FONTE_POR_MERCADO.get(asset.market)
    if preferida and DailyBar.objects.filter(asset=asset, source=preferida).exists():
        return preferida
    ultimo = (
        DailyBar.objects.filter(asset=asset)
        .order_by("-session_date")
        .values_list("source", flat=True)
        .first()
    )
    return ultimo


def _modo_demo() -> bool:
    from django.conf import settings

    return bool(settings.PORTAL["MODO_DEMO"])


def _ptax_por_data(datas: list[dt.date]) -> dict[dt.date, Decimal]:
    if not datas:
        return {}
    inicio = min(datas) - dt.timedelta(days=RECUO_PTAX_DIAS)
    regs = FxDailyRate.objects.filter(
        pair="USD-BRL", rate_type=FxRateType.PTAX_CLOSE, date__gte=inicio, date__lte=max(datas)
    ).order_by("date")
    por_data = {r.date: r.bid for r in regs}
    saida = {}
    for data in datas:
        for recuo in range(RECUO_PTAX_DIAS + 1):
            valor = por_data.get(data - dt.timedelta(days=recuo))
            if valor is not None:
                saida[data] = valor
                break
    return saida


def serie_historica(
    asset: Asset, periodo: str = "1m", moeda_visao: str | None = None, agora: dt.datetime | None = None
) -> SerieHistorica:
    if periodo not in PERIODOS:
        raise ValueError(f"período inválido: {periodo}")
    agora = agora or timezone.now()
    hoje = agora.date()
    fonte = fonte_principal(asset)
    serie = SerieHistorica(
        asset=asset,
        periodo=periodo,
        fonte=fonte or "",
        base_tempo="",
        moeda_nativa=asset.native_currency,
        moeda_visao=moeda_visao if moeda_visao and moeda_visao != asset.native_currency else None,
    )
    if fonte is None:
        serie.avisos.append("sem histórico carregado para este ativo")
        return serie
    dias = PERIODOS[periodo]
    if dias is None:
        primeira = asset.active_lots.order_by("trade_date").values_list("trade_date", flat=True).first()
        if asset.position_balance:
            primeira = asset.position_balance.reference_date
            serie.avisos.append("início na referência do saldo conciliado; data de aquisição desconhecida")
        if primeira is None:
            serie.avisos.append("sem compras cadastradas; período 'desde a compra' indisponível")
            return serie
        inicio = primeira
    else:
        inicio = hoje - dt.timedelta(days=dias)
    serie.inicio_pedido = inicio
    barras = list(
        DailyBar.objects.filter(asset=asset, source=fonte, session_date__gte=inicio).order_by("session_date")
    )
    if not barras:
        serie.avisos.append("sem candles no período")
        return serie
    serie.base_tempo = barras[0].time_basis
    serie.pontos = [Ponto(b.session_date, b.close, None, b.provisional) for b in barras]
    if serie.pontos[0].data > inicio:
        serie.avisos.append(
            f"histórico disponível a partir de {serie.pontos[0].data:%d/%m/%Y} (cobertura da fonte)"
        )
    if serie.moeda_visao:
        ptax = _ptax_por_data([p.data for p in serie.pontos])
        for p in serie.pontos:
            taxa = ptax.get(p.data)
            if taxa is None:
                continue
            p.convertido = (
                p.close * taxa
                if (asset.native_currency, serie.moeda_visao) == ("USD", "BRL")
                else p.close / taxa
            )
        faltam = len(serie.pontos) - serie.convertidos
        if faltam:
            serie.avisos.append(
                f"{faltam} ponto(s) sem PTAX disponível: sem valor convertido (não interpolado)"
            )
    if serie.pontos[-1].provisorio:
        serie.avisos.append("último candle é da sessão em andamento (provisório)")
    return serie


def como_json(serie: SerieHistorica) -> dict:
    return {
        "simbolo": serie.asset.symbol,
        "periodo": serie.periodo,
        "fonte": serie.fonte,
        "base_tempo": serie.base_tempo,
        "moeda_nativa": serie.moeda_nativa,
        "moeda_visao": serie.moeda_visao,
        "inicio_pedido": serie.inicio_pedido.isoformat() if serie.inicio_pedido else None,
        "inicio_real": serie.inicio_real.isoformat() if serie.inicio_real else None,
        "avisos": serie.avisos,
        "pontos": [
            {
                "data": p.data.isoformat(),
                "nativo": str(p.close),
                "convertido": None if p.convertido is None else str(p.convertido),
                "provisorio": p.provisorio,
            }
            for p in serie.pontos
        ],
    }
