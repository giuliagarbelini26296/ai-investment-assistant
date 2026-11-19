"""
Abas Ações EUA, Ações Brasil e Comparar mercados.

Conversão de moeda (sempre declarada na tela):
- preço atual convertido pelo câmbio em uso no portal (o mesmo da carteira: comercial ou PTAX, com rótulo);
- retorno de um período em outra moeda = (1 + retorno nativo) × (câmbio no fim / câmbio no início) − 1,
  com o câmbio do início = PTAX da data-base (ou do dia útil anterior). Efeito cambial = a diferença;
- proventos (dividendos, JCP) entram no "retorno com proventos" como soma simples dos valores brutos por ação
  com data-com no período, convertidos pelo PTAX da data-com — sem reinvestimento e sem descontar impostos;
- volatilidade e queda máxima na moeda comum usam a série diária convertida pelo PTAX de cada data.
Sem PTAX ou sem candles suficientes, o número fica "—" (nada é interpolado).
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from decimal import Decimal

from django.utils import timezone

from apps.marketdata import fxservice
from apps.marketdata.history import _ptax_por_data
from apps.portfolio.models import Asset, Market

from . import analise
from . import indicadores as ind_mod
from .models import FocusExpectativa, MacroSerie, Provento

EUA = (Market.NYSE, Market.NASDAQ)
BRASIL = (Market.B3,)

INICIAIS_EUA = (
    ("GOOGL", "Alphabet Inc. (Classe A)", Market.NASDAQ, "Comunicação e internet"),
    ("MSFT", "Microsoft Corporation", Market.NASDAQ, "Tecnologia"),
    ("NVDA", "NVIDIA Corporation", Market.NASDAQ, "Semicondutores"),
    ("AAPL", "Apple Inc.", Market.NASDAQ, "Tecnologia"),
    ("AMZN", "Amazon.com Inc.", Market.NASDAQ, "Consumo e nuvem"),
    ("META", "Meta Platforms Inc.", Market.NASDAQ, "Comunicação e internet"),
    ("TSLA", "Tesla Inc.", Market.NASDAQ, "Automotivo"),
    ("PLTR", "Palantir Technologies Inc.", Market.NASDAQ, "Tecnologia"),
)
INICIAIS_BRASIL = (
    ("PETR4", "Petrobras PN", Market.B3, "Petróleo e gás"),
    ("VALE3", "Vale ON", Market.B3, "Mineração"),
    ("ITUB4", "Itaú Unibanco PN", Market.B3, "Bancos"),
    ("BBAS3", "Banco do Brasil ON", Market.B3, "Bancos"),
    ("WEGE3", "WEG ON", Market.B3, "Bens industriais"),
    ("B3SA3", "B3 ON", Market.B3, "Serviços financeiros"),
    ("RENT3", "Localiza ON", Market.B3, "Locação de veículos"),
    ("ABEV3", "Ambev ON", Market.B3, "Bebidas"),
)
SEM_TOKEN_BRAPI = ("PETR4", "VALE3", "ITUB4", "MGLU3")


def semear(iniciais) -> list[str]:
    from .universo import garantir_ativo

    criados = []
    for simbolo, nome, mercado, setor in iniciais:
        _, novo = garantir_ativo(simbolo, mercado, nome, setor)
        if novo:
            criados.append(simbolo)
    analise.invalidar_cache()
    return criados


def leituras_do_mercado(mercados) -> list[analise.LeituraAtivo]:
    return [x for x in analise.leituras() if x.asset.market in mercados and not x.asset.is_etf]


# --- câmbio --------------------------------------------------------------------------------------


@dataclass
class Cambio:
    """Taxa USD→BRL atual (a mesma da carteira) e PTAX por data para as bases dos períodos."""

    atual: Decimal | None
    descricao: str = ""
    rotulo: str = ""
    ptax: dict = field(default_factory=dict)

    @classmethod
    def carregar(cls, datas: list[dt.date] | None = None) -> Cambio:
        c = fxservice.cambio_atual()
        cambio = cls(
            atual=c.valor if c else None, descricao=c.descricao if c else "", rotulo=c.rotulo if c else ""
        )
        if datas:
            cambio.ptax = _ptax_por_data(sorted(set(datas)))
        return cambio

    def fator(self, de: str, para: str, data: dt.date | None = None) -> Decimal | None:
        """Multiplicador de `de` para `para` (USDT = USD). data=None → câmbio atual; senão PTAX da data."""
        de, para = ("USD" if de == "USDT" else de), ("USD" if para == "USDT" else para)
        if de == para:
            return Decimal(1)
        taxa = self.atual if data is None else self.ptax.get(data)
        if not taxa:
            return None
        return taxa if (de, para) == ("USD", "BRL") else Decimal(1) / taxa


def converter(valor, de: str, para: str, cambio: Cambio, data: dt.date | None = None) -> Decimal | None:
    if valor is None:
        return None
    f = cambio.fator(de, para, data)
    return None if f is None else Decimal(str(valor)) * f


@dataclass
class RetornoMoeda:
    nativo: float | None
    moeda: float | None
    efeito_cambial: float | None
    base_data: dt.date | None


def retorno_em_moeda(x: analise.LeituraAtivo, periodo: str, moeda: str, cambio: Cambio) -> RetornoMoeda:
    nativa = x.moeda
    p = x.ind.periodos.get(periodo) if x.ind else None
    if p is None and periodo == "1d" and x.variacao_dia is not None:
        # ativo recém-incluído, sem candles: variação do dia pela cotação (fechamento anterior da fonte)
        return RetornoMoeda(x.variacao_dia, x.variacao_dia, None, None)
    if p is None or p.variacao_pct is None:
        return RetornoMoeda(None, None, None, None)
    if nativa == moeda or (nativa == "USDT" and moeda == "USD"):
        return RetornoMoeda(p.variacao_pct, p.variacao_pct, 0.0, p.base_data)
    if periodo == "1d":
        # "1 dia" fica na moeda do ativo (a tela avisa "variação em US$"): o câmbio do dia não entra
        return RetornoMoeda(p.variacao_pct, p.variacao_pct, None, p.base_data)
    if p.base_data is None:
        return RetornoMoeda(p.variacao_pct, None, None, p.base_data)
    f_ini = cambio.fator(nativa, moeda, p.base_data)
    f_fim = cambio.fator(nativa, moeda)
    if not f_ini or not f_fim:
        return RetornoMoeda(p.variacao_pct, None, None, p.base_data)
    rm = (1 + p.variacao_pct / 100) * float(f_fim / f_ini) * 100 - 100
    return RetornoMoeda(p.variacao_pct, rm, rm - p.variacao_pct, p.base_data)


# --- tela de mercado (EUA / Brasil) ---------------------------------------------------------------


@dataclass
class LinhaMercado:
    x: analise.LeituraAtivo
    preco_moeda: Decimal | None
    ret: RetornoMoeda
    moeda: str


def linhas(lista, periodo: str, moeda: str, cambio: Cambio) -> list[LinhaMercado]:
    saida = []
    for x in lista:
        preco = converter(x.preco, x.moeda, moeda, cambio) if x.preco is not None else None
        saida.append(LinhaMercado(x, preco, retorno_em_moeda(x, periodo, moeda, cambio), moeda))
    return saida


def ranking(linhas_: list[LinhaMercado]) -> tuple[list[LinhaMercado], list[LinhaMercado], list[LinhaMercado]]:
    com = [li for li in linhas_ if li.ret.moeda is not None and li.x.ranking_eligible]
    altas = sorted([li for li in com if li.ret.moeda > 0], key=lambda li: -li.ret.moeda)
    quedas = sorted([li for li in com if li.ret.moeda < 0], key=lambda li: li.ret.moeda)
    sem = [li for li in linhas_ if li.ret.moeda is None or not li.x.ranking_eligible]
    return altas, quedas, sem


def perto_da_minima(lista, limite_pct: float = 10.0, completa=True):
    saida = []
    for x in lista:
        i = x.ind
        if (i and i.janela_52s_completa == completa
            and i.dist_minima_52s_pct is not None and i.dist_minima_52s_pct <= limite_pct):
            saida.append(x)
    return sorted(saida, key=lambda x: x.ind.dist_minima_52s_pct)


def _mediana(valores):
    v = sorted(float(a) for a in valores if a is not None)
    if not v:
        return None
    m = len(v) // 2
    return v[m] if len(v) % 2 else (v[m - 1] + v[m]) / 2


METRICAS_SETOR = (
    ("pe", "P/L", "menor"),
    ("price_to_book", "P/VP", "menor"),
    ("ev_ebitda", "EV/EBITDA", "menor"),
    ("roe", "ROE", "maior"),
    ("revenue_growth", "Cresc. receita", "maior"),
    ("net_debt_ebitda", "Dív. líq./EBITDA", "menor"),
)


def _valor_metrica(x: analise.LeituraAtivo, campo: str):
    if campo == "dy":
        return x.dy[0]
    if campo == "var12":
        return x.variacao("12m")
    f = x.fundamentos
    return None if f is None else getattr(f, campo)


@dataclass
class GrupoSetor:
    setor: str
    membros: list[analise.LeituraAtivo]
    medianas: dict


def por_setor(lista) -> list[GrupoSetor]:
    grupos: dict[str, list] = {}
    for x in lista:
        grupos.setdefault(x.asset.sector or "Sem setor", []).append(x)
    saida = []
    for setor, membros in sorted(grupos.items(), key=lambda t: (-len(t[1]), t[0])):
        medianas = {c: _mediana(_valor_metrica(x, c) for x in membros) for c, _, _ in METRICAS_SETOR}
        medianas["dy"] = _mediana(x.dy[0] for x in membros)
        medianas["var12"] = _mediana(x.variacao("12m") for x in membros)
        saida.append(GrupoSetor(setor, sorted(membros, key=lambda x: x.asset.symbol), medianas))
    return saida


def calendario(lista, dias: int = 45):
    hoje = timezone.localdate()
    from .models import EarningsEvent

    resultados = list(
        EarningsEvent.objects.filter(
            asset__in=[x.asset for x in lista], date__gte=hoje, date__lte=hoje + dt.timedelta(days=dias)
        )
        .select_related("asset")
        .order_by("date")
    )
    proventos = list(
        Provento.objects.filter(asset__in=[x.asset for x in lista])
        .filter(data_pagamento__gte=hoje)
        .select_related("asset")
        .order_by("data_pagamento")[:40]
    ) + list(
        Provento.objects.filter(
            asset__in=[x.asset for x in lista], data_pagamento__isnull=True, data_com__gte=hoje
        )
        .select_related("asset")
        .order_by("data_com")[:20]
    )
    return resultados, proventos


def noticias(lista, limite: int = 15):
    from .models import NewsItem

    return list(
        NewsItem.objects.filter(asset__in=[x.asset for x in lista])
        .select_related("asset")
        .order_by("-published_at")[:limite]
    )


# --- Brasil: macro e sensibilidade ao dólar --------------------------------------------------------


@dataclass
class Macro:
    selic: MacroSerie | None = None
    ipca12: MacroSerie | None = None
    ipca_mes: MacroSerie | None = None
    juro_real: float | None = None
    focus: dict = field(default_factory=dict)  # indicador → [FocusExpectativa por ano]
    focus_data: dt.date | None = None
    dolar: object = None


def macro() -> Macro:
    from .dolar import painel

    m = Macro()
    m.selic = MacroSerie.objects.filter(codigo=432).order_by("-data").first()
    m.ipca12 = MacroSerie.objects.filter(codigo=13522).order_by("-data").first()
    m.ipca_mes = MacroSerie.objects.filter(codigo=433).order_by("-data").first()
    if m.selic and m.ipca12:
        m.juro_real = ((1 + float(m.selic.valor) / 100) / (1 + float(m.ipca12.valor) / 100) - 1) * 100
    ultima = FocusExpectativa.objects.filter(base_calculo=0).order_by("-data").first()
    if ultima:
        m.focus_data = ultima.data
        ano = timezone.localdate().year
        for f in FocusExpectativa.objects.filter(
            base_calculo=0, data=ultima.data, data_referencia__in=[str(ano), str(ano + 1)]
        ).order_by("data_referencia"):
            m.focus.setdefault(f.indicador, []).append(f)
    m.dolar = painel(dias_grafico=30)
    return m


@dataclass
class Sensibilidade:
    beta: float | None
    correlacao: float | None
    n: int


def sensibilidade_dolar(asset: Asset, minimo: int = 40) -> Sensibilidade:
    """β e correlação dos retornos diários da ação com os do PTAX nas mesmas datas (até 250 pregões)."""
    barras, _, _ = analise._barras(asset)
    completas = [b for b in barras if not b.provisorio][-251:]
    if len(completas) < minimo + 1:
        return Sensibilidade(None, None, max(0, len(completas) - 1))
    datas = [b.data for b in completas]
    from apps.marketdata.models import FxDailyRate, FxRateType

    ptax = dict(
        FxDailyRate.objects.filter(
            pair="USD-BRL", rate_type=FxRateType.PTAX_CLOSE, date__in=datas
        ).values_list("date", "bid")
    )
    pares = []
    for a, b in zip(completas[:-1], completas[1:], strict=False):
        fa, fb = ptax.get(a.data), ptax.get(b.data)
        if fa and fb and a.close > 0:
            pares.append((b.close / a.close - 1, float(fb / fa) - 1))
    if len(pares) < minimo:
        return Sensibilidade(None, None, len(pares))
    xs = [p[1] for p in pares]
    ys = [p[0] for p in pares]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    cov = sum((x - mx) * (y - my) for x, y in pares) / (len(pares) - 1)
    vx = sum((x - mx) ** 2 for x in xs) / (len(xs) - 1)
    vy = sum((y - my) ** 2 for y in ys) / (len(ys) - 1)
    if vx <= 0 or vy <= 0:
        return Sensibilidade(None, None, len(pares))
    return Sensibilidade(cov / vx, cov / math.sqrt(vx * vy), len(pares))


# --- Comparar mercados -------------------------------------------------------------------------------


@dataclass
class LinhaComparacao:
    x: analise.LeituraAtivo
    moeda: str
    retorno_nativo: float | None = None
    retorno_moeda: float | None = None
    efeito_cambial: float | None = None
    proventos_moeda: Decimal | None = None  # soma por ação convertida
    retorno_com_proventos: float | None = None
    proventos_situacao: str = ""
    volatilidade: float | None = None
    queda_maxima: float | None = None
    base_data: dt.date | None = None
    serie: list[tuple[dt.date, float]] = field(default_factory=list)  # base 100 na moeda comum
    serie_nativa: list[tuple[dt.date, float]] = field(default_factory=list)  # base 100 na moeda nativa
    avisos: list[str] = field(default_factory=list)


def comparar(
    ativos: list[Asset], moeda: str, periodo: str, agora: dt.datetime | None = None
) -> list[LinhaComparacao]:
    agora = agora or timezone.now()
    hoje = timezone.localdate(agora)
    dias = ind_mod.PERIODOS_DIAS.get(periodo, 365)
    por_id = {x.asset.pk: x for x in analise.leituras()}
    saida = []
    for asset in ativos:
        x = por_id.get(asset.pk) or analise.leitura_do_ativo(asset, agora)
        li = LinhaComparacao(x=x, moeda=moeda)
        barras, _, _ = analise._barras(asset)
        if not barras or x.preco is None:
            li.avisos.append("sem histórico ou sem cotação")
            saida.append(li)
            continue
        inicio = hoje - dt.timedelta(days=dias)
        base = ind_mod.fechamento_base(barras, inicio)
        if base is None or barras[0].data > inicio:
            li.avisos.append(f"histórico começa em {barras[0].data:%d/%m/%Y} (cobertura da fonte/plano)")
            base = barras[0]
        janela = [b for b in barras if base.data <= b.data < hoje]  # hoje entra pelo preço atual
        datas = [b.data for b in janela]
        cambio = Cambio.carregar(datas)
        nativa = x.moeda
        preco = float(x.preco)
        li.base_data = base.data
        li.retorno_nativo = (preco / base.close - 1) * 100
        f_ini = cambio.fator(nativa, moeda, base.data)
        f_fim = cambio.fator(nativa, moeda)
        if f_ini and f_fim:
            li.retorno_moeda = (preco * float(f_fim)) / (base.close * float(f_ini)) * 100 - 100
            mesma = nativa == moeda or (nativa == "USDT" and moeda == "USD")
            li.efeito_cambial = None if mesma else li.retorno_moeda - li.retorno_nativo
        else:
            li.avisos.append("sem câmbio para converter (PTAX da data-base ou câmbio atual)")
        # proventos no período
        provs = list(
            Provento.objects.filter(asset=asset, data_com__gt=base.data, data_com__lte=hoje).order_by(
                "data_com"
            )
        )
        if provs:
            datas_p = [p.data_com for p in provs]
            cambio_p = Cambio.carregar(datas_p)
            total = Decimal(0)
            ok = True
            for p in provs:
                f = cambio_p.fator(p.moeda, moeda, p.data_com)
                if f is None:
                    ok = False
                    break
                total += p.valor * f
            if ok and f_ini and f_fim:
                li.proventos_moeda = total
                li.retorno_com_proventos = (preco * float(f_fim) + float(total)) / (
                    base.close * float(f_ini)
                ) * 100 - 100
                li.proventos_situacao = f"{len(provs)} provento(s) ({provs[0].source})"
            else:
                li.proventos_situacao = "proventos sem câmbio para converter"
        else:
            tem_fonte = Provento.objects.filter(asset=asset).exists()
            li.proventos_situacao = "nenhum no período" if tem_fonte else "sem dados de proventos"
        # série convertida (base 100), volatilidade e queda máxima
        pontos, nativos = [], []
        for b in janela:
            f = cambio.fator(nativa, moeda, b.data)
            nativos.append((b.data, b.close))
            if f is not None:
                pontos.append((b.data, b.close * float(f)))
        if f_fim:
            pontos.append((hoje, preco * float(f_fim)))
        nativos.append((hoje, preco))
        if len(pontos) >= 2:
            v0 = pontos[0][1]
            li.serie = [(d, v / v0 * 100) for d, v in pontos]
            retornos = [
                math.log(b / a)
                for (_, a), (_, b) in zip(pontos[:-1], pontos[1:], strict=False)
                if a > 0 and b > 0
            ]
            if len(retornos) >= 10:
                media = sum(retornos) / len(retornos)
                var = sum((r - media) ** 2 for r in retornos) / (len(retornos) - 1)
                anual = 365 if asset.market == Market.CRYPTO else 252
                li.volatilidade = math.sqrt(var) * math.sqrt(anual) * 100
            pico, queda = pontos[0][1], 0.0
            for _, v in pontos:
                pico = max(pico, v)
                queda = min(queda, (v / pico - 1) * 100)
            li.queda_maxima = queda
        n0 = nativos[0][1]
        li.serie_nativa = [(d, v / n0 * 100) for d, v in nativos]
        saida.append(li)
    return saida


def series_json(linhas_: list[LinhaComparacao], nativa: bool = False) -> list[dict]:
    return [
        {
            "simbolo": li.x.asset.symbol,
            "pontos": [
                {"data": d.isoformat(), "valor": round(v, 4)}
                for d, v in (li.serie_nativa if nativa else li.serie)
            ],
        }
        for li in linhas_
    ]
