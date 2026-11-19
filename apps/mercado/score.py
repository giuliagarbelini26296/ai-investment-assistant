"""
Score de atratividade 0–100 do portal: régua fixa de critérios visíveis, NÃO recomendação.

Grupos e pesos (somam 100):
  Técnico (35)       T1 proximidade da mínima de 52 semanas (12) · T2 RSI de 14 sessões (8) ·
                     T3 tendência: preço vs. MM200 (8) e momento do MACD (7)
  Analistas (40)     A1 potencial até o preço-alvo médio (25) · A2 consenso de recomendações (15)
  Fundamentos (25)   F1 margem líquida (8) · F2 ROE (8) · F3 P/L (9)

Regras de honestidade:
- critério sem dado fica "não avaliado" e vale 0 — o total nunca é reescalonado. Assim um ativo só com
  leitura técnica (ex.: cripto, ou sem chave das fontes de analistas) tem no máximo 35 pontos e uma queda
  isolada não vira "oportunidade";
- a cobertura (pontos avaliáveis) aparece ao lado do score;
- risco (baixo/médio/alto) vem da volatilidade e sobe um nível com sinais de alerta;
- perspectiva = horizonte dos critérios que mais pesaram (técnico → semanas; preço-alvo → ~12 meses).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from .indicadores import Indicadores

PESOS = {"tecnico": 35, "analistas": 40, "fundamentos": 25}
MIN_ANALISTAS = 5
LIMIAR_TOP10 = PESOS["tecnico"]  # só entra no Top 10 quem tem algo além da leitura técnica avaliado


@dataclass
class Criterio:
    codigo: str
    nome: str
    grupo: str
    maximo: int
    pontos: float = 0.0
    avaliado: bool = False
    explicacao: str = ""

    @property
    def pontos_int(self) -> int:
        return round(self.pontos)


@dataclass
class DadosExternos:
    """Dados de terceiros usados no score (cada um com fonte e data para a tela)."""

    pct_compra: float | None = None
    pct_venda: float | None = None
    n_analistas: int = 0
    consenso: str = ""
    fonte_analistas: str = ""
    data_analistas: dt.date | None = None
    preco_alvo: float | None = None
    fonte_alvo: str = ""
    data_alvo: dt.date | None = None
    margem: float | None = None  # fração
    roe: float | None = None  # fração
    pl: float | None = None
    fonte_fundamentos: str = ""
    data_fundamentos: dt.date | None = None
    dias_para_resultado: int | None = None
    recomendacao_media: float | None = None  # 1 = compra forte … 5 = venda (quando a fonte não dá contagens)
    etf: bool = False
    cripto: bool = False


@dataclass
class Score:
    total: int
    cobertura: int  # pontos avaliáveis (soma dos máximos dos critérios avaliados)
    criterios: list[Criterio]
    risco: str
    motivos_risco: list[str]
    perspectiva: str
    perspectiva_motivo: str
    potencial_pct: float | None
    observacoes: list[str] = field(default_factory=list)

    @property
    def faixa(self) -> str:
        if self.total >= 70:
            return "alta"
        if self.total >= 50:
            return "moderada"
        return "baixa"

    @property
    def elegivel_top10(self) -> bool:
        return self.cobertura > LIMIAR_TOP10

    def por_grupo(self) -> dict[str, tuple[int, int, int]]:
        """{grupo: (pontos, avaliável, máximo)}"""
        saida = {}
        for g, peso in PESOS.items():
            itens = [c for c in self.criterios if c.grupo == g]
            saida[g] = (
                round(sum(c.pontos for c in itens)),
                sum(c.maximo for c in itens if c.avaliado),
                peso,
            )
        return saida


def _linear(valor: float, zero_em: float, cheio_em: float) -> float:
    """0 em `zero_em`, 1 em `cheio_em`, linear entre eles (aceita escala invertida)."""
    if zero_em == cheio_em:
        return 1.0 if valor >= cheio_em else 0.0
    f = (valor - zero_em) / (cheio_em - zero_em)
    return max(0.0, min(1.0, f))


def _num(v: float) -> str:
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v:+.1f}%".replace(".", ",")


def calcular(ind: Indicadores, ext: DadosExternos) -> Score:
    c: list[Criterio] = []

    # T1 — proximidade da mínima de 52 semanas
    t1 = Criterio("T1", "Perto da mínima de 52 semanas", "tecnico", 12)
    dist = ind.dist_minima_52s_pct
    if dist is not None and ind.janela_52s_dias >= 180:
        t1.avaliado = True
        t1.pontos = 12 * _linear(dist, 40, 5)
        parcial = "" if ind.janela_52s_completa else f" (janela de {ind.janela_52s_dias} dias)"
        t1.explicacao = (
            f"preço {_pct(dist)} acima da mínima de 52 semanas{parcial}; 12 pts até 5%, 0 a partir de 40%"
        )
    else:
        t1.explicacao = "histórico menor que 6 meses: não avaliado"
    c.append(t1)

    # T2 — RSI(14)
    t2 = Criterio("T2", "RSI (14 sessões)", "tecnico", 8)
    if ind.rsi14 is not None:
        t2.avaliado = True
        r = ind.rsi14
        t2.pontos = 8 if r <= 30 else 5 if r <= 40 else 2 if r <= 55 else 0
        t2.explicacao = (
            f"RSI {r:.0f}".replace(".", ",")
            + " — ≤30 sobrevenda (8), ≤40 (5), ≤55 (2), acima (0). Sobrevenda indica queda forte recente, "
            "que pode continuar."
        )
    else:
        t2.explicacao = "menos de 15 sessões: não avaliado"
    c.append(t2)

    # T3 — tendência
    t3 = Criterio("T3", "Tendência (MM200 e MACD)", "tecnico", 15)
    partes = []
    if ind.mm200 is not None:
        t3.avaliado = True
        if ind.preco >= ind.mm200:
            t3.pontos += 8
            partes.append("acima da média de 200 sessões (+8)")
        else:
            partes.append("abaixo da média de 200 sessões (0)")
    if ind.macd_hist is not None:
        t3.avaliado = True
        if ind.macd_hist > 0:
            t3.pontos += 7
            partes.append("MACD positivo (+7)")
        elif ind.macd_hist_anterior is not None and ind.macd_hist > ind.macd_hist_anterior:
            t3.pontos += 4
            partes.append("MACD negativo, mas melhorando (+4)")
        else:
            partes.append("MACD negativo (0)")
    t3.explicacao = "; ".join(partes) if partes else "menos de 200 sessões e sem MACD: não avaliado"
    if t3.avaliado and ind.mm200 is None:
        t3.maximo = 7  # só o MACD pôde ser avaliado: a parte da MM200 não conta na cobertura
        t3.explicacao += "; MM200 indisponível (histórico < 200 sessões)"
    c.append(t3)

    # A1 — potencial até o preço-alvo
    a1 = Criterio("A1", "Potencial até o preço-alvo médio", "analistas", 25)
    potencial = None
    if ext.preco_alvo and ext.preco_alvo > 0 and not ext.cripto:
        potencial = (ext.preco_alvo / ind.preco - 1) * 100
        a1.avaliado = True
        a1.pontos = 25 * _linear(potencial, 0, 30)
        a1.explicacao = (
            f"alvo médio {_num(ext.preco_alvo)} ({ext.fonte_alvo}) = {_pct(potencial)} sobre o preço atual; "
            "25 pts a partir de +30%, 0 se ≤ 0. Projeção de analistas, não garantia."
        )
    else:
        a1.explicacao = (
            "cripto: não há preço-alvo de analistas"
            if ext.cripto
            else "sem preço-alvo da fonte (chave ausente, plano ou ETF)"
        )
    c.append(a1)

    # A2 — consenso
    a2 = Criterio("A2", "Consenso dos analistas", "analistas", 15)
    if ext.pct_compra is not None and ext.n_analistas >= MIN_ANALISTAS:
        a2.avaliado = True
        a2.pontos = 15 * _linear(ext.pct_compra, 40, 85)
        if (ext.pct_venda or 0) >= 20:
            a2.pontos = max(0.0, a2.pontos - 5)
        a2.explicacao = (
            f"{ext.pct_compra:.0f}% compra, {ext.pct_venda or 0:.0f}% venda "
            f"entre {ext.n_analistas} analistas "
            f"({ext.fonte_analistas}); 15 pts com ≥85% compra, 0 com ≤40%; −5 se venda ≥ 20%"
        )
    elif ext.recomendacao_media is not None and ext.n_analistas >= 3:
        a2.avaliado = True
        a2.pontos = 15 * _linear(ext.recomendacao_media, 3.0, 1.5)
        a2.explicacao = (
            f"recomendação média {ext.recomendacao_media:.2f} entre {ext.n_analistas} analistas "
            f"({ext.fonte_analistas}; 1 = compra forte … 5 = venda); 15 pts com ≤ 1,5, 0 com ≥ 3"
        ).replace(".", ",", 1)
    elif ext.n_analistas:
        a2.explicacao = f"só {ext.n_analistas} analista(s): mínimo de {MIN_ANALISTAS} para avaliar"
    else:
        a2.explicacao = "cripto: sem cobertura de analistas" if ext.cripto else "sem recomendações da fonte"
    c.append(a2)

    # F1–F3 — fundamentos
    f1 = Criterio("F1", "Margem líquida", "fundamentos", 8)
    f2 = Criterio("F2", "Retorno sobre o patrimônio (ROE)", "fundamentos", 8)
    f3 = Criterio("F3", "Preço/lucro (P/L)", "fundamentos", 9)
    nao_aplica = "cripto/ETF: não se aplica" if (ext.cripto or ext.etf) else "sem ficha da empresa"
    if ext.margem is not None and not ext.cripto:
        f1.avaliado = True
        m = ext.margem * 100
        f1.pontos = 8 if m > 10 else 5 if m > 0 else 0
        f1.explicacao = f"margem {m:.1f}%".replace(".", ",") + " — >10% (8), >0 (5), prejuízo (0)"
    else:
        f1.explicacao = nao_aplica
    if ext.roe is not None and not ext.cripto:
        f2.avaliado = True
        r = ext.roe * 100
        f2.pontos = 8 if r >= 20 else 5 if r >= 10 else 2 if r > 0 else 0
        f2.explicacao = f"ROE {r:.1f}%".replace(".", ",") + " — ≥20% (8), ≥10% (5), >0 (2)"
    else:
        f2.explicacao = nao_aplica
    if ext.pl is not None and not ext.cripto and not ext.etf:
        f3.avaliado = True
        pl = ext.pl
        f3.pontos = 9 if 0 < pl <= 20 else 6 if 0 < pl <= 30 else 3 if 0 < pl <= 45 else 0
        f3.explicacao = (
            f"P/L {pl:.1f}".replace(".", ",") + " — até 20 (9), até 30 (6), até 45 (3), acima ou negativo (0)"
        )
    else:
        f3.explicacao = nao_aplica
    c.extend([f1, f2, f3])
    if ext.fonte_fundamentos:
        for f in (f1, f2, f3):
            if f.avaliado:
                f.explicacao += f" ({ext.fonte_fundamentos})"

    total = round(sum(x.pontos for x in c))
    cobertura = sum(x.maximo for x in c if x.avaliado)

    risco, motivos = _risco(ind, ext)
    tec = sum(x.pontos for x in c if x.grupo == "tecnico")
    outros = sum(x.pontos for x in c if x.grupo != "tecnico")
    if outros >= tec and outros > 0:
        perspectiva = "médio/longo prazo"
        motivo_persp = "pesaram mais preço-alvo/consenso/fundamentos (horizonte típico de 12 meses)"
    else:
        perspectiva = "curto prazo"
        motivo_persp = "pesaram mais sinais técnicos, que mudam em semanas"
    obs = []
    if cobertura <= LIMIAR_TOP10:
        obs.append("só leitura técnica avaliada: fundamentos e analistas não entram — fora do Top 10")
    return Score(total, cobertura, c, risco, motivos, perspectiva, motivo_persp, potencial, obs)


def _risco(ind: Indicadores, ext: DadosExternos) -> tuple[str, list[str]]:
    niveis = ["baixo", "médio", "alto"]
    motivos: list[str] = []
    vol = ind.vol90 if ind.vol90 is not None else ind.vol30
    if vol is None:
        nivel = 1
        motivos.append("volatilidade não calculável (histórico curto): risco tratado como médio")
    else:
        nivel = 0 if vol < 25 else 1 if vol < 45 else 2
        motivos.append(f"volatilidade anualizada de 90 sessões {vol:.0f}%".replace(".", ","))
    extras = []
    if ext.dias_para_resultado is not None and 0 <= ext.dias_para_resultado <= 7:
        extras.append(f"resultado trimestral em {ext.dias_para_resultado} dia(s)")
    if ind.tendencia == "baixa":
        extras.append("tendência de baixa (preço < MM50 < MM200)")
    if ind.variacao_5d is not None and ind.variacao_5d <= -15:
        extras.append(f"queda de {abs(ind.variacao_5d):.0f}% em uma semana".replace(".", ","))
    if (ext.pct_venda or 0) >= 30:
        extras.append(f"{ext.pct_venda:.0f}% dos analistas recomendam venda")
    if ind.vol30 and ind.vol180 and ind.vol30 > 1.5 * ind.vol180:
        extras.append("volatilidade recente acima de 1,5× a média de 6 meses")
    if extras:
        nivel = min(2, nivel + 1)
        motivos.extend(extras)
    return niveis[nivel], motivos
