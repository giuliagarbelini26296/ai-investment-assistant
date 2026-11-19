"""
Twelve Data — ações dos EUA (USD).

Cotação: GET /quote?symbol=A,B&apikey=...  → um objeto por símbolo (ou o objeto direto para 1 símbolo).
Histórico: GET /time_series?symbol=A&interval=1day&outputsize=N&apikey=...

Homologado em 30/10/2026 com captura real (TTWO, plano Basic): /quote devolve `close`, `previous_close`,
`currency`, `is_market_open` e `timestamp`; /time_series devolve `values[]` com datetime/open/high/low/
close/volume (fechamento do dia já presente após o encerramento).

Observação da captura: `timestamp` do /quote é o INÍCIO do candle diário (09:30 New York), não o horário
da última negociação — com o mercado fechado às 17:57 ET veio 13:30Z. Por isso NÃO o usamos como
`horario_mercado` (ele faria todas as cotações do dia colidirem na deduplicação por horário e rotularia
o fechamento como "das 09:30"). A cotação fica com horário de mercado desconhecido; a atualidade usa o
horário da coleta e o calendário da bolsa. O valor bruto continua em `bruto.timestamp_candle`.

Plano Basic (gratuito, uso pessoal): 8 req/min, 800 créditos/dia. Atraso não declarado pela fonte
para o plano Basic → cotações rotuladas "atraso não declarado".
Erros vêm em HTTP 200 com corpo {"code": 401|429|400, "message": "...", "status": "error"}.
"""

from __future__ import annotations

import datetime as dt

from apps.marketdata.models import QuoteType, TimeBasis

from .base import (
    AuthError,
    BaseAdapter,
    CandleNormalizado,
    CotacaoNormalizada,
    FonteInfo,
    InvalidResponseError,
    RateLimitError,
    ResultadoCotacao,
    decimal_ou_none,
)

_NY = "America/New_York"


class TwelveDataAdapter(BaseAdapter):
    base_url = "https://api.twelvedata.com"
    moeda_cotacao = "USD"
    base_tempo = TimeBasis.LOCAL_SESSION
    info = FonteInfo(
        codigo="twelvedata",
        nome="Twelve Data (ações EUA)",
        tipo_dado=QuoteType.UNDECLARED,
        atraso_declarado_min=None,
        atraso_declarado_texto="não declarado pela fonte para o plano Basic",
        limites="Basic: 8 req/min, 800 créditos/dia (1 crédito por símbolo, a confirmar)",
        termos_url="https://twelvedata.com/pricing",
        homologada=True,
        observacoes=(
            "Homologada em 30/10/2026 (captura real, plano Basic). O 'timestamp' do /quote é o início do "
            "candle diário, não a última negociação: o horário de mercado fica como não informado e a "
            "atualidade usa o horário da coleta e o calendário da bolsa."
        ),
        limite_dia=800,
        limite_minuto=8,
    )

    def _params(self, **extra) -> dict:
        p = dict(extra)
        if self.api_key:
            p["apikey"] = self.api_key
        return p

    @staticmethod
    def _erro_no_corpo(corpo) -> None:
        if isinstance(corpo, dict) and corpo.get("status") == "error":
            codigo = int(corpo.get("code") or 0)
            msg = str(corpo.get("message", "erro"))[:200]
            if codigo == 401:
                raise AuthError(f"twelvedata: {msg}")
            if codigo == 429:
                raise RateLimitError(f"twelvedata: {msg}")
            raise InvalidResponseError(f"twelvedata: {msg}")

    def cotar(self, simbolos: list[str]) -> ResultadoCotacao:
        if not simbolos:
            return ResultadoCotacao()
        resposta = self._get(f"{self.base_url}/quote", params=self._params(symbol=",".join(simbolos)))
        corpo = self._json(resposta)
        self._erro_no_corpo(corpo)
        if not isinstance(corpo, dict):
            raise InvalidResponseError("twelvedata: resposta inesperada")
        # 1 símbolo → objeto direto; N símbolos → {SIMBOLO: objeto}
        por_simbolo = {simbolos[0].upper(): corpo} if "symbol" in corpo else corpo
        coleta = self.agora()
        brutas: list[CotacaoNormalizada] = []
        falhas: dict[str, str] = {}
        for pedido in simbolos:
            chave = pedido.upper()
            item = por_simbolo.get(chave)
            if not isinstance(item, dict):
                falhas[chave] = "ausente na resposta (resposta parcial)"
                continue
            if item.get("status") == "error":
                falhas[chave] = str(item.get("message", "erro"))[:200]
                continue
            preco = decimal_ou_none(item.get("close"))
            if preco is None:
                falhas[chave] = "sem 'close' na resposta"
                continue
            ts = item.get("timestamp")
            inicio_candle = (
                dt.datetime.fromtimestamp(int(ts), tz=dt.UTC).isoformat()
                if isinstance(ts, int | float | str) and str(ts).isdigit()
                else None
            )
            brutas.append(
                CotacaoNormalizada(
                    simbolo=chave,
                    preco=preco,
                    moeda=str(item.get("currency") or ""),
                    horario_coleta=coleta,
                    # a fonte não informa o horário da última negociação (ver docstring)
                    horario_mercado=None,
                    fechamento_anterior=decimal_ou_none(item.get("previous_close")),
                    variacao_24h_pct=None,
                    fonte=self.info.codigo,
                    fonte_versao="quote",
                    tipo=self.info.tipo_dado,
                    atraso_declarado_min=None,
                    bruto={
                        "close": item.get("close"),
                        "currency": item.get("currency"),
                        "is_market_open": item.get("is_market_open"),
                        "timestamp_candle": inicio_candle,
                    },
                )
            )
        return self._finalizar(brutas, falhas)

    def historico(self, simbolo: str, dias: int = 30) -> list[CandleNormalizado]:
        resposta = self._get(
            f"{self.base_url}/time_series",
            params=self._params(symbol=simbolo, interval="1day", outputsize=max(dias, 1), timezone=_NY),
        )
        corpo = self._json(resposta)
        self._erro_no_corpo(corpo)
        valores = corpo.get("values") if isinstance(corpo, dict) else None
        if not isinstance(valores, list):
            raise InvalidResponseError("twelvedata: série sem 'values'")
        candles: list[CandleNormalizado] = []
        for ponto in valores:
            close = decimal_ou_none(ponto.get("close"))
            try:
                data = dt.date.fromisoformat(str(ponto.get("datetime"))[:10])
            except ValueError:
                continue
            if close is None or close <= 0:
                continue
            candles.append(
                CandleNormalizado(
                    simbolo=simbolo.upper(),
                    data=data,
                    base_tempo=self.base_tempo,
                    open=decimal_ou_none(ponto.get("open")),
                    high=decimal_ou_none(ponto.get("high")),
                    low=decimal_ou_none(ponto.get("low")),
                    close=close,
                    volume=decimal_ou_none(ponto.get("volume")),
                    fonte=self.info.codigo,
                    provisorio=False,
                )
            )
        candles.sort(key=lambda c: c.data)
        return candles
