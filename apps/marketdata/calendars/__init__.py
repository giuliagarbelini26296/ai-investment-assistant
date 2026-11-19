"""
Calendários de negociação.

- Bolsas (NYSE/Nasdaq, B3): dias úteis menos feriados versionados em `feriados.json`, horário local
  da bolsa com fuso (o horário de verão americano desloca a sessão em São Paulo: 10h30–17h ou 11h30–18h;
  o Brasil não tem horário de verão).
- Cripto: 24/7; a "sessão" é o dia UTC.
- Ano sem feriados cadastrados → estado "desconhecido" (alertas suspensos).
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

ARQUIVO_FERIADOS = Path(__file__).with_name("feriados.json")


@dataclass(frozen=True)
class Sessao:
    data: dt.date
    abertura: dt.datetime
    fechamento: dt.datetime

    def contem(self, instante: dt.datetime) -> bool:
        return self.abertura <= instante < self.fechamento


@dataclass(frozen=True)
class Situacao:
    estado: str  # aberto | fechado | desconhecido
    sessao: Sessao | None  # sessão em curso (aberto) ou última encerrada (fechado)


class CalendarioBolsa:
    def __init__(
        self, codigo: str, fuso: str, abertura: str, fechamento: str, feriados: dict, antecipados: dict, periodos=()
    ):
        self.codigo = codigo
        self.tz = ZoneInfo(fuso)
        self.abre = dt.time.fromisoformat(abertura)
        self.fecha = dt.time.fromisoformat(fechamento)
        self.feriados = {dt.date.fromisoformat(k): v for k, v in feriados.items()}
        self.antecipados = {
            dt.date.fromisoformat(k): dt.time.fromisoformat(v) for k, v in antecipados.items()
        }
        self.periodos = tuple(periodos)
        self.anos_cobertos = {d.year for d in self.feriados}

    def cobre(self, data: dt.date) -> bool:
        return data.year in self.anos_cobertos

    def eh_pregao(self, data: dt.date) -> bool:
        return data.weekday() < 5 and data not in self.feriados

    def sessao(self, data: dt.date) -> Sessao | None:
        if not self.eh_pregao(data):
            return None
        fecha = self.fecha
        for periodo in self.periodos:
            if periodo["inicio"] <= data.isoformat() <= periodo["fim"]:
                fecha = dt.time.fromisoformat(periodo["fechamento"])
        fecha = self.antecipados.get(data, fecha)
        return Sessao(
            data=data,
            abertura=dt.datetime.combine(data, self.abre, tzinfo=self.tz),
            fechamento=dt.datetime.combine(data, fecha, tzinfo=self.tz),
        )

    def situacao(self, agora: dt.datetime) -> Situacao:
        local = agora.astimezone(self.tz)
        if not self.cobre(local.date()):
            return Situacao("desconhecido", None)
        hoje = self.sessao(local.date())
        if hoje and hoje.contem(local):
            return Situacao("aberto", hoje)
        return Situacao("fechado", self.ultima_sessao_encerrada(agora))

    def ultima_sessao_encerrada(self, agora: dt.datetime) -> Sessao | None:
        local = agora.astimezone(self.tz)
        data = local.date()
        for _ in range(15):
            s = self.sessao(data)
            if s and s.fechamento <= local:
                return s
            data -= dt.timedelta(days=1)
        return None

    def proxima_abertura(self, agora: dt.datetime) -> dt.datetime | None:
        local = agora.astimezone(self.tz)
        data = local.date()
        for _ in range(15):
            s = self.sessao(data)
            if s and s.abertura > local:
                return s.abertura
            data += dt.timedelta(days=1)
        return None


class CalendarioCripto:
    codigo = "CRIPTO"
    tz = dt.UTC

    def cobre(self, data: dt.date) -> bool:
        return True

    def eh_pregao(self, data: dt.date) -> bool:
        return True

    def sessao(self, data: dt.date) -> Sessao:
        inicio = dt.datetime.combine(data, dt.time(0), tzinfo=dt.UTC)
        return Sessao(data=data, abertura=inicio, fechamento=inicio + dt.timedelta(days=1))

    def situacao(self, agora: dt.datetime) -> Situacao:
        return Situacao("aberto", self.sessao(agora.astimezone(dt.UTC).date()))

    def ultima_sessao_encerrada(self, agora: dt.datetime) -> Sessao:
        return self.sessao(agora.astimezone(dt.UTC).date() - dt.timedelta(days=1))

    def proxima_abertura(self, agora: dt.datetime) -> dt.datetime:
        return agora


@lru_cache(maxsize=1)
def _carregar() -> dict:
    return json.loads(ARQUIVO_FERIADOS.read_text(encoding="utf-8"))


@lru_cache(maxsize=8)
def calendario(mercado: str) -> CalendarioBolsa | CalendarioCripto:
    chave = {"NYSE": "NYSE", "NASDAQ": "NYSE", "B3": "B3", "CRIPTO": "CRIPTO"}[mercado]
    if chave == "CRIPTO":
        return CalendarioCripto()
    cfg = _carregar()[chave]
    return CalendarioBolsa(
        chave, cfg["fuso"], cfg["abertura"], cfg["fechamento"], cfg["feriados"], cfg["fechamento_antecipado"], cfg.get("periodos", ())
    )
