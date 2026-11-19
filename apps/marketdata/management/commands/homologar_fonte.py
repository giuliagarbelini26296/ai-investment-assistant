"""
Homologação de uma fonte: chamada real, resultado normalizado e captura bruta para fixture.

Uso (na máquina com acesso à internet e chaves no .env):
    manage.py homologar_fonte twelvedata TTWO AAPL
    manage.py homologar_fonte binance BTCUSDT BTCBRL
    manage.py homologar_fonte brapi PETR4
    manage.py homologar_fonte ptax_bcb            # câmbio de fechamento mais recente
    manage.py homologar_fonte awesomeapi          # câmbio intradiário
    manage.py homologar_fonte finnhub AAPL        # analistas (e por instituição), alvo, notícias, resultados
    manage.py homologar_fonte alphavantage AAPL   # ficha OVERVIEW (gasta 1 das 25 requisições do dia)
    manage.py homologar_fonte focus_bcb           # expectativas Focus (câmbio, Selic, IPCA)
    manage.py homologar_fonte sgs_bcb             # Selic meta, IPCA mensal e em 12 meses
    manage.py homologar_fonte brapi PETR4 --fundamentos   # + estatísticas, dados financeiros, dividendos
    --historico N   também busca N candles diários do primeiro símbolo
    --salvar DIR    grava a resposta bruta (sem chaves) em DIR/<fonte>-<data>.json

Nada é gravado no banco. Chaves nunca aparecem na saída.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
from pathlib import Path

import httpx
from django.core.management.base import BaseCommand, CommandError

from apps.marketdata.adapters import ADAPTADORES, criar_adaptador
from apps.marketdata.adapters.base import AdapterError, BaseFxAdapter
from apps.marketdata.adapters.brapi import BrapiAdapter
from apps.marketdata.adapters.inteligencia import (
    AlphaVantageAdapter,
    FinnhubAdapter,
    FocusBcbAdapter,
    SgsBcbAdapter,
)


def _passo(saida: dict, nome: str, func) -> None:
    """Executa uma etapa da homologação; erro de uma etapa não interrompe as demais."""
    try:
        saida[nome] = func()
    except AdapterError as exc:
        saida[nome] = {"erro": {"tipo": exc.kind, "mensagem": str(exc), "status": exc.status}}


class _Gravador(httpx.BaseTransport):
    """Transporte que registra as respostas brutas para a fixture."""

    def __init__(self):
        self.inner = httpx.HTTPTransport()
        self.capturas: list[dict] = []

    def handle_request(self, request):
        inicio = dt.datetime.now(dt.UTC)
        resposta = self.inner.handle_request(request)
        corpo = resposta.read()
        url = str(request.url)
        for segredo in ("apikey", "token", "x-api-key"):
            if segredo in url.lower():
                url = url.split("?")[0] + "?<parametros omitidos>"
        try:
            payload = json.loads(corpo)
        except ValueError:
            payload = corpo.decode("utf-8", "replace")[:2000]
        self.capturas.append(
            {
                "requested_at": inicio.isoformat(),
                "url": url,
                "http_status": resposta.status_code,
                "elapsed_seconds": (dt.datetime.now(dt.UTC) - inicio).total_seconds(),
                "payload": payload,
            }
        )
        return httpx.Response(resposta.status_code, headers=resposta.headers, content=corpo, request=request)


def _json_default(o):
    if dataclasses.is_dataclass(o):
        return dataclasses.asdict(o)
    if isinstance(o, dt.datetime | dt.date):
        return o.isoformat()
    return str(o)


class Command(BaseCommand):
    help = "Chamada real a uma fonte para homologação (sem gravar no banco)."

    def add_arguments(self, parser):
        parser.add_argument("fonte", choices=sorted(k for k in ADAPTADORES if not k.startswith("demo")))
        parser.add_argument("simbolos", nargs="*")
        parser.add_argument("--historico", type=int, default=0)
        parser.add_argument("--salvar", type=str, default="")
        parser.add_argument(
            "--fundamentos", action="store_true", help="brapi: estatísticas, dados e dividendos"
        )

    def handle(self, *args, **opts):
        gravador = _Gravador()
        client = httpx.Client(transport=gravador, timeout=10.0)
        adaptador = criar_adaptador(opts["fonte"], client=client)
        inicio = dt.datetime.now(dt.UTC)
        saida: dict = {"fonte": adaptador.info.codigo, "info": dataclasses.asdict(adaptador.info)}
        try:
            if isinstance(adaptador, FinnhubAdapter | AlphaVantageAdapter) and not opts["simbolos"]:
                raise CommandError("informe ao menos um símbolo")
            if isinstance(adaptador, FocusBcbAdapter):
                _passo(saida, "focus", lambda: adaptador.expectativas(("Câmbio", "Selic", "IPCA"), 12))
            elif isinstance(adaptador, SgsBcbAdapter):
                for codigo in SgsBcbAdapter.SERIES:
                    _passo(saida, f"sgs_{codigo}", lambda codigo=codigo: adaptador.serie(codigo, 3))
            elif isinstance(adaptador, AlphaVantageAdapter):
                _passo(saida, "ficha", lambda: adaptador.ficha(opts["simbolos"][0]))
                _passo(saida, "dividendos", lambda: adaptador.dividendos(opts["simbolos"][0])[:5])
            elif isinstance(adaptador, FinnhubAdapter):
                s0 = opts["simbolos"][0]
                hoje = inicio.date()
                _passo(saida, "recomendacoes", lambda: adaptador.recomendacoes(s0)[-3:])
                _passo(saida, "preco_alvo", lambda: adaptador.preco_alvo(s0))
                _passo(
                    saida,
                    "mudancas_recomendacao",
                    lambda: adaptador.mudancas_recomendacao(s0, hoje - dt.timedelta(days=90))[:5],
                )
                _passo(
                    saida,
                    "noticias",
                    lambda: adaptador.noticias_empresa(s0, hoje - dt.timedelta(days=7), hoje)[:5],
                )
                _passo(saida, "noticias_cambio", lambda: adaptador.noticias_gerais("forex")[:5])
                _passo(
                    saida, "resultados", lambda: adaptador.resultados(hoje, hoje + dt.timedelta(days=120), s0)
                )
                _passo(saida, "metricas", lambda: adaptador.ficha(s0))
            elif isinstance(adaptador, BaseFxAdapter):
                saida["cambio"] = adaptador.cambio()
            else:
                if not opts["simbolos"]:
                    raise CommandError("informe ao menos um símbolo")
                saida["cotacoes"] = adaptador.cotar(opts["simbolos"])
                if opts["historico"]:
                    saida["historico"] = adaptador.historico(opts["simbolos"][0], opts["historico"])
                if opts["fundamentos"] and isinstance(adaptador, BrapiAdapter):
                    s0 = opts["simbolos"][0]
                    _passo(saida, "estatisticas", lambda: adaptador.estatisticas(s0))
                    _passo(saida, "dados_financeiros", lambda: adaptador.dados_financeiros(s0))
                    _passo(saida, "proventos", lambda: adaptador.proventos(s0)[:5])
        except AdapterError as exc:
            saida["erro"] = {"tipo": exc.kind, "mensagem": str(exc), "status": exc.status}
        agora = dt.datetime.now(dt.UTC)
        for c in getattr(saida.get("cotacoes"), "cotacoes", []):
            if c.horario_mercado:
                c.bruto["idade_informada_s"] = (agora - c.horario_mercado).total_seconds()
        self.stdout.write(json.dumps(saida, default=_json_default, ensure_ascii=False, indent=2))
        self.stdout.write(
            "\nNOTA: 'idade_informada_s' mede timestamp da fonte vs. relógio local; não comprova atraso real."
        )
        if opts["salvar"]:
            pasta = Path(opts["salvar"])
            pasta.mkdir(parents=True, exist_ok=True)
            arquivo = pasta / f"{adaptador.info.codigo}-{inicio:%Y%m%d-%H%M%S}.json"
            arquivo.write_text(json.dumps(gravador.capturas, ensure_ascii=False, indent=2), encoding="utf-8")
            self.stdout.write(f"resposta bruta salva em {arquivo}")
