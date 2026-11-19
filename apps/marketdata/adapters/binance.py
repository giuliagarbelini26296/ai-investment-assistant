"""
Binance — criptomoedas (API pública de mercado, sem chave).

Cotação: GET /api/v3/ticker/24hr?symbols=["BTCUSDT","BTCBRL"]  (ou ?symbol=BTCUSDT)
  → {"symbol","lastPrice","prevClosePrice","priceChangePercent","closeTime"(ms), ...}
Candles: GET /api/v3/klines?symbol=BTCUSDT&interval=1d&limit=N
  → [[openTime, open, high, low, close, volume, closeTime, ...], ...]

Formato conforme a documentação pública (https://developers.binance.com); o endpoint não pôde ser
acessado do ambiente de desenvolvimento. NÃO HOMOLOGADO até captura real.
Limites por peso de requisição (cabeçalhos X-MBX-USED-WEIGHT-*); 429 com Retry-After; 418 = IP banido.
Par em USDT é rotulado USDT (≠ USD); par em BRL é preço BRL da exchange.
"""

from __future__ import annotations

import datetime as dt
import json

from apps.marketdata.models import QuoteType, TimeBasis

from .base import (
    BaseAdapter,
    CandleNormalizado,
    CotacaoNormalizada,
    FonteInfo,
    InvalidResponseError,
    RateLimitError,
    ResultadoCotacao,
    decimal_ou_none,
)

MOEDA_POR_SUFIXO = (("USDT", "USDT"), ("BRL", "BRL"), ("USDC", "USD"), ("USD", "USD"))


def moeda_do_par(par: str) -> str | None:
    for sufixo, moeda in MOEDA_POR_SUFIXO:
        if par.upper().endswith(sufixo):
            return moeda
    return None


class BinanceAdapter(BaseAdapter):
    base_url = "https://api.binance.com"
    moeda_cotacao = "USDT"  # a validação por par usa `moeda_do_par`
    base_tempo = TimeBasis.UTC_DAY
    info = FonteInfo(
        codigo="binance",
        nome="Binance (API pública de mercado)",
        tipo_dado=QuoteType.SPOT,
        atraso_declarado_min=0,
        atraso_declarado_texto="preço spot da exchange, com horário informado pela fonte (closeTime)",
        limites="limites por peso de requisição por IP (ver documentação); 429 com Retry-After",
        termos_url="https://www.binance.com/en/terms",
        homologada=True,
        observacoes=(
            "Homologada em 30/10/2026 (captura real BTCUSDT/BTCBRL): ticker/24hr com lastPrice, closeTime "
            "(1-2 s de idade informada) e priceChangePercent; klines 1d com o candle do dia provisório."
        ),
    )

    def _verificar_status(self, resposta):
        if resposta.status_code == 418:
            raise RateLimitError("binance: IP temporariamente banido (418)", status=418, retry_after=None)
        super()._verificar_status(resposta)

    def cotar(self, simbolos: list[str]) -> ResultadoCotacao:
        if not simbolos:
            return ResultadoCotacao()
        pares = [s.upper() for s in simbolos]
        resposta = self._get(
            f"{self.base_url}/api/v3/ticker/24hr",
            params={"symbols": json.dumps(pares, separators=(",", ":"))},
        )
        corpo = self._json(resposta)
        if isinstance(corpo, dict) and "code" in corpo:
            raise InvalidResponseError(f"binance: {corpo.get('msg', 'erro')} (código {corpo.get('code')})")
        if isinstance(corpo, dict):
            corpo = [corpo]
        if not isinstance(corpo, list):
            raise InvalidResponseError("binance: resposta inesperada")
        coleta = self.agora()
        brutas: list[CotacaoNormalizada] = []
        falhas: dict[str, str] = {}
        vistos: set[str] = set()
        for item in corpo:
            par = str(item.get("symbol") or "").upper()
            if not par:
                continue
            vistos.add(par)
            preco = decimal_ou_none(item.get("lastPrice"))
            if preco is None:
                falhas[par] = "sem lastPrice"
                continue
            moeda = moeda_do_par(par)
            if moeda is None:
                falhas[par] = "moeda de cotação do par não reconhecida"
                continue
            close_ms = item.get("closeTime")
            horario = dt.datetime.fromtimestamp(int(close_ms) / 1000, tz=dt.UTC) if close_ms else None
            brutas.append(
                CotacaoNormalizada(
                    simbolo=par,
                    preco=preco,
                    moeda=moeda,
                    horario_coleta=coleta,
                    horario_mercado=horario,
                    fechamento_anterior=decimal_ou_none(item.get("prevClosePrice")),
                    variacao_24h_pct=decimal_ou_none(item.get("priceChangePercent")),
                    fonte=self.info.codigo,
                    fonte_versao="v3/ticker/24hr",
                    tipo=self.info.tipo_dado,
                    atraso_declarado_min=0,
                    bruto={k: item.get(k) for k in ("lastPrice", "closeTime", "priceChangePercent")},
                )
            )
        for par in pares:
            if par not in vistos:
                falhas[par] = "ausente na resposta (resposta parcial)"
        return self._finalizar_por_par(brutas, falhas)

    def _finalizar_por_par(self, brutas, falhas) -> ResultadoCotacao:
        # a moeda esperada varia por par (USDT/BRL), então valida-se contra a própria moeda do par
        agora = self.agora()
        from .base import validar_cotacao

        resultado = ResultadoCotacao(falhas=dict(falhas))
        for cot in brutas:
            motivo = validar_cotacao(cot, cot.moeda, agora)
            if motivo:
                resultado.falhas[cot.simbolo] = motivo
            else:
                resultado.cotacoes.append(cot)
        return resultado

    def historico(self, simbolo: str, dias: int = 30) -> list[CandleNormalizado]:
        resposta = self._get(
            f"{self.base_url}/api/v3/klines",
            params={"symbol": simbolo.upper(), "interval": "1d", "limit": max(1, min(dias, 1000))},
        )
        corpo = self._json(resposta)
        if isinstance(corpo, dict) and "code" in corpo:
            raise InvalidResponseError(f"binance: {corpo.get('msg', 'erro')} (código {corpo.get('code')})")
        if not isinstance(corpo, list):
            raise InvalidResponseError("binance: klines inesperado")
        agora_ms = int(self.agora().timestamp() * 1000)
        candles: list[CandleNormalizado] = []
        for k in corpo:
            if not isinstance(k, list) or len(k) < 7:
                continue
            close = decimal_ou_none(k[4])
            if close is None or close <= 0:
                continue
            candles.append(
                CandleNormalizado(
                    simbolo=simbolo.upper(),
                    data=dt.datetime.fromtimestamp(int(k[0]) / 1000, tz=dt.UTC).date(),
                    base_tempo=self.base_tempo,
                    open=decimal_ou_none(k[1]),
                    high=decimal_ou_none(k[2]),
                    low=decimal_ou_none(k[3]),
                    close=close,
                    volume=decimal_ou_none(k[5]),
                    fonte=self.info.codigo,
                    provisorio=int(k[6]) > agora_ms,  # closeTime ainda no futuro = dia em andamento
                )
            )
        return candles
