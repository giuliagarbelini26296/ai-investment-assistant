"""
Resumo executivo estruturado da Visão geral (rc9).

Mesmas regras do resumo em texto (analise.resumo_mercado), mas em linhas com ativo, número e detalhe, para a
tela mostrar tabelas legíveis e, em cada bloco, a fonte, o horário de referência e as limitações.

Nada é estimado aqui: cada número vem de uma cotação, de um candle ou de uma fonte de terceiros registrada no
banco. Quando a fonte não tem o dado (ex.: preço-alvo no plano gratuito), o bloco diz por quê.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from dataclasses import dataclass, field

from django.utils import timezone

from apps.marketdata.adapters import ADAPTADORES, chave_ausente
from apps.marketdata.calendars import calendario
from apps.marketdata.models import ScheduleState
from apps.portfolio.models import Market

from .analise import LeituraAtivo
from .models import AnalystAction, EarningsEvent, FocusExpectativa, MacroSerie, RefreshState

GRUPOS_MERCADO = (
    ("EUA", (Market.NYSE, Market.NASDAQ), "NYSE"),
    ("Brasil", (Market.B3,), "B3"),
    ("Cripto", (Market.CRYPTO,), None),
)
LIMITE_ATIPICO_MES = 25.0  # % em 1 mês para ação/ETF
LIMITE_ATIPICO_DIA = 15.0  # % num único pregão
FOCUS_INDICADORES = (
    ("Selic", "Selic no fim do ano", "% a.a."),
    ("IPCA", "IPCA no ano", "%"),
    ("Câmbio", "Dólar no fim do ano", "R$"),
)
SEM_ACESSO = dt.timedelta(days=7)


@dataclass
class Linha:
    x: LeituraAtivo
    valor: float | None = None
    detalhe: str = ""


@dataclass
class Referencia:
    mercado: str
    situacao: str
    fonte: str
    atraso: str
    horario: dt.datetime | None
    n: int


@dataclass
class Setor:
    nome: str
    media: float
    membros: list[str]


@dataclass
class Consenso:
    x: LeituraAtivo
    total: int
    pct_compra: float
    barras: list[tuple[str, int, float]]  # (classe, contagem, % da largura)
    delta_pp: float | None
    periodo: dt.date
    seu: bool = False


@dataclass
class FocusLinha:
    rotulo: str
    unidade: str
    anos: list[tuple[str, FocusExpectativa | None]]


@dataclass
class Painel:
    cobertura: dict = field(default_factory=dict)
    referencias: list[Referencia] = field(default_factory=list)
    altas: int = 0
    quedas: int = 0
    estaveis: int = 0
    com_variacao: int = 0
    maiores_altas: list[Linha] = field(default_factory=list)
    maiores_quedas: list[Linha] = field(default_factory=list)
    perto_minima: list[Linha] = field(default_factory=list)
    setores_fortes: list[Setor] = field(default_factory=list)
    setores_fracos: list[Setor] = field(default_factory=list)
    tendencia_alta: list[Linha] = field(default_factory=list)
    atipicos: list[Linha] = field(default_factory=list)
    consenso_top: list[Consenso] = field(default_factory=list)
    consenso_seus: list[Consenso] = field(default_factory=list)
    analistas_periodo: dt.date | None = None
    analistas_coleta: dt.datetime | None = None
    mudancas: list[AnalystAction] = field(default_factory=list)
    mudancas_estado: str = ""  # ok | sem_acesso | aguardando
    alvos: list[Linha] = field(default_factory=list)
    focus: list[FocusLinha] = field(default_factory=list)
    focus_data: dt.date | None = None
    focus_estado: dict = field(default_factory=dict)
    macro: list[MacroSerie] = field(default_factory=list)
    tendencia_baixa: list[Linha] = field(default_factory=list)
    risco_alto: list[Linha] = field(default_factory=list)
    eventos: list[tuple[EarningsEvent, LeituraAtivo | None]] = field(default_factory=list)

    @property
    def pct_altas(self) -> float:
        return 100 * self.altas / self.com_variacao if self.com_variacao else 0

    @property
    def pct_quedas(self) -> float:
        return 100 * self.quedas / self.com_variacao if self.com_variacao else 0


def _fmt(v: float, casas: int = 1) -> str:
    return f"{v:+.{casas}f}%".replace(".", ",")


def _nome_fonte(codigo: str) -> str:
    cls = ADAPTADORES.get(codigo)
    return cls.info.nome.split(" (")[0] if cls else (codigo or "—")


def _referencias(lista: list[LeituraAtivo], agora: dt.datetime) -> list[Referencia]:
    saida = []
    for rotulo, mercados, cal in GRUPOS_MERCADO:
        grupo = [x for x in lista if x.asset.market in mercados and x.cotado_em]
        if not grupo:
            continue
        fontes = Counter(x.fonte_preco.split(" ")[0] for x in grupo if x.fonte_preco)
        codigo = fontes.most_common(1)[0][0] if fontes else ""
        cls = ADAPTADORES.get(codigo)
        if cal is None:
            situacao = "24 h"
        else:
            sit = calendario(cal).situacao(agora)
            if sit.estado == "aberto":
                situacao = "pregão aberto"
            elif sit.sessao is not None:
                situacao = f"fechado · último pregão {sit.sessao.data:%d/%m}"
            else:
                situacao = "fechado"
        saida.append(
            Referencia(
                mercado=rotulo,
                situacao=situacao,
                fonte=_nome_fonte(codigo),
                atraso=cls.info.atraso_declarado_texto if cls else "",
                horario=max(x.cotado_em for x in grupo),
                n=len(grupo),
            )
        )
    return saida


def _consenso(x: LeituraAtivo, seus: set[int]) -> Consenso | None:
    a = x.analistas
    if a is None or not a.total:
        return None
    partes = (
        ("compra-forte", a.strong_buy),
        ("compra", a.buy),
        ("manter", a.hold),
        ("venda", a.sell),
        ("venda-forte", a.strong_sell),
    )
    return Consenso(
        x=x,
        total=a.total,
        pct_compra=float(a.pct_compra or 0),
        barras=[(c, n, 100 * n / a.total) for c, n in partes if n],
        delta_pp=x.mudanca_recomendacao_pp,
        periodo=a.period,
        seu=x.asset.pk in seus,
    )


def _setores(com: list[LeituraAtivo]) -> list[Setor]:
    grupos: dict[str, list[LeituraAtivo]] = {}
    for x in com:
        if x.variacao("1m") is None:
            continue
        nome = x.asset.sector or ("Cripto" if x.asset.market == Market.CRYPTO else "Sem setor")
        grupos.setdefault(nome, []).append(x)
    setores = [
        Setor(nome, sum(x.variacao("1m") for x in xs) / len(xs), [x.asset.symbol for x in xs])
        for nome, xs in grupos.items()
    ]
    return sorted(setores, key=lambda s: -s.media)


def _focus(agora: dt.datetime) -> tuple[list[FocusLinha], dt.date | None]:
    ultima = FocusExpectativa.objects.filter(base_calculo=0).order_by("-data").first()
    if ultima is None:
        return [], None
    anos = [str(agora.year), str(agora.year + 1)]
    linhas = []
    for indicador, rotulo, unidade in FOCUS_INDICADORES:
        regs = {
            f.data_referencia: f
            for f in FocusExpectativa.objects.filter(
                indicador=indicador, base_calculo=0, data=ultima.data, data_referencia__in=anos
            )
        }
        if regs:
            linhas.append(FocusLinha(rotulo, unidade, [(ano, regs.get(ano)) for ano in anos]))
    return linhas, ultima.data


def _estado_tarefa(task: str) -> dict:
    e = ScheduleState.objects.filter(task=task).first()
    if e is None or e.last_attempt_at is None:
        return {"estado": "aguardando", "quando": None, "erro": ""}
    if e.last_status == "sem_acesso":
        return {"estado": "sem_acesso", "quando": e.last_attempt_at, "erro": e.last_error}
    if e.last_error and (e.last_success_at is None or e.last_success_at < e.last_attempt_at):
        return {"estado": "falha", "quando": e.last_attempt_at, "erro": e.last_error}
    return {"estado": "ok", "quando": e.last_success_at, "erro": ""}


def montar(lista: list[LeituraAtivo], favoritos: set[int] | None = None, agora=None) -> Painel:
    agora = agora or timezone.now()
    hoje = timezone.localdate(agora)
    favoritos = favoritos or set()
    seus = {x.asset.pk for x in lista if x.na_carteira} | set(favoritos)
    p = Painel()
    com = [x for x in lista if x.ind is not None]
    p.cobertura = {
        "ativos": len(lista),
        "com_historico": len(com),
        "com_analistas": sum(1 for x in lista if x.analistas),
        "com_alvo": sum(1 for x in lista if x.preco_alvo),
    }
    p.referencias = _referencias(lista, agora)

    # --- fatos ---
    dia = [(x, x.variacao("1d")) for x in lista if x.ranking_eligible and x.variacao("1d") is not None]
    p.com_variacao = len(dia)
    p.altas = sum(1 for _, v in dia if v > 0)
    p.quedas = sum(1 for _, v in dia if v < 0)
    p.estaveis = p.com_variacao - p.altas - p.quedas
    ordenado = sorted(dia, key=lambda t: -t[1])
    p.maiores_altas = [Linha(x, v) for x, v in ordenado[:5] if v > 0]
    p.maiores_quedas = [Linha(x, v) for x, v in reversed(ordenado[-5:]) if v < 0]
    p.perto_minima = sorted(
        (
            Linha(x, x.ind.dist_minima_52s_pct, f"mín. 52 sem. {x.ind.minima_52s:.2f}".replace(".", ","))
            for x in com
            if x.ind.dist_minima_52s_pct is not None
            and x.ind.dist_minima_52s_pct <= 10
            and x.ind.janela_52s_completa
        ),
        key=lambda li: li.valor,
    )[:8]
    setores = _setores(com)
    if setores:
        p.setores_fortes = setores[:2]
        p.setores_fracos = [s for s in setores[-2:][::-1] if s not in p.setores_fortes]
    p.tendencia_alta = [
        Linha(x, (x.ind.preco / x.ind.mm200 - 1) * 100, "acima da média de 200 sessões")
        for x in com
        if x.ind.tendencia == "alta" and x.ind.mm200
    ]
    p.tendencia_alta.sort(key=lambda li: -li.valor)
    for x in com:
        if x.asset.market == Market.CRYPTO:
            continue
        v1m = x.variacao("1m")
        if x.maior_salto and abs(x.maior_salto[1]) >= LIMITE_ATIPICO_DIA:
            d, v = x.maior_salto
            p.atipicos.append(Linha(x, v, f"num único pregão ({d:%d/%m})"))
        elif v1m is not None and abs(v1m) >= LIMITE_ATIPICO_MES:
            p.atipicos.append(Linha(x, v1m, "em 1 mês"))

    # --- analistas ---
    consensos = [c for c in (_consenso(x, seus) for x in lista) if c]
    elegiveis = (c for c in consensos if c.total >= 5)
    p.consenso_top = sorted(elegiveis, key=lambda c: (-c.pct_compra, -c.total))[:6]
    topo = {c.x.asset.pk for c in p.consenso_top}
    p.consenso_seus = sorted(
        (c for c in consensos if c.seu and c.x.asset.pk not in topo), key=lambda c: -c.pct_compra
    )[:6]
    if consensos:
        p.analistas_periodo = max(c.periodo for c in consensos)
        p.analistas_coleta = max(c.x.analistas.collected_at for c in consensos)
    p.mudancas = list(
        AnalystAction.objects.filter(date__gte=hoje - dt.timedelta(days=30), asset__is_active=True)
        .select_related("asset")
        .order_by("-date", "asset__symbol")[:8]
    )
    p.mudancas_estado = _estado_tarefa("FH_MUDANCAS")["estado"]

    # --- projeções ---
    p.alvos = sorted(
        (Linha(x, x.score.potencial_pct) for x in lista if x.score and x.score.potencial_pct is not None),
        key=lambda li: -li.valor,
    )[:5]
    p.focus, p.focus_data = _focus(agora)
    p.focus_estado = _estado_tarefa("FOCUS")
    p.macro = [
        m
        for m in (MacroSerie.objects.filter(codigo=c).order_by("-data").first() for c in (432, 13522))
        if m is not None
    ]

    # --- riscos ---
    p.tendencia_baixa = sorted(
        (
            Linha(x, (x.ind.preco / x.ind.mm200 - 1) * 100, "abaixo da média de 200 sessões")
            for x in com
            if x.ind.tendencia == "baixa" and x.ind.mm200
        ),
        key=lambda li: li.valor,
    )
    p.risco_alto = sorted(
        (
            Linha(x, x.ind.vol90 if x.ind.vol90 is not None else x.ind.vol30, _motivo_extra(x))
            for x in com
            if x.score and x.score.risco == "alto"
        ),
        key=lambda li: -(li.valor or 0),
    )
    por_id = {x.asset.pk: x for x in lista}
    p.eventos = [
        (e, por_id.get(e.asset_id))
        for e in EarningsEvent.objects.filter(
            date__gte=hoje, date__lte=hoje + dt.timedelta(days=14), asset__is_active=True
        )
        .select_related("asset")
        .order_by("date")[:10]
    ]
    return p


def _motivo_extra(x: LeituraAtivo) -> str:
    """Primeiro motivo além da volatilidade (o que fez o risco subir de nível), se houver."""
    extras = [m for m in (x.score.motivos_risco if x.score else [])[1:] if m]
    return extras[0] if extras else ""


# --- estado das fontes de inteligência (selos no topo do Mercado) ---------------------------------------


def _ultimo_sucesso(prefixo: str):
    e = (
        RefreshState.objects.filter(kind__startswith=prefixo, last_success_at__isnull=False)
        .order_by("-last_success_at")
        .first()
    )
    return e.last_success_at if e else None


def estado_fontes(agora=None) -> list[dict]:
    """Por fonte: estado (ok | parcial | sem_chave | sem_acesso | falha | aguardando) e o que falta."""
    agora = agora or timezone.now()
    saida = []

    def item(codigo, estado, detalhe="", quando=None, titulo=""):
        cls = ADAPTADORES[codigo]
        saida.append(
            {
                "codigo": codigo,
                "nome": cls.info.nome.split(" (")[0],
                "estado": estado,
                "detalhe": detalhe,
                "quando": quando,
                "titulo": titulo or cls.info.observacoes,
            }
        )

    if chave_ausente("finnhub"):
        item("finnhub", "sem_chave", "sem FINNHUB_API_KEY no .env")
    else:
        pagos = []
        if (
            _estado_tarefa("FH_PRECO_ALVO")["estado"] == "sem_acesso"
            or RefreshState.objects.filter(
                kind="fh_preco_alvo", last_error__startswith="autenticacao"
            ).exists()
        ):
            pagos.append("preço-alvo")
        if _estado_tarefa("FH_MUDANCAS")["estado"] == "sem_acesso":
            pagos.append("analistas por instituição")
        quando = _ultimo_sucesso("fh_")
        detalhe = "analistas, notícias, resultados, métricas"
        if pagos:
            detalhe += " · pago: " + ", ".join(pagos)
        item("finnhub", "parcial" if pagos else ("ok" if quando else "aguardando"), detalhe, quando)

    if chave_ausente("alphavantage"):
        item("alphavantage", "sem_chave", "sem ALPHAVANTAGE_API_KEY no .env")
    else:
        ficha = _estado_tarefa("AV_FICHA")
        quando = _ultimo_sucesso("av_")
        if ficha["estado"] == "sem_acesso":
            ate = ficha["quando"] + SEM_ACESSO if ficha["quando"] else None
            item(
                "alphavantage",
                "parcial" if quando else "sem_acesso",
                f"ficha/preço-alvo pagos · nova tentativa {ate:%d/%m}" if ate else "ficha/preço-alvo pagos",
                quando,
            )
        else:
            item("alphavantage", "ok" if quando else "aguardando", "ficha da empresa e dividendos", quando)

    focus = _estado_tarefa("FOCUS")
    ultima = FocusExpectativa.objects.order_by("-data").first()
    if focus["estado"] == "falha":
        item("focus_bcb", "falha", focus["erro"][:120], focus["quando"])
    elif ultima:
        item("focus_bcb", "ok", f"boletim de {ultima.data:%d/%m}", focus["quando"])
    else:
        item("focus_bcb", focus["estado"], "Selic, IPCA e câmbio projetados", focus["quando"])

    macro = _estado_tarefa("MACRO")
    ultima_m = MacroSerie.objects.order_by("-collected_at").first()
    if macro["estado"] == "falha":
        item("sgs_bcb", "falha", macro["erro"][:120], macro["quando"])
    else:
        item(
            "sgs_bcb",
            "ok" if ultima_m else macro["estado"],
            "Selic meta e IPCA oficiais",
            ultima_m.collected_at if ultima_m else macro["quando"],
        )
    return saida


def fmt_pct(v: float | None, casas: int = 1) -> str:
    return "—" if v is None else _fmt(v, casas)
