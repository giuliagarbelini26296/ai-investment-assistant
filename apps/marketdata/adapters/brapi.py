"""
brapi — ações da B3 (BRL).

Cotação: GET /api/v2/stocks/quote?symbols=A,B  (contrato validado com resposta real em 29/10/2026,
fixture `testes-fontes-dados-2026-10-29-b3.json`, teste `brapi_v2_quote`).
Histórico: GET /api/quote/{símbolo}?range=1mo&interval=1d (endpoint legado, também na fixture).
A documentação orienta v2 para integrações novas; o histórico v2 ainda não foi homologado.

Plano Gratuito: ~30 min de atraso declarado; 15.000 req/mês; 1 simultânea; histórico até 3 meses.
Sem token só respondem PETR4, VALE3, MGLU3 e ITUB4 (20 req/min por IP).

Planos (brapi.dev/pricing, lido em 02/11/2026): Gratuito — 1 ação por requisição, histórico de 3 meses,
atualização a cada 30 min, sem dividendos; Startup — 10 ações/requisição, 1 ano, dividendos de 1 ano;
Pro — 20 ações/requisição, 10+ anos, dividendos completos. Recurso fora do plano → HTTP 403.

Fundamentos (v2): /api/v2/stocks/statistics (P/L, P/VP, EV/EBITDA, dividend yield),
/api/v2/stocks/financial-data (ROE, crescimento da receita, EBITDA, dívida, caixa, preço-alvo,
recomendação), /api/v2/stocks/dividends (cashDividends: dividendos e JCP). Disponibilidade por plano não
confirmada: 403 vira "indisponível no plano" e o coletor não insiste por 7 dias.
"""

from __future__ import annotations

import datetime as dt
import time

from django.conf import settings

from apps.marketdata.models import QuoteType, TimeBasis

from .base import (
    AdapterError,
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
from .inteligencia import FichaEmpresa, PrecoAlvoNormalizado, ProventoNormalizado

FAIXAS = (("1mo", 31), ("3mo", 93), ("6mo", 186), ("1y", 370), ("2y", 740))
# Faixa fora do plano: a brapi pode responder 403 (recurso do plano), 400/422 (parâmetro fora do contrato)
# ou um corpo com "error". Nesses casos o histórico cai para 3 meses (máximo do Gratuito).
ESPERA_CONCORRENCIA_S = 2.0  # Gratuito: 1 requisição simultânea por conta; 429 → espera curta e repete


class BrapiAdapter(BaseAdapter):
    base_url = "https://brapi.dev"
    moeda_cotacao = "BRL"
    base_tempo = TimeBasis.LOCAL_SESSION
    info = FonteInfo(
        codigo="brapi",
        nome="brapi (B3)",
        tipo_dado=QuoteType.DELAYED,
        atraso_declarado_min=30,
        atraso_declarado_texto="aprox. 30 min no plano Gratuito (declarado pela fonte)",
        limites="Gratuito: 15.000 req/mês, 1 simultânea, histórico até 3 meses",
        termos_url="https://brapi.dev/pricing",
        homologada=False,
        observacoes=(
            "Cotação v2 validada com resposta real (29/10 e 02/11/2026, com token). No plano Gratuito, "
            "estatísticas, dados financeiros e dividendos respondem 403 (confirmado em 02/11/2026, WEGE3): "
            "fundamentos e proventos da B3 exigem Startup ou Pro."
        ),
        limite_mes=15000,
    )

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    @staticmethod
    def por_requisicao() -> int:
        """Ações por requisição: 1 no Gratuito (padrão), 10 no Startup, 20 no Pro.

        Configurável por BRAPI_SIMBOLOS_POR_REQUISICAO no .env.
        """
        try:
            return max(1, min(20, int(settings.FONTES.get("BRAPI_SIMBOLOS_POR_REQUISICAO") or 1)))
        except (TypeError, ValueError):
            return 1

    def cotar(self, simbolos: list[str]) -> ResultadoCotacao:
        if not simbolos:
            return ResultadoCotacao()
        n = self.por_requisicao()
        lotes = [simbolos[i : i + n] for i in range(0, len(simbolos), n)]
        total = ResultadoCotacao()
        ultimo_erro: AdapterError | None = None
        for lote in lotes:
            # Um ativo com problema (ticker inexistente, 422, 429 de concorrência) não derruba os demais.
            try:
                parcial = self._cotar_com_espera(lote)
            except AuthError as exc:
                ultimo_erro = exc
                motivo = "token brapi ausente ou plano sem acesso (token gratuito em brapi.dev)"
                total.falhas.update({s.upper(): motivo for s in lote})
                continue
            except AdapterError as exc:
                ultimo_erro = exc
                total.falhas.update({s.upper(): f"{exc.kind}: {exc}"[:200] for s in lote})
                continue
            total.cotacoes.extend(parcial.cotacoes)
            total.falhas.update(parcial.falhas)
        if not total.cotacoes and ultimo_erro is not None:
            raise ultimo_erro
        return total

    def _cotar_com_espera(self, lote: list[str]) -> ResultadoCotacao:
        try:
            return self._cotar_lote(lote)
        except RateLimitError as exc:
            # 429 por concorrência (outra chamada em andamento) passa logo; cota mensal esgotada não.
            if exc.retry_after and exc.retry_after > 10:
                raise
            time.sleep(exc.retry_after or ESPERA_CONCORRENCIA_S)
            return self._cotar_lote(lote)

    def _cotar_lote(self, simbolos: list[str]) -> ResultadoCotacao:
        resposta = self._get(
            f"{self.base_url}/api/v2/stocks/quote",
            params={"symbols": ",".join(simbolos)},
            headers=self._headers(),
        )
        corpo = self._json(resposta)
        if isinstance(corpo, dict) and corpo.get("error"):
            raise InvalidResponseError(f"brapi: {corpo.get('message', 'erro')}")
        resultados = corpo.get("results") if isinstance(corpo, dict) else None
        if not isinstance(resultados, list):
            raise InvalidResponseError("brapi: resposta sem 'results'")
        coleta = self.agora()
        brutas: list[CotacaoNormalizada] = []
        falhas: dict[str, str] = {}
        vistos: set[str] = set()
        for item in resultados:
            pedido = (item.get("requestedSymbol") or item.get("symbol") or "").upper()
            dados = item.get("data") or {}
            if not pedido:
                continue
            vistos.add(pedido)
            if item.get("changed") and item.get("symbol") and item["symbol"].upper() != pedido:
                # Conversão de ticker: exige revisão manual; não altera a carteira.
                falhas[pedido] = f"símbolo convertido pela fonte para {item['symbol']} — revisar cadastro"
                continue
            preco = decimal_ou_none(dados.get("regularMarketPrice"))
            if preco is None:
                falhas[pedido] = "sem regularMarketPrice na resposta"
                continue
            brutas.append(
                CotacaoNormalizada(
                    simbolo=pedido,
                    preco=preco,
                    moeda=str(dados.get("currency") or ""),
                    horario_coleta=coleta,
                    horario_mercado=_iso(dados.get("regularMarketTime")),
                    fechamento_anterior=decimal_ou_none(dados.get("regularMarketPreviousClose")),
                    variacao_24h_pct=None,
                    fonte=self.info.codigo,
                    fonte_versao="v2",
                    tipo=self.info.tipo_dado,
                    atraso_declarado_min=self.info.atraso_declarado_min,
                    bruto={k: dados.get(k) for k in ("regularMarketPrice", "regularMarketTime", "currency")},
                )
            )
        for s in simbolos:
            if s.upper() not in vistos:
                falhas[s.upper()] = "ausente na resposta (resposta parcial)"
        return self._finalizar(brutas, falhas)

    aviso_plano = ""  # preenchido quando o plano limitou o histórico pedido

    def historico(self, simbolo: str, dias: int = 30) -> list[CandleNormalizado]:
        faixa = next((f for f, d in FAIXAS if dias <= d), "2y")
        self.aviso_plano = ""
        try:
            return self._historico(simbolo, faixa)
        except AdapterError as exc:
            fora_do_plano = isinstance(exc, AuthError | InvalidResponseError)
            if faixa in ("1mo", "3mo") or not fora_do_plano:
                raise
            # plano não cobre a faixa pedida (ex.: Gratuito = 3 meses): usa o máximo do plano gratuito
            self.aviso_plano = f"histórico limitado a 3 meses pelo plano da brapi (pedido: {faixa})"
            return self._historico(simbolo, "3mo")

    def _historico(self, simbolo: str, faixa: str) -> list[CandleNormalizado]:
        resposta = self._get(
            f"{self.base_url}/api/quote/{simbolo}",
            params={"range": faixa, "interval": "1d"},
            headers=self._headers(),
        )
        corpo = self._json(resposta)
        if corpo.get("error"):
            if corpo.get("code") == "MISSING_TOKEN":
                raise AuthError("brapi: token ausente")
            raise InvalidResponseError(f"brapi: {corpo.get('message', 'erro')}")
        try:
            serie = corpo["results"][0]["historicalDataPrice"]
        except (KeyError, IndexError, TypeError) as exc:
            raise InvalidResponseError("brapi: histórico ausente na resposta") from exc
        hoje_sp = _hoje_sao_paulo(self.agora())
        candles: list[CandleNormalizado] = []
        for ponto in serie:
            close = decimal_ou_none(ponto.get("close"))
            if close is None or close <= 0 or "date" not in ponto:
                continue
            data = _data_sao_paulo(int(ponto["date"]))
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
                    provisorio=data >= hoje_sp,
                )
            )
        return candles

    # --- fundamentos e proventos (v2) ----------------------------------------------------------------

    def _v2(self, recurso: str, simbolo: str) -> dict:
        resposta = self._get(
            f"{self.base_url}/api/v2/stocks/{recurso}", params={"symbols": simbolo}, headers=self._headers()
        )
        corpo = self._json(resposta)
        if isinstance(corpo, dict) and corpo.get("error"):
            if corpo.get("code") in ("MISSING_TOKEN", "UNAUTHORIZED", "FORBIDDEN"):
                raise AuthError(f"brapi: {corpo.get('message', 'sem acesso')}")
            raise InvalidResponseError(f"brapi: {corpo.get('message', 'erro')}")
        resultados = corpo.get("results") if isinstance(corpo, dict) else None
        if not isinstance(resultados, list) or not resultados:
            raise InvalidResponseError(f"brapi: {recurso} sem 'results'")
        item = resultados[0]
        dados = item.get("data") if isinstance(item.get("data"), dict) else item
        return dados or {}

    def estatisticas(self, simbolo: str) -> FichaEmpresa:
        d = self._v2("statistics", simbolo)
        return FichaEmpresa(
            simbolo=simbolo.upper(),
            fonte=self.info.codigo,
            pl=decimal_ou_none(d.get("trailingPE")),
            pl_projetado=decimal_ou_none(d.get("forwardPE")),
            pvp=decimal_ou_none(d.get("priceToBook")),
            ev_ebitda=decimal_ou_none(d.get("enterpriseToEbitda")),
            dividend_yield=_fracao(d.get("dividendYield")),
            valor_mercado=decimal_ou_none(d.get("marketCap")),
            bruto={k: d.get(k) for k in ("trailingPE", "priceToBook", "enterpriseToEbitda", "dividendYield")},
        )

    def dados_financeiros(self, simbolo: str) -> FichaEmpresa:
        d = self._v2("financial-data", simbolo)
        ebitda = decimal_ou_none(d.get("ebitda"))
        divida = decimal_ou_none(d.get("totalDebt"))
        caixa = decimal_ou_none(d.get("totalCash"))
        div_liq = None
        if ebitda and ebitda > 0 and divida is not None and caixa is not None:
            div_liq = (divida - caixa) / ebitda
        media = decimal_ou_none(d.get("targetMeanPrice"))
        alvo = None
        if media and media > 0:
            alvo = PrecoAlvoNormalizado(
                simbolo=simbolo.upper(),
                fonte=self.info.codigo,
                media=media,
                mediana=decimal_ou_none(d.get("targetMedianPrice")),
                maximo=decimal_ou_none(d.get("targetHighPrice")),
                minimo=decimal_ou_none(d.get("targetLowPrice")),
            )
        n = d.get("numberOfAnalystOpinions")
        return FichaEmpresa(
            simbolo=simbolo.upper(),
            fonte=self.info.codigo,
            roe=_fracao(d.get("returnOnEquity"), ja_fracao=True),
            margem_liquida=_fracao(d.get("profitMargins"), ja_fracao=True),
            crescimento_receita=_fracao(d.get("revenueGrowth"), ja_fracao=True),
            divida_liquida_ebitda=div_liq,
            divida_liquida_calculada=div_liq is not None,
            recomendacao_chave=str(d.get("recommendationKey") or "")[:20],
            recomendacao_media=decimal_ou_none(d.get("recommendationMean")),
            n_analistas=n if isinstance(n, int) else None,
            preco_alvo=alvo,
            bruto={
                k: d.get(k) for k in ("returnOnEquity", "revenueGrowth", "ebitda", "totalDebt", "totalCash")
            },
        )

    def proventos(self, simbolo: str) -> list[ProventoNormalizado]:
        d = self._v2("dividends", simbolo)
        itens = d.get("cashDividends")
        if itens is None and isinstance(d.get("dividendsData"), dict):
            itens = d["dividendsData"].get("cashDividends")
        if not isinstance(itens, list):
            raise InvalidResponseError("brapi: dividendos sem 'cashDividends'")
        saida = []
        for item in itens:
            valor = decimal_ou_none(item.get("rate"))
            if valor is None or valor <= 0:
                continue
            saida.append(
                ProventoNormalizado(
                    simbolo=simbolo.upper(),
                    fonte=self.info.codigo,
                    tipo=str(item.get("label") or "DIVIDENDO").upper()[:30],
                    valor=valor,
                    moeda="BRL",
                    data_com=_data_iso(item.get("lastDatePrior") or item.get("exDate")),
                    data_pagamento=_data_iso(item.get("paymentDate")),
                    data_aprovacao=_data_iso(item.get("approvedOn")),
                )
            )
        return saida


def _fracao(valor, ja_fracao: bool = False):
    """Normaliza percentuais: 0,03 = 3 %. Valor > 1 é lido como ponto percentual (salvo `ja_fracao`)."""
    d = decimal_ou_none(valor)
    if d is None:
        return None
    if not ja_fracao and abs(d) > 1:
        return d / 100
    return d


def _data_iso(valor) -> dt.date | None:
    if not valor:
        return None
    try:
        return dt.date.fromisoformat(str(valor)[:10])
    except ValueError:
        return None


def _iso(valor) -> dt.datetime | None:
    if not valor:
        return None
    try:
        return dt.datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except ValueError:
        return None


_SP = dt.timezone(dt.timedelta(hours=-3))  # B3 opera em America/Sao_Paulo (sem horário de verão)


def _data_sao_paulo(epoch: int) -> dt.date:
    return dt.datetime.fromtimestamp(epoch, tz=_SP).date()


def _hoje_sao_paulo(agora: dt.datetime) -> dt.date:
    return agora.astimezone(_SP).date()
