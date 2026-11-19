"""Registro de adaptadores por fonte e por mercado."""

from __future__ import annotations

from django.conf import settings

from .base import AdapterError, BaseAdapter, BaseFxAdapter, FonteInfo
from .binance import BinanceAdapter
from .brapi import BrapiAdapter
from .demo import DemoAdapter, DemoFxAdapter
from .fx import AwesomeApiAdapter, PtaxBcbAdapter
from .inteligencia import AlphaVantageAdapter, FinnhubAdapter, FocusBcbAdapter, SgsBcbAdapter
from .twelvedata import TwelveDataAdapter

ADAPTADORES: dict[str, type[BaseAdapter]] = {
    "twelvedata": TwelveDataAdapter,
    "binance": BinanceAdapter,
    "brapi": BrapiAdapter,
    "ptax_bcb": PtaxBcbAdapter,
    "awesomeapi": AwesomeApiAdapter,
    "finnhub": FinnhubAdapter,
    "alphavantage": AlphaVantageAdapter,
    "focus_bcb": FocusBcbAdapter,
    "sgs_bcb": SgsBcbAdapter,
    "demo": DemoAdapter,
    "demo_fx": DemoFxAdapter,
}

# Fonte primária por mercado (proposta v1.1, seção 1). O modo demo substitui todas.
FONTE_POR_MERCADO = {"NYSE": "twelvedata", "NASDAQ": "twelvedata", "CRIPTO": "binance", "B3": "brapi"}
FONTE_CAMBIO_FECHAMENTO = "ptax_bcb"
FONTE_CAMBIO_INTRADIARIO = "awesomeapi"


# Fontes que não respondem sem credencial (401). Sem chave, a tarefa do mercado fica pausada em vez de falhar.
FONTES_COM_CHAVE_OBRIGATORIA = {
    "twelvedata": "TWELVEDATA_API_KEY",
    "finnhub": "FINNHUB_API_KEY",
    "alphavantage": "ALPHAVANTAGE_API_KEY",
}
# Fontes de inteligência de mercado (opcionais): sem chave, as seções ficam "indisponível".
FONTES_INTELIGENCIA = ("finnhub", "alphavantage", "focus_bcb", "sgs_bcb")


def chave_da_fonte(codigo: str) -> str | None:
    return (settings.FONTES.get("CHAVES") or {}).get(codigo) or None


def fonte_cotacao(mercado: str) -> str | None:
    """Fonte das COTAÇÕES do mercado. Ações dos EUA podem usar a Finnhub (FONTE_COTACAO_EUA=finnhub);
    candles diários continuam na fonte do mercado (FONTE_POR_MERCADO)."""
    if mercado in ("NYSE", "NASDAQ"):
        escolhida = (settings.FONTES.get("FONTE_COTACAO_EUA") or "").strip().lower()
        if escolhida == "finnhub":
            return "finnhub"
    return FONTE_POR_MERCADO.get(mercado)


def fonte_sem_chave(mercado: str) -> str | None:
    """Nome da variável de ambiente que falta para coletar o mercado, ou None se a fonte está pronta."""
    if settings.PORTAL["MODO_DEMO"]:
        return None
    codigo = fonte_cotacao(mercado)
    variavel = FONTES_COM_CHAVE_OBRIGATORIA.get(codigo)
    if variavel and not chave_da_fonte(codigo):
        return variavel
    return None


def chave_ausente(codigo: str) -> str | None:
    """Variável de ambiente que falta para a fonte, ou None (fonte pública ou chave presente)."""
    variavel = FONTES_COM_CHAVE_OBRIGATORIA.get(codigo)
    if variavel and not chave_da_fonte(codigo):
        return variavel
    return None


def criar_adaptador(codigo: str, **kwargs) -> BaseAdapter:
    try:
        cls = ADAPTADORES[codigo]
    except KeyError as exc:
        raise AdapterError(f"fonte desconhecida: {codigo}") from exc
    if codigo.startswith("demo"):
        return cls(**kwargs)
    return cls(api_key=chave_da_fonte(codigo), **kwargs)


def adaptador_para_mercado(mercado: str, **kwargs) -> BaseAdapter:
    if settings.PORTAL["MODO_DEMO"]:
        return DemoAdapter(moeda="BRL" if mercado == "B3" else "USD", **kwargs)
    return criar_adaptador(FONTE_POR_MERCADO[mercado], **kwargs)


def adaptador_cotacoes(mercado: str, **kwargs) -> BaseAdapter:
    """Adaptador do ciclo de cotações (pode diferir da fonte de candles; ver fonte_cotacao)."""
    if settings.PORTAL["MODO_DEMO"]:
        return DemoAdapter(moeda="BRL" if mercado == "B3" else "USD", **kwargs)
    return criar_adaptador(fonte_cotacao(mercado), **kwargs)


def adaptador_cambio(tipo: str = "fechamento", **kwargs) -> BaseFxAdapter:
    if settings.PORTAL["MODO_DEMO"]:
        return DemoFxAdapter(**kwargs)
    codigo = FONTE_CAMBIO_FECHAMENTO if tipo == "fechamento" else FONTE_CAMBIO_INTRADIARIO
    return criar_adaptador(codigo, **kwargs)  # type: ignore[return-value]


def todas_as_fontes() -> list[FonteInfo]:
    return [cls.info for cls in ADAPTADORES.values()]


__all__ = [
    "ADAPTADORES",
    "FONTE_CAMBIO_FECHAMENTO",
    "FONTE_CAMBIO_INTRADIARIO",
    "FONTE_POR_MERCADO",
    "FONTES_INTELIGENCIA",
    "chave_ausente",
    "adaptador_cambio",
    "adaptador_cotacoes",
    "adaptador_para_mercado",
    "fonte_cotacao",
    "criar_adaptador",
    "fonte_sem_chave",
    "todas_as_fontes",
]
