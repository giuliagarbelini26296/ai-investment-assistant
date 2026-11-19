"""
Estado de atualidade de cada ativo, a partir da última cotação válida, do calendário e da agenda.

Estados (proposta v1.1, seção 8):
  atual                 — fonte declara tempo real e a coleta está dentro do intervalo
  atrasado              — fonte declara atraso (ex.: 30 min) e a coleta está dentro do intervalo
  nao_declarado         — fonte não declara atraso; coleta dentro do intervalo
  spot                  — cripto: preço spot recente (até 2 intervalos)
  mercado_fechado       — bolsa fechada e a cotação corresponde ao fechamento da última sessão
  desatualizado         — coleta vencida (falha, cota, coletor parado) ou fechamento ainda não obtido
  desconhecido          — calendário sem cobertura para a data (alertas suspensos)
  sem_cotacao           — nunca houve cotação válida
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from apps.portfolio.models import Asset

from .calendars import calendario
from .collector import INTERVALO
from .models import Quote, QuoteType, RecordStatus

TOLERANCIA = dt.timedelta(minutes=2)
IDADE_MAX_CRIPTO = 2 * INTERVALO
# Ações dos EUA só acompanhadas (Mercado/Favoritos): rodízio de candles diários a cada ~1 h.
INTERVALO_HORARIO = dt.timedelta(minutes=60)
TOLERANCIA_HORARIA = dt.timedelta(minutes=20)

ROTULOS = {
    "atual": "tempo real (declarado pela fonte)",
    "atrasado": "atrasado",
    "nao_declarado": "atraso não declarado pela fonte",
    "spot": "spot",
    "mercado_fechado": "mercado fechado",
    "desatualizado": "desatualizado",
    "desconhecido": "estado desconhecido",
    "sem_cotacao": "sem cotação",
}


@dataclass(frozen=True)
class Atualidade:
    estado: str
    descricao: str
    cotacao: Quote | None
    permite_alertas: bool

    @property
    def rotulo(self) -> str:
        if self.cotacao is not None and self.cotacao.source.startswith("demo"):
            return "demonstração"
        return ROTULOS[self.estado]


def ultima_cotacao(asset: Asset) -> Quote | None:
    return (
        Quote.objects.filter(asset=asset, status=RecordStatus.VALID).order_by("-collected_at", "-id").first()
    )


def atualidade(asset: Asset, agora: dt.datetime, cotacao: Quote | None = None) -> Atualidade:
    q = cotacao if cotacao is not None else ultima_cotacao(asset)
    if q is None:
        return Atualidade("sem_cotacao", "nenhuma cotação válida obtida", None, False)
    a = _atualidade(asset, agora, q)
    if q.source.startswith("demo"):
        return Atualidade(a.estado, f"DEMONSTRAÇÃO — dado sintético; {a.descricao}", q, a.permite_alertas)
    return a


def sessao_informada(asset: Asset, q: Quote) -> dt.date | None:
    """Use provider/candle metadata, never collection time as the trading session."""
    if asset.market in ('CRIPTO', 'CAMBIO'):
        return None
    raw = q.raw or {}
    try:
        if raw.get('candle'):
            return dt.date.fromisoformat(raw['candle'])
        if raw.get('timestamp_candle'):
            return dt.datetime.fromisoformat(raw['timestamp_candle']).astimezone(calendario(asset.market).tz).date()
    except (TypeError, ValueError):
        return None
    return q.market_time.astimezone(calendario(asset.market).tz).date() if q.market_time else None


def _atualidade(asset: Asset, agora: dt.datetime, q: Quote) -> Atualidade:
    idade_coleta = agora - q.collected_at
    quando = f"coleta {_fmt(q.collected_at)}"
    if q.market_time:
        quando += f", mercado {_fmt(q.market_time)}"
    if asset.market == "CAMBIO":
        return _atualidade_cambio(agora, q, idade_coleta, quando)
    cal = calendario(asset.market)
    sit = cal.situacao(agora)

    if asset.market == "CRIPTO":
        if idade_coleta <= IDADE_MAX_CRIPTO:
            return Atualidade("spot", f"spot {q.source} ({quando})", q, True)
        return Atualidade("desatualizado", f"última coleta há {_dur(idade_coleta)} ({quando})", q, False)

    if sit.estado == "desconhecido":
        return Atualidade("desconhecido", "calendário sem cobertura para a data; alertas suspensos", q, False)

    if q.source == "alpaca_iex":
        # A minute bar from one venue is never an official consolidated close.
        recent = q.market_time and dt.timedelta(0) <= agora-q.market_time <= dt.timedelta(minutes=3)
        label = f"Alpaca · IEX parcial, barra de 1 min em USD ({quando}); não é cotação do BDR"
        if sit.estado == "aberto" and recent and idade_coleta <= dt.timedelta(minutes=3):
            return Atualidade("atual", label, q, True)
        return Atualidade("desatualizado", label + "; aguardando nova observação ou fonte periódica", q, False)

    informed = sessao_informada(asset, q)
    if informed and sit.sessao and informed < sit.sessao.data:
        return Atualidade('desatualizado',
            f'preço do pregão de {informed:%d/%m/%Y}; aguardando cotação do pregão de {sit.sessao.data:%d/%m/%Y} ({quando})', q, False)

    if sit.estado == "aberto" and asset.coleta_horaria:
        if idade_coleta <= INTERVALO_HORARIO + TOLERANCIA_HORARIA:
            return Atualidade(
                "nao_declarado",
                f"atualização ~1 h (candle diário em andamento, {q.source}; {quando})",
                q,
                True,
            )
        return Atualidade(
            "desatualizado", f"última coleta há {_dur(idade_coleta)} com pregão aberto ({quando})", q, False
        )

    if sit.estado == "aberto":
        from .collector import intervalo_da_tarefa

        intervalo = intervalo_da_tarefa("B3" if asset.market == "B3" else "NYSE")
        if idade_coleta <= intervalo + TOLERANCIA:
            if q.quote_type == QuoteType.REAL_TIME:
                return Atualidade("atual", f"{q.source} ({quando})", q, True)
            if q.quote_type == QuoteType.DELAYED:
                atraso = f"~{q.declared_delay_minutes} min" if q.declared_delay_minutes else "declarado"
                return Atualidade("atrasado", f"atraso {atraso} declarado por {q.source} ({quando})", q, True)
            return Atualidade("nao_declarado", f"{q.source} não declara atraso ({quando})", q, True)
        return Atualidade(
            "desatualizado", f"última coleta há {_dur(idade_coleta)} com pregão aberto ({quando})", q, False
        )

    # fechado
    sessao = sit.sessao
    if sessao is None:
        return Atualidade("desconhecido", "sem sessão encerrada conhecida", q, False)
    referencia = q.market_time or q.collected_at
    margem = dt.timedelta(minutes=(q.declared_delay_minutes or 0) + 60)
    if referencia >= sessao.fechamento - margem or q.collected_at >= sessao.fechamento:
        return Atualidade(
            "mercado_fechado", f"fechamento de {sessao.data:%d/%m/%Y} por {q.source} ({quando})", q, True
        )
    return Atualidade(
        "desatualizado", f"fechamento de {sessao.data:%d/%m/%Y} ainda não obtido ({quando})", q, False
    )


def _atualidade_cambio(agora: dt.datetime, q: Quote, idade: dt.timedelta, quando: str) -> Atualidade:
    """Dólar comercial (câmbio intradiário): coletado em dias úteis, 09h–18h de São Paulo."""
    from zoneinfo import ZoneInfo

    local = agora.astimezone(ZoneInfo("America/Sao_Paulo"))
    em_horario = local.weekday() < 5 and 9 <= local.hour < 18
    if idade <= INTERVALO + TOLERANCIA:
        return Atualidade("nao_declarado", f"comercial, {q.source} não declara atraso ({quando})", q, True)
    if not em_horario:
        return Atualidade(
            "mercado_fechado",
            f"fora do horário de coleta (09h–18h, dias úteis); último valor ({quando})",
            q,
            True,
        )
    return Atualidade(
        "desatualizado", f"última coleta há {_dur(idade)} em horário comercial ({quando})", q, False
    )


def _fmt(instante: dt.datetime) -> str:
    from zoneinfo import ZoneInfo

    return instante.astimezone(ZoneInfo("America/Sao_Paulo")).strftime("%d/%m %H:%M")


def _dur(delta: dt.timedelta) -> str:
    minutos = int(delta.total_seconds() // 60)
    if minutos < 60:
        return f"{minutos} min"
    horas, resto = divmod(minutos, 60)
    if horas < 48:
        return f"{horas} h {resto:02d} min"
    return f"{horas // 24} dias"
