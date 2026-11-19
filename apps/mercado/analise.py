"""
Leituras de mercado montadas a partir do banco: indicadores, score, rankings, Top 10, análise dos
favoritos e resumo executivo.

Cada afirmação levada à tela é um `Item` com tipo explícito:
  fato      — dado observado (preço, variação, resultado divulgado), com fonte e horário;
  consenso  — opinião agregada de analistas (contagem de recomendações), com fonte e data;
  projecao  — expectativa de terceiros (preço-alvo, Focus) ou cálculo do portal sobre ela (potencial);
  risco     — regra do portal que aponta o que pode impedir a valorização.
O portal não "acha" nada: cada frase vem de uma regra descrita em docs/mercado.md.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from django.core.cache import cache
from django.utils import timezone

from apps.marketdata import freshness
from apps.marketdata.history import fonte_principal
from apps.marketdata.models import DailyBar
from apps.portfolio.models import Asset, Market

from . import indicadores as ind_mod
from . import score as score_mod
from .models import AnalystSnapshot, EarningsEvent, Favorito, Fundamentos, NewsItem, PriceTarget, Provento
from .score import _num

CACHE_S = 60
CHAVE_CACHE = "mercado:leituras:v1"
JANELA_NOTICIAS = dt.timedelta(hours=48)


@dataclass
class Item:
    tipo: str  # fato | consenso | projecao | risco
    texto: str
    fonte: str = ""
    quando: dt.datetime | dt.date | None = None

    @property
    def rotulo(self) -> str:
        return {"fato": "Fato", "consenso": "Consenso", "projecao": "Projeção", "risco": "Risco"}[self.tipo]


CAMPOS_FUNDAMENTOS = (
    "sector",
    "industry",
    "pe",
    "forward_pe",
    "profit_margin",
    "roe",
    "market_cap",
    "dividend_yield",
    "latest_quarter",
    "price_to_book",
    "ev_ebitda",
    "revenue_growth",
    "net_debt_ebitda",
    "net_debt_calculado",
    "recommendation_key",
    "recommendation_mean",
    "analyst_opinions",
)


@dataclass
class FundamentosVista:
    """Fundamentos mesclados das fontes: para cada campo, o valor mais recente disponível (com a fonte)."""

    fontes: dict = field(default_factory=dict)  # campo → fonte
    collected_at: dt.datetime | None = None
    sector: str = ""
    industry: str = ""
    pe: Decimal | None = None
    forward_pe: Decimal | None = None
    profit_margin: Decimal | None = None
    roe: Decimal | None = None
    market_cap: Decimal | None = None
    dividend_yield: Decimal | None = None
    latest_quarter: dt.date | None = None
    price_to_book: Decimal | None = None
    ev_ebitda: Decimal | None = None
    revenue_growth: Decimal | None = None
    net_debt_ebitda: Decimal | None = None
    net_debt_calculado: bool = False
    recommendation_key: str = ""
    recommendation_mean: Decimal | None = None
    analyst_opinions: int | None = None

    @property
    def source(self) -> str:
        return ", ".join(sorted(set(self.fontes.values())))


def fundamentos_mesclados(asset: Asset) -> FundamentosVista | None:
    regs = list(Fundamentos.objects.filter(asset=asset).order_by("-collected_at"))
    if not regs:
        return None
    v = FundamentosVista(collected_at=regs[0].collected_at)
    for campo in CAMPOS_FUNDAMENTOS:
        if campo == "net_debt_calculado":
            continue
        for r in regs:
            valor = getattr(r, campo)
            if valor not in (None, ""):
                setattr(v, campo, valor)
                v.fontes[campo] = r.source
                if campo == "net_debt_ebitda":
                    v.net_debt_calculado = r.net_debt_calculado
                break
    return v


@dataclass
class LeituraAtivo:
    asset: Asset
    atualidade: freshness.Atualidade
    preco: Decimal | None
    moeda: str
    cotado_em: dt.datetime | None
    fonte_preco: str
    fonte_barras: str = ""
    barras_coletadas_em: dt.datetime | None = None
    ind: ind_mod.Indicadores | None = None
    ext: score_mod.DadosExternos | None = None
    score: score_mod.Score | None = None
    analistas: AnalystSnapshot | None = None
    analistas_anterior: AnalystSnapshot | None = None
    mudanca_recomendacao_pp: float | None = None
    preco_alvo: PriceTarget | None = None
    fundamentos: FundamentosVista | None = None
    proventos_12m: Decimal | None = None  # soma por ação (moeda nativa), data-com nos últimos 365 dias
    proventos_n: int = 0
    proximos_proventos: list = field(default_factory=list)
    fonte_proventos: str = ""
    proximo_resultado: EarningsEvent | None = None
    ultimo_resultado: EarningsEvent | None = None
    noticias: list[NewsItem] = field(default_factory=list)
    noticias_recentes: int = 0
    na_carteira: bool = False
    sparkline: list[float] = field(default_factory=list)
    variacao_dia: float | None = None  # da cotação (fechamento anterior informado pela fonte)
    maior_salto: tuple[dt.date, float] | None = None  # maior variação de um pregão no último mês (ações/ETFs)
    day_session: dt.date | None = None
    day_base_date: dt.date | None = None
    ranking_warning: str = ''

    @property
    def ranking_eligible(self):
        return self.atualidade.permite_alertas and not self.ranking_warning

    @property
    def day_reference_label(self):
        if self.asset.market == Market.CRYPTO:
            return 'Últimas 24 h'
        return f'Pregão de {self.day_session:%d/%m/%Y}' if self.day_session else 'Pregão não confirmado'

    @property
    def tipo(self) -> str:
        return self.asset.tipo_rotulo

    @property
    def dy_12m(self) -> float | None:
        """Dividend yield de 12 meses pelos proventos registrados (soma bruta / preço atual), em %."""
        if self.proventos_12m is None or not self.preco:
            return None
        return float(self.proventos_12m / self.preco * 100)

    @property
    def dy(self) -> tuple[float | None, str]:
        """(DY em %, origem): proventos registrados quando houver; senão o informado pela fonte."""
        if self.dy_12m is not None and self.proventos_n:
            return self.dy_12m, f"proventos de 12 meses ({self.fonte_proventos})"
        f = self.fundamentos
        if f is not None and f.dividend_yield is not None:
            return float(f.dividend_yield) * 100, f"informado por {f.fontes.get('dividend_yield', '')}"
        return None, ""

    @property
    def tem_dados(self) -> bool:
        return self.ind is not None

    def variacao(self, periodo: str) -> float | None:
        if self.ind is None:
            # sem candles ainda: a variação do dia ainda sai da cotação (fechamento anterior da fonte)
            return self.variacao_dia if periodo == "1d" else None
        p = self.ind.periodos.get(periodo)
        return p.variacao_pct if p else None


def _float(v) -> float | None:
    return None if v is None else float(v)


def _barras(asset: Asset) -> tuple[list[ind_mod.Barra], str, dt.datetime | None]:
    fonte = fonte_principal(asset)
    if fonte is None:
        return [], "", None
    desde = timezone.now().date() - dt.timedelta(days=420)
    regs = list(
        DailyBar.objects.filter(asset=asset, source=fonte, session_date__gte=desde)
        .order_by("session_date")
        .values_list("session_date", "close", "high", "low", "volume", "provisional", "collected_at")
    )
    barras = [
        ind_mod.Barra(d, float(c), _float(h), _float(lo), _float(v), bool(p)) for d, c, h, lo, v, p, _ in regs
    ]
    coletado = max((r[6] for r in regs), default=None)
    return barras, fonte, coletado


def _externos(leitura: LeituraAtivo, hoje: dt.date) -> score_mod.DadosExternos:
    a = leitura.asset
    ext = score_mod.DadosExternos(etf=a.is_etf, cripto=a.market == Market.CRYPTO)
    if a.market == Market.CRYPTO:
        return ext
    snaps = list(AnalystSnapshot.objects.filter(asset=a, source="finnhub").order_by("-period")[:2])
    if not snaps:
        snaps = list(AnalystSnapshot.objects.filter(asset=a).order_by("-period")[:2])
    if snaps:
        s = snaps[0]
        leitura.analistas = s
        ext.n_analistas = s.total
        ext.pct_compra = _float(s.pct_compra)
        ext.pct_venda = _float(s.pct_venda)
        ext.consenso = s.consenso
        ext.fonte_analistas = s.source
        ext.data_analistas = s.period
        if (
            len(snaps) > 1
            and snaps[1].source == s.source
            and s.pct_compra is not None
            and snaps[1].pct_compra is not None
        ):
            leitura.analistas_anterior = snaps[1]
            leitura.mudanca_recomendacao_pp = float(s.pct_compra - snaps[1].pct_compra)
    alvo = (
        PriceTarget.objects.filter(asset=a, source="finnhub").order_by("-collected_at").first()
        or PriceTarget.objects.filter(asset=a).order_by("-collected_at").first()
    )
    if alvo and alvo.mean:
        leitura.preco_alvo = alvo
        ext.preco_alvo = float(alvo.mean)
        ext.fonte_alvo = alvo.source
        ext.data_alvo = alvo.reference_date or alvo.collected_at.date()
    fund = fundamentos_mesclados(a)
    if fund:
        leitura.fundamentos = fund
        ext.margem, ext.roe, ext.pl = _float(fund.profit_margin), _float(fund.roe), _float(fund.pe)
        ext.fonte_fundamentos = fund.source
        ext.data_fundamentos = fund.collected_at.date()
        if ext.pct_compra is None and fund.recommendation_mean is not None:
            ext.recomendacao_media = _float(fund.recommendation_mean)
            ext.n_analistas = fund.analyst_opinions or 0
            ext.fonte_analistas = fund.fontes.get("recommendation_mean", "")
    provs = list(Provento.objects.filter(asset=a).order_by("-data_com", "-data_pagamento"))
    if provs:
        ano = [p for p in provs if p.data_referencia and p.data_referencia > hoje - dt.timedelta(days=365)]
        leitura.proventos_12m = sum((p.valor for p in ano if p.data_referencia <= hoje), Decimal(0))
        leitura.proventos_n = len(ano)
        leitura.proximos_proventos = sorted(
            [
                p
                for p in provs
                if (p.data_pagamento and p.data_pagamento >= hoje) or (p.data_com and p.data_com >= hoje)
            ],
            key=lambda p: p.data_com or p.data_pagamento,
        )
        leitura.fonte_proventos = provs[0].source
    prox = EarningsEvent.objects.filter(asset=a, date__gte=hoje).order_by("date").first()
    if prox:
        leitura.proximo_resultado = prox
        ext.dias_para_resultado = (prox.date - hoje).days
    leitura.ultimo_resultado = (
        EarningsEvent.objects.filter(asset=a, date__lt=hoje, eps_actual__isnull=False)
        .order_by("-date")
        .first()
    )
    return ext


def leitura_do_ativo(asset: Asset, agora: dt.datetime | None = None) -> LeituraAtivo:
    agora = agora or timezone.now()
    hoje = timezone.localdate(agora)
    atual = freshness.atualidade(asset, agora)
    q = atual.cotacao
    leitura = LeituraAtivo(
        asset=asset,
        atualidade=atual,
        preco=q.price if q else None,
        moeda="USD" if (q and q.currency == "USDT") else (q.currency if q else asset.native_currency),
        cotado_em=q.collected_at if q else None,
        fonte_preco=q.source if q else "",
        na_carteira=asset.has_position,
    )
    barras, fonte, coletado = _barras(asset)
    leitura.fonte_barras, leitura.barras_coletadas_em = fonte, coletado
    leitura.sparkline = [b.close for b in barras[-30:]]
    preco = float(q.price) if q else (barras[-1].close if barras else None)
    if preco is None and barras:
        preco = barras[-1].close
    if leitura.preco is None and barras:
        # sem cotação, mas com candles: preço = último fechamento (rótulo indica a data)
        leitura.preco = Decimal(str(barras[-1].close))
        leitura.fonte_preco = f"{fonte} (fechamento de {barras[-1].data:%d/%m})"
    variacao_dia = None
    if q and (q.collected_at > agora + dt.timedelta(minutes=5) or
              q.market_time and q.market_time > agora + dt.timedelta(minutes=5)):
        leitura.ranking_warning = 'Horário da cotação no futuro; excluído do ranking.'
    if q and asset.market != Market.CRYPTO:
        from apps.marketdata.calendars import calendario
        cal = calendario(asset.market)
        session = cal.situacao(q.market_time or q.collected_at).sessao
        leitura.day_session = session.data if session else None
        raw = q.raw or {}
        if raw.get('candle'):
            try:
                leitura.day_session = dt.date.fromisoformat(raw['candle'])
            except (ValueError, TypeError):
                pass
        elif raw.get('timestamp_candle'):
            try:
                leitura.day_session = dt.datetime.fromisoformat(raw['timestamp_candle']).astimezone(cal.tz).date()
            except (ValueError, TypeError):
                pass
        expected = cal.situacao(agora).sessao
        if expected and leitura.day_session and leitura.day_session < expected.data:
            leitura.ranking_warning = 'Cotação ainda pertence a um pregão anterior; excluída até a fonte atualizar.'
        previous = [b for b in barras if leitura.day_session and b.data < leitura.day_session]
        if q.previous_close and previous and abs(Decimal(str(previous[-1].close)) - q.previous_close) <= Decimal('.0001'):
            leitura.day_base_date = previous[-1].data
        if not q.previous_close or q.previous_close <= 0:
            leitura.ranking_warning = 'Fechamento anterior ausente; variação diária não confirmada.'
    if q is not None:
        if asset.market == Market.CRYPTO and q.change_24h_pct is not None:
            variacao_dia = float(q.change_24h_pct)
        elif q.previous_close:
            variacao_dia = float((q.price / q.previous_close - 1) * 100)
    leitura.variacao_dia = variacao_dia
    if asset.market != Market.CRYPTO and len(barras) >= 2:
        recentes = barras[-23:]
        saltos = [
            (b.data, (b.close / a.close - 1) * 100)
            for a, b in zip(recentes, recentes[1:], strict=False)
            if a.close
        ]
        if saltos:
            leitura.maior_salto = max(saltos, key=lambda t: abs(t[1]))
    if preco is not None and barras:
        leitura.ind = ind_mod.calcular(barras, preco, hoje, asset.market == Market.CRYPTO, variacao_dia)
        # A quote for today's session can arrive before today's daily candle. Do not label its
        # previous-close denominator with the penultimate historical bar's date.
        day = leitura.ind.periodos['1d']
        day.base_data = leitura.day_base_date
        day.variacao_pct = variacao_dia if leitura.ranking_eligible else None
    leitura.ext = _externos(leitura, hoje)
    if leitura.ind is not None:
        leitura.score = score_mod.calcular(leitura.ind, leitura.ext)
    noticias = list(NewsItem.objects.filter(asset=asset).order_by("-published_at")[:6])
    leitura.noticias = noticias
    leitura.noticias_recentes = sum(1 for n in noticias if agora - n.published_at <= JANELA_NOTICIAS)
    return leitura


def ativos_avaliados():
    return Asset.objects.filter(
        is_active=True, market__in=(Market.NYSE, Market.NASDAQ, Market.CRYPTO, Market.B3)
    ).order_by("symbol")


def leituras(agora: dt.datetime | None = None, usar_cache: bool = True) -> list[LeituraAtivo]:
    if usar_cache:
        em_cache = cache.get(CHAVE_CACHE)
        if em_cache is not None:
            return em_cache
    agora = agora or timezone.now()
    saida = [leitura_do_ativo(a, agora) for a in ativos_avaliados()]
    if usar_cache:
        cache.set(CHAVE_CACHE, saida, CACHE_S)
    return saida


def invalidar_cache() -> None:
    cache.delete(CHAVE_CACHE)


# --- Rankings ------------------------------------------------------------------------------------

TIPOS_FILTRO = {"todos": "Todos", "acao": "Ações", "etf": "ETFs", "cripto": "Cripto", "carteira": "Carteira"}


def _passa_tipo(leitura: LeituraAtivo, tipo: str, favoritos: set[int] | None = None) -> bool:
    a = leitura.asset
    if tipo == "acao":
        return a.market != Market.CRYPTO and not a.is_etf
    if tipo == "etf":
        return a.is_etf
    if tipo == "cripto":
        return a.market == Market.CRYPTO
    if tipo == "carteira":
        return leitura.na_carteira
    if tipo == "favoritos":
        return favoritos is not None and a.pk in favoritos
    return True


def ranking(lista: list[LeituraAtivo], periodo: str, tipo: str = "todos", favoritos=None) -> dict:
    """{'altas': [...], 'quedas': [...], 'sem_dado': [...]} com (leitura, Periodo)."""
    if periodo not in ind_mod.PERIODOS_DIAS:
        periodo = "1d"
    com, sem = [], []
    for leitura in lista:
        if not _passa_tipo(leitura, tipo, favoritos):
            continue
        p = leitura.ind.periodos.get(periodo) if leitura.ind else None
        if not leitura.ranking_eligible:
            sem.append(leitura)
            continue
        if p is None and periodo == '1d' and leitura.variacao_dia is not None:
            p = ind_mod.Periodo('1d', leitura.variacao_dia, None, None, None, None, leitura.day_base_date)
        if p is None or p.variacao_pct is None:
            sem.append(leitura)
            continue
        com.append((leitura, p))
    altas = sorted([x for x in com if x[1].variacao_pct > 0], key=lambda x: -x[1].variacao_pct)
    quedas = sorted([x for x in com if x[1].variacao_pct < 0], key=lambda x: x[1].variacao_pct)
    return {"altas": altas, "quedas": quedas, "sem_dado": sem, "periodo": periodo}


# --- Oportunidades --------------------------------------------------------------------------------


def top10(lista: list[LeituraAtivo]) -> list[LeituraAtivo]:
    elegiveis = [x for x in lista if x.score and x.score.elegivel_top10]
    return sorted(elegiveis, key=lambda x: (-x.score.total, x.asset.symbol))[:10]


def pontuados(lista: list[LeituraAtivo]) -> list[LeituraAtivo]:
    return sorted([x for x in lista if x.score], key=lambda x: (-x.score.total, x.asset.symbol))


# --- Favoritos -------------------------------------------------------------------------------------


def ids_favoritos(user) -> set[int]:
    if user is None or not user.is_authenticated:
        return set()
    return set(Favorito.objects.filter(user=user).values_list("asset_id", flat=True))


@dataclass
class Achado:
    leitura: LeituraAtivo
    motivo: str


def analisar_favoritos(
    lista: list[LeituraAtivo], agora: dt.datetime | None = None
) -> dict[str, list[Achado]]:
    """Cinco perguntas, cada uma com uma regra fixa (documentada na tela)."""
    agora = agora or timezone.now()
    entrada, potencial, tendencia_alta, tendencia_baixa, mudancas, atencao = [], [], [], [], [], []
    for x in lista:
        i, s = x.ind, x.score
        if i is None:
            continue
        # 1. possíveis pontos de entrada: perto do suporte de 20 sessões ou da mínima de 52 semanas, RSI ≤ 40
        motivos = []
        if i.suporte20 and i.preco <= i.suporte20 * 1.03 and i.preco >= i.suporte20:
            motivos.append(f"a até 3% do suporte de 20 sessões ({_num(i.suporte20)})")
        dist = i.dist_minima_52s_pct
        if dist is not None and dist <= 10 and i.janela_52s_dias >= 180:
            motivos.append(f"{_fmt_pct(dist)} acima da mínima de 52 semanas")
        if motivos and (i.rsi14 is None or i.rsi14 <= 40):
            rsi_txt = f"; RSI {i.rsi14:.0f}" if i.rsi14 is not None else ""
            entrada.append(Achado(x, " e ".join(motivos) + rsi_txt))
        # 2. maior potencial: preço-alvo e score
        if s and s.potencial_pct is not None and s.potencial_pct > 0:
            potencial.append(
                Achado(
                    x, f"{_fmt_pct(s.potencial_pct)} até o alvo médio ({x.ext.fonte_alvo}); score {s.total}"
                )
            )
        elif s and s.elegivel_top10 and s.total >= 50:
            potencial.append(Achado(x, f"score {s.total} (sem preço-alvo)"))
        # 3. tendência
        if i.tendencia == "alta":
            tendencia_alta.append(Achado(x, "preço > MM50 > MM200"))
        elif i.tendencia == "baixa":
            tendencia_baixa.append(Achado(x, "preço < MM50 < MM200"))
        # 4. mudanças relevantes
        m = []
        if x.mudanca_recomendacao_pp is not None and abs(x.mudanca_recomendacao_pp) >= 10:
            m.append(f"recomendações de compra {x.mudanca_recomendacao_pp:+.0f} p.p. no mês")
        if x.proximo_resultado and (x.proximo_resultado.date - timezone.localdate(agora)).days <= 14:
            m.append(f"resultado em {x.proximo_resultado.date:%d/%m}")
        if x.ultimo_resultado and (timezone.localdate(agora) - x.ultimo_resultado.date).days <= 14:
            r = x.ultimo_resultado
            if r.eps_estimate is not None and r.eps_actual is not None:
                lado = (
                    "acima"
                    if r.eps_actual > r.eps_estimate
                    else "abaixo"
                    if r.eps_actual < r.eps_estimate
                    else "em linha com"
                )
                m.append(f"resultado de {r.date:%d/%m}: LPA {lado} da estimativa")
        if x.noticias_recentes >= 3:
            m.append(f"{x.noticias_recentes} notícias em 48 h")
        if i.vol90 and i.variacao_5d is not None and abs(i.variacao_5d) >= max(8.0, i.vol90 / 4):
            m.append(f"movimento de {_fmt_pct(i.variacao_5d)} na semana, fora do padrão")
        if m:
            mudancas.append(Achado(x, "; ".join(m)))
        # 5. atenção por aumento de risco
        if s and s.risco == "alto":
            atencao.append(Achado(x, "; ".join(s.motivos_risco)))
        elif i.suporte20 and i.preco < i.suporte20:
            atencao.append(Achado(x, f"rompeu o suporte de 20 sessões ({_num(i.suporte20)})"))
    potencial.sort(key=lambda a: -(a.leitura.score.potencial_pct or 0) if a.leitura.score else 0)
    return {
        "entrada": entrada,
        "potencial": potencial[:8],
        "tendencia_alta": tendencia_alta,
        "tendencia_baixa": tendencia_baixa,
        "mudancas": mudancas,
        "atencao": atencao,
    }


# --- Resumo executivo --------------------------------------------------------------------------------


def _fmt_pct(v: float | None) -> str:
    return "—" if v is None else f"{v:+.1f}%".replace(".", ",")


def leitura_executiva(x: LeituraAtivo) -> dict[str, list[Item]]:
    """Por que pode subir / o que pode impedir, separado em fatos, consenso e projeções."""
    sobe: list[Item] = []
    impede: list[Item] = []
    fatos: list[Item] = []
    i, s = x.ind, x.score
    if i is not None:
        fonte = x.fonte_preco or x.fonte_barras
        fatos.append(
            Item(
                "fato",
                f"{_fmt_pct(i.periodos['1d'].variacao_pct)} no dia, "
                f"{_fmt_pct(i.periodos['1m'].variacao_pct)} em 1 mês, "
                f"{_fmt_pct(i.periodos['12m'].variacao_pct)} em 12 meses",
                fonte,
                x.cotado_em,
            )
        )
        if i.dist_maxima_52s_pct is not None:
            fatos.append(
                Item("fato", f"{_fmt_pct(i.dist_maxima_52s_pct)} da máxima de 52 semanas", x.fonte_barras)
            )
        if i.tendencia == "alta":
            sobe.append(Item("fato", "tendência de alta pelas médias (preço > MM50 > MM200)", x.fonte_barras))
        elif i.tendencia == "baixa":
            impede.append(
                Item("risco", "tendência de baixa pelas médias (preço < MM50 < MM200)", x.fonte_barras)
            )
        if i.rsi14 is not None and i.rsi14 <= 30:
            sobe.append(
                Item(
                    "fato",
                    f"RSI {i.rsi14:.0f}: sobrevenda — recuperações técnicas são comuns, mas não garantidas",
                )
            )
        if i.rsi14 is not None and i.rsi14 >= 70:
            impede.append(Item("risco", f"RSI {i.rsi14:.0f}: sobrecompra — correções são comuns"))
    if x.analistas and x.analistas.total:
        a = x.analistas
        txt = (
            f"{a.strong_buy + a.buy} compra · {a.hold} manter · {a.sell + a.strong_sell} venda "
            f"({a.total} analistas; maioria: {a.consenso})"
        )
        item = Item("consenso", txt, a.source, a.period)
        (sobe if a.consenso == "comprar" else impede if a.consenso == "vender" else fatos).append(item)
        if x.mudanca_recomendacao_pp is not None and abs(x.mudanca_recomendacao_pp) >= 10:
            alvo = sobe if x.mudanca_recomendacao_pp > 0 else impede
            alvo.append(
                Item(
                    "consenso",
                    f"recomendações de compra {x.mudanca_recomendacao_pp:+.0f} p.p. vs. mês anterior",
                    a.source,
                    a.period,
                )
            )
    if s and s.potencial_pct is not None:
        alvo = x.preco_alvo
        item = Item(
            "projecao",
            f"preço-alvo médio {_num(float(alvo.mean))} → potencial de {_fmt_pct(s.potencial_pct)} "
            "(horizonte típico de 12 meses)",
            alvo.source,
            alvo.reference_date or alvo.collected_at,
        )
        (sobe if s.potencial_pct > 0 else impede).append(item)
    f = x.fundamentos
    if f is not None:
        if f.profit_margin is not None and f.profit_margin <= 0:
            impede.append(Item("fato", "empresa com prejuízo nos últimos 12 meses", f.source, f.collected_at))
        if f.pe is not None and f.pe > 45:
            impede.append(
                Item("fato", f"P/L {f.pe:.0f}: preço exige crescimento forte", f.source, f.collected_at)
            )
    if x.proximo_resultado is not None:
        r = x.proximo_resultado
        impede.append(
            Item(
                "risco",
                f"resultado em {r.date:%d/%m} {r.horario_rotulo}: costuma mover o preço nos dois sentidos",
                r.source,
                r.collected_at,
            )
        )
    if s:
        for motivo in s.motivos_risco[1:]:
            impede.append(Item("risco", motivo))
    return {"fatos": fatos, "sobe": sobe, "impede": impede}


@dataclass
class Resumo:
    fatos: list[Item] = field(default_factory=list)
    consenso: list[Item] = field(default_factory=list)
    projecoes: list[Item] = field(default_factory=list)
    riscos: list[Item] = field(default_factory=list)
    setores: list[tuple[str, float, int]] = field(default_factory=list)
    eventos: list[EarningsEvent] = field(default_factory=list)
    cobertura: dict = field(default_factory=dict)


def resumo_mercado(lista: list[LeituraAtivo], agora: dt.datetime | None = None) -> Resumo:
    agora = agora or timezone.now()
    hoje = timezone.localdate(agora)
    r = Resumo()
    com = [x for x in lista if x.ind is not None]
    r.cobertura = {
        "ativos": len(lista),
        "com_historico": len(com),
        "com_analistas": sum(1 for x in lista if x.analistas),
        "com_alvo": sum(1 for x in lista if x.preco_alvo),
    }
    if not com:
        return r
    dia = [(x, x.variacao("1d")) for x in com if x.variacao("1d") is not None]
    altas = sum(1 for _, v in dia if v > 0)
    quedas = sum(1 for _, v in dia if v < 0)
    if dia:
        melhor = max(dia, key=lambda t: t[1])
        pior = min(dia, key=lambda t: t[1])
        r.fatos.append(
            Item(
                "fato",
                f"{altas} ativos em alta e {quedas} em queda no dia, de {len(dia)} com variação disponível",
            )
        )
        r.fatos.append(
            Item(
                "fato",
                f"maior alta: {melhor[0].asset.symbol} {_fmt_pct(melhor[1])}; "
                f"maior queda: {pior[0].asset.symbol} {_fmt_pct(pior[1])}",
            )
        )
    perto_min = [
        x.asset.symbol
        for x in com
        if x.ind.dist_minima_52s_pct is not None
        and x.ind.dist_minima_52s_pct <= 10
        and x.ind.janela_52s_dias >= 180
    ]
    if perto_min:
        r.fatos.append(Item("fato", f"a até 10% da mínima de 52 semanas: {', '.join(perto_min)}"))
    # setores: média simples da variação de 1 mês por setor informado no cadastro
    grupos: dict[str, list[float]] = {}
    for x in com:
        v = x.variacao("1m")
        if v is None:
            continue
        grupos.setdefault(
            x.asset.sector or ("Cripto" if x.asset.market == Market.CRYPTO else "Sem setor"), []
        ).append(v)
    r.setores = sorted(((s, sum(v) / len(v), len(v)) for s, v in grupos.items()), key=lambda t: -t[1])
    if r.setores:
        topo, fundo = r.setores[0], r.setores[-1]
        r.fatos.append(
            Item(
                "fato",
                f"setor mais forte em 1 mês: {topo[0]} {_fmt_pct(topo[1])} (média de {topo[2]}); "
                f"mais fraco: {fundo[0]} {_fmt_pct(fundo[1])}",
            )
        )
    tend = [x.asset.symbol for x in com if x.ind.tendencia == "alta"]
    if tend:
        r.fatos.append(
            Item(
                "fato",
                f"tendência de alta pelas médias: {', '.join(tend[:12])}{'…' if len(tend) > 12 else ''}",
            )
        )
    comp = sorted(
        [x for x in lista if x.analistas and x.analistas.total >= 5],
        key=lambda x: -(float(x.analistas.pct_compra or 0)),
    )
    if comp:
        r.consenso.append(
            Item(
                "consenso",
                "maior fatia de recomendações de compra: "
                + ", ".join(f"{x.asset.symbol} {float(x.analistas.pct_compra):.0f}%" for x in comp[:5]),
                comp[0].analistas.source,
                comp[0].analistas.period,
            )
        )
    mud = [x for x in lista if x.mudanca_recomendacao_pp is not None and abs(x.mudanca_recomendacao_pp) >= 10]
    for x in mud[:5]:
        r.consenso.append(
            Item(
                "consenso",
                f"{x.asset.symbol}: recomendações de compra {x.mudanca_recomendacao_pp:+.0f} p.p. no mês",
                x.analistas.source,
                x.analistas.period,
            )
        )
    pot = sorted(
        [x for x in lista if x.score and x.score.potencial_pct is not None],
        key=lambda x: -x.score.potencial_pct,
    )
    if pot:
        r.projecoes.append(
            Item(
                "projecao",
                "maior distância até o preço-alvo médio: "
                + ", ".join(f"{x.asset.symbol} {_fmt_pct(x.score.potencial_pct)}" for x in pot[:5]),
                pot[0].preco_alvo.source,
            )
        )
    baixa = [x.asset.symbol for x in com if x.ind.tendencia == "baixa"]
    if baixa:
        r.riscos.append(Item("risco", f"tendência de baixa pelas médias: {', '.join(baixa[:12])}"))
    vol = [x.asset.symbol for x in com if x.score and x.score.risco == "alto"]
    if vol:
        r.riscos.append(
            Item(
                "risco",
                f"risco alto pela régua do portal: {', '.join(vol[:12])}{'…' if len(vol) > 12 else ''}",
            )
        )
    r.eventos = list(
        EarningsEvent.objects.filter(
            date__gte=hoje, date__lte=hoje + dt.timedelta(days=14), asset__is_active=True
        )
        .select_related("asset")
        .order_by("date")[:15]
    )
    if r.eventos:
        r.riscos.append(
            Item(
                "risco",
                "resultados nos próximos 14 dias: "
                + ", ".join(f"{e.asset.symbol} {e.date:%d/%m}" for e in r.eventos[:8]),
            )
        )
    return r
