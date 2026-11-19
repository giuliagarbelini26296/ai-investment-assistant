"""
Indicadores técnicos calculados só a partir de candles diários de UMA fonte (sem interpolação).

Convenções (fórmulas usuais, documentadas em docs/mercado.md):
- RSI(14) de Wilder; MACD(12, 26, 9) com médias exponenciais; médias móveis simples de 50 e 200 sessões;
- 52 semanas = candles dos últimos 365 dias corridos, mais o preço atual;
- suporte/resistência de N sessões = mínima/máxima das N sessões COMPLETAS anteriores (sem a de hoje);
- volatilidade = desvio-padrão dos retornos logarítmicos diários, anualizado (252 sessões; 365 para cripto);
- variação por período = preço atual vs. fechamento da última sessão em ou antes de (hoje − N dias).
Sem dados suficientes o indicador fica None e a tela mostra "dados insuficientes" — nunca estimado.

Este módulo não conhece Django: recebe listas e devolve números (float, apenas para indicadores;
preços exibidos continuam vindo das fontes com a precisão original).
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field

PERIODOS_DIAS = {"1d": 1, "7d": 7, "1m": 30, "3m": 91, "6m": 182, "12m": 365}
ROTULOS_PERIODO = {
    "1d": "1 dia",
    "7d": "7 dias",
    "1m": "1 mês",
    "3m": "3 meses",
    "6m": "6 meses",
    "12m": "12 meses",
}


@dataclass(frozen=True)
class Barra:
    data: dt.date
    close: float
    high: float | None = None
    low: float | None = None
    volume: float | None = None
    provisorio: bool = False


def sma(valores: list[float], n: int) -> float | None:
    if n <= 0 or len(valores) < n:
        return None
    return sum(valores[-n:]) / n


def ema_serie(valores: list[float], n: int) -> list[float]:
    if len(valores) < n:
        return []
    k = 2 / (n + 1)
    atual = sum(valores[:n]) / n
    saida = [atual]
    for v in valores[n:]:
        atual = v * k + atual * (1 - k)
        saida.append(atual)
    return saida


def rsi(fechamentos: list[float], n: int = 14) -> float | None:
    if len(fechamentos) < n + 1:
        return None
    ganhos, perdas = [], []
    for a, b in zip(fechamentos[:-1], fechamentos[1:], strict=False):
        d = b - a
        ganhos.append(max(d, 0.0))
        perdas.append(max(-d, 0.0))
    media_g = sum(ganhos[:n]) / n
    media_p = sum(perdas[:n]) / n
    for g, p in zip(ganhos[n:], perdas[n:], strict=False):
        media_g = (media_g * (n - 1) + g) / n
        media_p = (media_p * (n - 1) + p) / n
    if media_p == 0:
        return 100.0 if media_g > 0 else 50.0
    rs = media_g / media_p
    return 100 - 100 / (1 + rs)


def macd(fechamentos: list[float], rapida: int = 12, lenta: int = 26, sinal: int = 9):
    """(macd, sinal, histograma, histograma_anterior) ou None sem dados suficientes."""
    if len(fechamentos) < lenta + sinal + 1:
        return None
    e_rapida = ema_serie(fechamentos, rapida)
    e_lenta = ema_serie(fechamentos, lenta)
    desloc = lenta - rapida
    linha = [r - s for r, s in zip(e_rapida[desloc:], e_lenta, strict=False)]
    sinal_serie = ema_serie(linha, sinal)
    if len(sinal_serie) < 2:
        return None
    hist = [m - s for m, s in zip(linha[sinal - 1 :], sinal_serie, strict=False)]
    return linha[-1], sinal_serie[-1], hist[-1], hist[-2]


def volatilidade_anual(fechamentos: list[float], n: int, anual: int = 252) -> float | None:
    if len(fechamentos) < n + 1:
        return None
    janela = fechamentos[-(n + 1) :]
    retornos = [math.log(b / a) for a, b in zip(janela[:-1], janela[1:], strict=False) if a > 0 and b > 0]
    if len(retornos) < 2:
        return None
    media = sum(retornos) / len(retornos)
    var = sum((r - media) ** 2 for r in retornos) / (len(retornos) - 1)
    return math.sqrt(var) * math.sqrt(anual) * 100


def fechamento_base(barras: list[Barra], data_base: dt.date) -> Barra | None:
    """Última barra completa em ou antes da data-base (None se o histórico não chega lá)."""
    candidata = None
    for b in barras:
        if b.data <= data_base:
            candidata = b
        else:
            break
    return candidata


@dataclass
class Periodo:
    chave: str
    variacao_pct: float | None
    maxima: float | None
    minima: float | None
    volume_medio: float | None  # média diária (unidades)
    volume_financeiro: float | None  # média diária ≈ volume × fechamento (aproximação)
    base_data: dt.date | None


def periodo(
    barras: list[Barra], preco: float, hoje: dt.date, chave: str, variacao_dia: float | None = None
) -> Periodo:
    dias = PERIODOS_DIAS[chave]
    if chave == "1d":
        # "dia" = sessão mais recente (em andamento ou a última encerrada); base = a sessão anterior
        janela = barras[-1:]
        base = barras[-2] if len(barras) >= 2 else None
    else:
        base = fechamento_base(barras, hoje - dt.timedelta(days=dias))
        janela = [b for b in barras if b.data > (base.data if base else hoje - dt.timedelta(days=dias))]
    if chave == "1d" and variacao_dia is not None:
        var = variacao_dia
    elif base is not None and base.close > 0 and (chave == "1d" or barras and barras[0].data <= base.data):
        var = (preco / base.close - 1) * 100
    else:
        var = None
    altas = [b.high for b in janela if b.high is not None] + [preco]
    baixas = [b.low for b in janela if b.low is not None] + [preco]
    vols = [b.volume for b in janela if b.volume is not None]
    fin = [b.volume * b.close for b in janela if b.volume is not None]
    return Periodo(
        chave=chave,
        variacao_pct=var,
        maxima=max(altas) if janela else None,
        minima=min(baixas) if janela else None,
        volume_medio=sum(vols) / len(vols) if vols else None,
        volume_financeiro=sum(fin) / len(fin) if fin else None,
        base_data=base.data if base else None,
    )


@dataclass
class Indicadores:
    preco: float
    hoje: dt.date
    n_barras: int
    primeira_data: dt.date | None
    ultima_data: dt.date | None
    rsi14: float | None = None
    mm50: float | None = None
    mm200: float | None = None
    macd: float | None = None
    macd_sinal: float | None = None
    macd_hist: float | None = None
    macd_hist_anterior: float | None = None
    maxima_52s: float | None = None
    minima_52s: float | None = None
    janela_52s_dias: int = 0
    suporte20: float | None = None
    resistencia20: float | None = None
    vol90: float | None = None
    vol30: float | None = None
    vol180: float | None = None
    variacao_5d: float | None = None
    periodos: dict[str, Periodo] = field(default_factory=dict)

    @property
    def dist_minima_52s_pct(self) -> float | None:
        if not self.minima_52s:
            return None
        return (self.preco / self.minima_52s - 1) * 100

    @property
    def dist_maxima_52s_pct(self) -> float | None:
        if not self.maxima_52s:
            return None
        return (self.preco / self.maxima_52s - 1) * 100

    @property
    def janela_52s_completa(self) -> bool:
        return self.janela_52s_dias >= 300

    @property
    def tendencia(self) -> str:
        """alta: preço > MM50 > MM200; baixa: preço < MM50 < MM200; senão lateral (ou indefinida)."""
        if self.mm50 is None or self.mm200 is None:
            return "indefinida"
        if self.preco > self.mm50 > self.mm200:
            return "alta"
        if self.preco < self.mm50 < self.mm200:
            return "baixa"
        return "lateral"


def suporte_resistencia(barras: list[Barra], hoje: dt.date, n: int) -> tuple[float | None, float | None]:
    completas = [b for b in barras if not b.provisorio and b.data < hoje][-n:]
    if len(completas) < n:
        return None, None
    lows = [b.low for b in completas if b.low is not None]
    highs = [b.high for b in completas if b.high is not None]
    return (min(lows) if lows else None, max(highs) if highs else None)


def calcular(
    barras: list[Barra], preco: float, hoje: dt.date, cripto: bool = False, variacao_dia: float | None = None
) -> Indicadores:
    barras = sorted(barras, key=lambda b: b.data)
    # fechamentos com o preço atual no lugar do candle em andamento (ou somado, sem candle de hoje)
    fech = [b.close for b in barras if not (b.provisorio or b.data >= hoje)] + [preco]
    ind = Indicadores(
        preco=preco,
        hoje=hoje,
        n_barras=len(barras),
        primeira_data=barras[0].data if barras else None,
        ultima_data=barras[-1].data if barras else None,
    )
    ind.rsi14 = rsi(fech)
    ind.mm50 = sma(fech, 50)
    ind.mm200 = sma(fech, 200)
    m = macd(fech)
    if m:
        ind.macd, ind.macd_sinal, ind.macd_hist, ind.macd_hist_anterior = m
    anual = 365 if cripto else 252
    ind.vol30 = volatilidade_anual(fech, 30, anual)
    ind.vol90 = volatilidade_anual(fech, 90, anual)
    ind.vol180 = volatilidade_anual(fech, 180, anual)
    ano = [b for b in barras if b.data >= hoje - dt.timedelta(days=365)]
    if ano:
        ind.janela_52s_dias = (hoje - ano[0].data).days
        altas = [b.high if b.high is not None else b.close for b in ano] + [preco]
        baixas = [b.low if b.low is not None else b.close for b in ano] + [preco]
        ind.maxima_52s, ind.minima_52s = max(altas), min(baixas)
    ind.suporte20, ind.resistencia20 = suporte_resistencia(barras, hoje, 20)
    base5 = fechamento_base(barras, hoje - dt.timedelta(days=7))
    if base5 and base5.close > 0:
        ind.variacao_5d = (preco / base5.close - 1) * 100
    for chave in PERIODOS_DIAS:
        ind.periodos[chave] = periodo(barras, preco, hoje, chave, variacao_dia)
    return ind
