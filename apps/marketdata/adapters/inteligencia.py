"""
Fontes de inteligência de mercado (opcionais): analistas, notícias, resultados, fundamentos e Focus.

Finnhub (chave gratuita, header X-Finnhub-Token):
  GET /stock/recommendation?symbol=AAPL      → [{period, strongBuy, buy, hold, sell, strongSell, symbol}]
  GET /stock/price-target?symbol=AAPL        → {targetMean, targetMedian, targetHigh, targetLow, lastUpdated}
                                               (pode exigir plano pago: 401/403 → "indisponível no plano")
  GET /company-news?symbol=AAPL&from=&to=    → [{datetime(epoch s), headline, source, summary, url}]
  GET /news?category=forex|general           → mesma forma, sem símbolo
  GET /calendar/earnings?from=&to=[&symbol=] → {earningsCalendar: [{date, hour, epsEstimate, epsActual, ...}]}
  GET /stock/metric?symbol=AAPL&metric=all   → {metric: {52WeekHigh, 52WeekLow, peTTM, roeTTM, ...}}

Alpha Vantage (chave gratuita, 25 requisições/dia):
  GET /query?function=OVERVIEW&symbol=AAPL   → ficha com AnalystTargetPrice, AnalystRating*, PERatio,
                                               ProfitMargin, ReturnOnEquityTTM, Sector, 52WeekHigh/Low
  Cota e chave inválida vêm em HTTP 200 com {"Information"|"Note"|"Error Message": "..."}.

Focus — Banco Central (dados abertos, sem chave):
  GET olinda.bcb.gov.br/olinda/servico/Expectativas/versao/v1/odata/ExpectativasMercadoAnuais
      ?$filter=Indicador eq 'Câmbio'&$orderby=Data desc&$format=json
  → {value: [{Indicador, Data, DataReferencia, Media, Mediana, DesvioPadrao, Minimo, Maximo,
              numeroRespondentes, baseCalculo}]}

Formatos conforme documentação pública das fontes. NÃO HOMOLOGADAS até captura real com
`manage.py homologar_fonte` na máquina do usuário; até lá a interface rotula os dados como "fonte não
homologada". Nenhum adaptador grava no banco.
"""

from __future__ import annotations

import datetime as dt
import threading
import time
from dataclasses import dataclass, field
from decimal import Decimal
from urllib.parse import quote, urlencode

from apps.marketdata.models import QuoteType

from .base import (
    AdapterError,
    AuthError,
    BaseAdapter,
    CotacaoNormalizada,
    FonteInfo,
    InvalidResponseError,
    RateLimitError,
    ResultadoCotacao,
    decimal_ou_none,
)


@dataclass
class RecomendacaoNormalizada:
    simbolo: str
    periodo: dt.date
    strong_buy: int
    buy: int
    hold: int
    sell: int
    strong_sell: int
    fonte: str


@dataclass
class MudancaRecomendacao:
    """Ação de um analista identificado (instituição) sobre a recomendação de um ativo."""

    simbolo: str
    data: dt.date
    instituicao: str
    de: str
    para: str
    acao: str  # up | down | main | init | reit (como a fonte informa)
    fonte: str


ACOES_ANALISTA = {
    "up": "elevou",
    "down": "rebaixou",
    "main": "manteve",
    "init": "iniciou cobertura",
    "reit": "reiterou",
}


@dataclass
class PrecoAlvoNormalizado:
    simbolo: str
    fonte: str
    media: Decimal | None = None
    mediana: Decimal | None = None
    maximo: Decimal | None = None
    minimo: Decimal | None = None
    data_referencia: dt.date | None = None


@dataclass
class NoticiaNormalizada:
    titulo: str
    url: str
    publicada_em: dt.datetime
    fonte: str
    veiculo: str = ""
    resumo: str = ""
    simbolo: str = ""
    sentimento: Decimal | None = None
    sentimento_rotulo: str = ""


@dataclass
class ResultadoTrimestral:
    simbolo: str
    data: dt.date
    fonte: str
    horario: str = ""
    eps_estimado: Decimal | None = None
    eps_real: Decimal | None = None
    receita_estimada: Decimal | None = None
    receita_real: Decimal | None = None
    trimestre: int | None = None
    ano: int | None = None


@dataclass
class FichaEmpresa:
    simbolo: str
    fonte: str
    setor: str = ""
    industria: str = ""
    pl: Decimal | None = None
    pl_projetado: Decimal | None = None
    margem_liquida: Decimal | None = None  # fração (0,25 = 25 %)
    roe: Decimal | None = None  # fração
    valor_mercado: Decimal | None = None
    dividend_yield: Decimal | None = None
    maxima_52s: Decimal | None = None
    minima_52s: Decimal | None = None
    ultimo_trimestre: dt.date | None = None
    pvp: Decimal | None = None
    ev_ebitda: Decimal | None = None
    crescimento_receita: Decimal | None = None  # fração
    divida_liquida_ebitda: Decimal | None = None
    divida_liquida_calculada: bool = False
    recomendacao_chave: str = ""
    recomendacao_media: Decimal | None = None  # 1 = compra forte … 5 = venda (convenção da fonte)
    n_analistas: int | None = None
    recomendacao: RecomendacaoNormalizada | None = None
    preco_alvo: PrecoAlvoNormalizado | None = None
    bruto: dict = field(default_factory=dict)


@dataclass
class ProventoNormalizado:
    simbolo: str
    fonte: str
    tipo: str
    valor: Decimal
    moeda: str
    data_com: dt.date | None = None
    data_pagamento: dt.date | None = None
    data_aprovacao: dt.date | None = None


@dataclass
class ExpectativaFocus:
    indicador: str
    data: dt.date
    data_referencia: str
    base_calculo: int
    media: Decimal | None
    mediana: Decimal | None
    minimo: Decimal | None
    maximo: Decimal | None
    desvio: Decimal | None
    respondentes: int | None


def _data(valor) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(valor)[:10])
    except (TypeError, ValueError):
        return None


def _inteiro(valor) -> int:
    try:
        return max(0, int(valor))
    except (TypeError, ValueError):
        return 0


def _num_av(valor) -> Decimal | None:
    """Alpha Vantage usa 'None', '-' e '0' como ausência em vários campos."""
    if valor in (None, "", "None", "-", "null"):
        return None
    return decimal_ou_none(valor)


class FinnhubAdapter(BaseAdapter):
    base_url = "https://finnhub.io/api/v1"
    info = FonteInfo(
        codigo="finnhub",
        nome="Finnhub (analistas, notícias, resultados)",
        tipo_dado=QuoteType.UNDECLARED,
        atraso_declarado_min=None,
        atraso_declarado_texto="dados de referência (não são cotações)",
        limites="gratuito: 60 chamadas/min (o portal usa até 50); alguns endpoints exigem plano pago",
        atribuicao="Cotações opcionais de ações dos EUA (FONTE_COTACAO_EUA=finnhub); atraso a confirmar",
        termos_url="https://finnhub.io/terms-of-service",
        homologada=True,
        observacoes=(
            "Homologada em 02/11/2026 (chamada real, TTWO, plano gratuito): recomendações, notícias por "
            "empresa e de câmbio, calendário de resultados e métricas respondem 200; preço-alvo responde 403 "
            "(exige plano pago). Valor de mercado em milhões de US$. Mudanças de recomendação por "
            "instituição (/stock/upgrade-downgrade) a confirmar no plano gratuito. Cotações (/quote) como "
            "fonte opcional ainda sem medição de atraso."
        ),
        limite_minuto=50,
    )

    moeda_cotacao = "USD"

    def _headers(self) -> dict:
        return {"X-Finnhub-Token": self.api_key} if self.api_key else {}

    def cotar(self, simbolos: list[str]) -> ResultadoCotacao:
        """GET /quote?symbol=X → {c, d, dp, h, l, o, pc, t}; uma chamada por símbolo (ações dos EUA)."""
        brutas: list[CotacaoNormalizada] = []
        falhas: dict[str, str] = {}
        ultimo_erro: AdapterError | None = None
        for s in simbolos:
            chave = s.upper()
            try:
                corpo = self._consultar("/quote", symbol=chave)
            except (AuthError, RateLimitError):
                raise
            except AdapterError as exc:
                ultimo_erro = exc
                falhas[chave] = f"{exc.kind}: {exc}"[:200]
                continue
            preco = decimal_ou_none(corpo.get("c")) if isinstance(corpo, dict) else None
            if not preco:
                falhas[chave] = "sem cotação na Finnhub para o símbolo"
                continue
            ts = corpo.get("t")
            horario = (
                dt.datetime.fromtimestamp(int(ts), tz=dt.UTC) if isinstance(ts, int) and ts > 0 else None
            )
            brutas.append(
                CotacaoNormalizada(
                    simbolo=chave,
                    preco=preco,
                    moeda="USD",
                    horario_coleta=self.agora(),
                    horario_mercado=horario,
                    fechamento_anterior=decimal_ou_none(corpo.get("pc")),
                    variacao_24h_pct=None,
                    fonte=self.info.codigo,
                    fonte_versao="quote",
                    tipo=self.info.tipo_dado,
                    atraso_declarado_min=None,
                    bruto={k: corpo.get(k) for k in ("c", "pc", "h", "l", "t")},
                )
            )
        if not brutas and ultimo_erro is not None:
            raise ultimo_erro
        return self._finalizar(brutas, falhas)

    def _consultar(self, caminho: str, **params):
        if not self.api_key:
            raise AuthError("finnhub: FINNHUB_API_KEY vazia no .env")
        resposta = self._get(f"{self.base_url}{caminho}", params=params, headers=self._headers())
        corpo = self._json(resposta)
        if isinstance(corpo, dict) and corpo.get("error"):
            msg = str(corpo["error"])[:200]
            if "access" in msg.lower() or "premium" in msg.lower():
                raise AuthError(f"finnhub: {msg}")
            if "limit" in msg.lower():
                raise RateLimitError(f"finnhub: {msg}")
            raise InvalidResponseError(f"finnhub: {msg}")
        return corpo

    def recomendacoes(self, simbolo: str) -> list[RecomendacaoNormalizada]:
        corpo = self._consultar("/stock/recommendation", symbol=simbolo)
        if not isinstance(corpo, list):
            raise InvalidResponseError("finnhub: recomendações em formato inesperado")
        saida = []
        for item in corpo:
            periodo = _data(item.get("period"))
            if periodo is None:
                continue
            saida.append(
                RecomendacaoNormalizada(
                    simbolo=simbolo.upper(),
                    periodo=periodo,
                    strong_buy=_inteiro(item.get("strongBuy")),
                    buy=_inteiro(item.get("buy")),
                    hold=_inteiro(item.get("hold")),
                    sell=_inteiro(item.get("sell")),
                    strong_sell=_inteiro(item.get("strongSell")),
                    fonte=self.info.codigo,
                )
            )
        return sorted(saida, key=lambda r: r.periodo)

    def preco_alvo(self, simbolo: str) -> PrecoAlvoNormalizado | None:
        corpo = self._consultar("/stock/price-target", symbol=simbolo)
        if not isinstance(corpo, dict):
            raise InvalidResponseError("finnhub: preço-alvo em formato inesperado")
        alvo = PrecoAlvoNormalizado(
            simbolo=simbolo.upper(),
            fonte=self.info.codigo,
            media=decimal_ou_none(corpo.get("targetMean")),
            mediana=decimal_ou_none(corpo.get("targetMedian")),
            maximo=decimal_ou_none(corpo.get("targetHigh")),
            minimo=decimal_ou_none(corpo.get("targetLow")),
            data_referencia=_data(corpo.get("lastUpdated")),
        )
        if not alvo.media or alvo.media <= 0:
            return None
        return alvo

    def mudancas_recomendacao(self, simbolo: str, desde: dt.date) -> list[MudancaRecomendacao]:
        """GET /stock/upgrade-downgrade?symbol=X&from=AAAA-MM-DD → instituição, de → para, ação, data.

        Pode exigir plano pago (como o preço-alvo): 403 vira AuthError e o coletor suspende a consulta.
        """
        corpo = self._consultar("/stock/upgrade-downgrade", symbol=simbolo, **{"from": desde.isoformat()})
        if not isinstance(corpo, list):
            raise InvalidResponseError("finnhub: mudanças de recomendação em formato inesperado")
        saida = []
        for item in corpo:
            ts, firma = item.get("gradeTime"), (item.get("company") or "").strip()
            if not firma or not str(ts).isdigit():
                continue
            saida.append(
                MudancaRecomendacao(
                    simbolo=(item.get("symbol") or simbolo).upper(),
                    data=dt.datetime.fromtimestamp(int(ts), dt.UTC).date(),
                    instituicao=firma[:80],
                    de=(item.get("fromGrade") or "").strip()[:40],
                    para=(item.get("toGrade") or "").strip()[:40],
                    acao=(item.get("action") or "").strip().lower()[:10],
                    fonte=self.info.codigo,
                )
            )
        return saida

    def _noticias(self, corpo, simbolo: str = "") -> list[NoticiaNormalizada]:
        if not isinstance(corpo, list):
            raise InvalidResponseError("finnhub: notícias em formato inesperado")
        saida = []
        for item in corpo:
            url, titulo, ts = item.get("url"), (item.get("headline") or "").strip(), item.get("datetime")
            if not url or not titulo or not str(ts).isdigit():
                continue
            saida.append(
                NoticiaNormalizada(
                    titulo=titulo[:400],
                    url=str(url)[:600],
                    publicada_em=dt.datetime.fromtimestamp(int(ts), tz=dt.UTC),
                    fonte=self.info.codigo,
                    veiculo=str(item.get("source") or "")[:120],
                    resumo=str(item.get("summary") or "")[:1500],
                    simbolo=simbolo.upper(),
                )
            )
        return saida

    def noticias_empresa(self, simbolo: str, de: dt.date, ate: dt.date) -> list[NoticiaNormalizada]:
        corpo = self._consultar(
            "/company-news", symbol=simbolo, **{"from": de.isoformat(), "to": ate.isoformat()}
        )
        return self._noticias(corpo, simbolo)

    def noticias_gerais(self, categoria: str = "forex") -> list[NoticiaNormalizada]:
        return self._noticias(self._consultar("/news", category=categoria))

    def resultados(self, de: dt.date, ate: dt.date, simbolo: str | None = None) -> list[ResultadoTrimestral]:
        params = {"from": de.isoformat(), "to": ate.isoformat()}
        if simbolo:
            params["symbol"] = simbolo
        corpo = self._consultar("/calendar/earnings", **params)
        itens = corpo.get("earningsCalendar") if isinstance(corpo, dict) else None
        if not isinstance(itens, list):
            raise InvalidResponseError("finnhub: calendário de resultados em formato inesperado")
        saida = []
        for item in itens:
            data = _data(item.get("date"))
            if data is None or not item.get("symbol"):
                continue
            saida.append(
                ResultadoTrimestral(
                    simbolo=str(item["symbol"]).upper(),
                    data=data,
                    fonte=self.info.codigo,
                    horario=str(item.get("hour") or "")[:10],
                    eps_estimado=decimal_ou_none(item.get("epsEstimate")),
                    eps_real=decimal_ou_none(item.get("epsActual")),
                    receita_estimada=decimal_ou_none(item.get("revenueEstimate")),
                    receita_real=decimal_ou_none(item.get("revenueActual")),
                    trimestre=item.get("quarter") if isinstance(item.get("quarter"), int) else None,
                    ano=item.get("year") if isinstance(item.get("year"), int) else None,
                )
            )
        return saida

    def ficha(self, simbolo: str) -> FichaEmpresa:
        corpo = self._consultar("/stock/metric", symbol=simbolo, metric="all")
        m = corpo.get("metric") if isinstance(corpo, dict) else None
        if not isinstance(m, dict):
            raise InvalidResponseError("finnhub: métricas em formato inesperado")

        def pct(*chaves):  # Finnhub informa margens e ROE em pontos percentuais
            for c in chaves:
                v = decimal_ou_none(m.get(c))
                if v is not None:
                    return v / 100
            return None

        def num(*chaves):
            for c in chaves:
                v = decimal_ou_none(m.get(c))
                if v is not None:
                    return v
            return None

        return FichaEmpresa(
            simbolo=simbolo.upper(),
            fonte=self.info.codigo,
            pl=num("peTTM", "peBasicExclExtraTTM", "peExclExtraTTM"),
            margem_liquida=pct("netProfitMarginTTM", "netProfitMarginAnnual"),
            roe=pct("roeTTM", "roeRfy"),
            valor_mercado=(num("marketCapitalization") or 0) * 1_000_000
            or None,  # a Finnhub informa em milhões
            dividend_yield=pct("dividendYieldIndicatedAnnual", "currentDividendYieldTTM"),
            maxima_52s=num("52WeekHigh"),
            minima_52s=num("52WeekLow"),
            pvp=num("pbQuarterly", "pbAnnual", "pb"),
            ev_ebitda=num("evEbitdaTTM", "currentEv/ebitdaTTM", "enterpriseValueEbitdaTTM"),
            crescimento_receita=pct("revenueGrowthQuarterlyYoy", "revenueGrowthTTMYoy"),
            bruto={k: m.get(k) for k in ("52WeekHigh", "52WeekLow", "peTTM", "roeTTM", "netProfitMarginTTM")},
        )


class AlphaVantageAdapter(BaseAdapter):
    base_url = "https://www.alphavantage.co/query"
    info = FonteInfo(
        codigo="alphavantage",
        nome="Alpha Vantage (ficha da empresa e preço-alvo)",
        tipo_dado=QuoteType.UNDECLARED,
        atraso_declarado_min=None,
        atraso_declarado_texto="dados de referência atualizados pela fonte (não são cotações)",
        limites="gratuito: 25 requisições/dia (o portal usa até 20, rodízio semanal por ativo)",
        termos_url="https://www.alphavantage.co/terms_of_service/",
        homologada=False,
        observacoes=(
            "Opcional (ALPHAVANTAGE_API_KEY). Ficha OVERVIEW: preço-alvo médio dos analistas, contagem de "
            "recomendações, setor, P/L, margem e ROE. Cada ação é atualizada no máximo uma vez por semana. "
            "02/11/2026 (chamadas reais): OVERVIEW passou a ser endpoint pago ('This is a premium "
            "endpoint') — sem preço-alvo e ficha no plano gratuito; o portal suspende a função por 7 dias "
            "para todos os ativos. "
            "Limite de 1 requisição por segundo; o portal espaça as chamadas."
        ),
        limite_dia=25,
    )

    INTERVALO_MIN_S = 1.5
    _trava = threading.Lock()
    _ultima_chamada = 0.0

    def ficha(self, simbolo: str) -> FichaEmpresa:
        corpo = self._consultar("OVERVIEW", simbolo)
        if not corpo.get("Symbol"):
            raise InvalidResponseError("alphavantage: ficha vazia (ETF ou símbolo sem cobertura)")
        hoje = self.agora().date()
        notas = [
            _inteiro(corpo.get(k))
            for k in (
                "AnalystRatingStrongBuy",
                "AnalystRatingBuy",
                "AnalystRatingHold",
                "AnalystRatingSell",
                "AnalystRatingStrongSell",
            )
        ]
        recomendacao = None
        if sum(notas):
            recomendacao = RecomendacaoNormalizada(simbolo.upper(), hoje, *notas, fonte=self.info.codigo)
        alvo_medio = _num_av(corpo.get("AnalystTargetPrice"))
        preco_alvo = (
            PrecoAlvoNormalizado(simbolo=simbolo.upper(), fonte=self.info.codigo, media=alvo_medio)
            if alvo_medio and alvo_medio > 0
            else None
        )
        return FichaEmpresa(
            simbolo=simbolo.upper(),
            fonte=self.info.codigo,
            setor=str(corpo.get("Sector") or "")[:80].title(),
            industria=str(corpo.get("Industry") or "")[:120].title(),
            pl=_num_av(corpo.get("PERatio")),
            pl_projetado=_num_av(corpo.get("ForwardPE")),
            margem_liquida=_num_av(corpo.get("ProfitMargin")),
            roe=_num_av(corpo.get("ReturnOnEquityTTM")),
            valor_mercado=_num_av(corpo.get("MarketCapitalization")),
            dividend_yield=_num_av(corpo.get("DividendYield")),
            maxima_52s=_num_av(corpo.get("52WeekHigh")),
            minima_52s=_num_av(corpo.get("52WeekLow")),
            ultimo_trimestre=_data(corpo.get("LatestQuarter")),
            pvp=_num_av(corpo.get("PriceToBookRatio")),
            ev_ebitda=_num_av(corpo.get("EVToEBITDA")),
            crescimento_receita=_num_av(corpo.get("QuarterlyRevenueGrowthYOY")),
            n_analistas=sum(notas) or None,
            recomendacao=recomendacao,
            preco_alvo=preco_alvo,
            bruto={k: corpo.get(k) for k in ("Symbol", "AnalystTargetPrice", "PERatio", "LatestQuarter")},
        )

    def _consultar(self, funcao: str, simbolo: str) -> dict:
        if not self.api_key:
            raise AuthError("alphavantage: ALPHAVANTAGE_API_KEY vazia no .env")
        with AlphaVantageAdapter._trava:  # 1 requisição por segundo no plano gratuito
            espera = AlphaVantageAdapter.INTERVALO_MIN_S - (
                time.monotonic() - AlphaVantageAdapter._ultima_chamada
            )
            if espera > 0:
                time.sleep(espera)
            try:
                resposta = self._get(
                    self.base_url, params={"function": funcao, "symbol": simbolo, "apikey": self.api_key}
                )
            finally:
                AlphaVantageAdapter._ultima_chamada = time.monotonic()
        corpo = self._json(resposta)
        if not isinstance(corpo, dict):
            raise InvalidResponseError("alphavantage: resposta inesperada")
        for chave in ("Information", "Note", "Error Message"):
            if chave in corpo:
                msg = str(corpo[chave])[:200]
                baixo = msg.lower()
                if "premium endpoint" in baixo:
                    raise AuthError(
                        f"alphavantage: {funcao} é endpoint pago (premium) — indisponível no plano gratuito"
                    )
                if "per second" in baixo or "sparingly" in baixo or "spreading out" in baixo:
                    raise RateLimitError("alphavantage: limite de 1 requisição por segundo (tentar de novo)")
                if "rate limit" in baixo or "requests per day" in baixo or "frequency" in baixo:
                    raise RateLimitError(f"alphavantage: cota diária — {msg}")
                if "apikey" in baixo or ("api key" in baixo and "invalid" in baixo):
                    raise AuthError(f"alphavantage: {msg}")
                raise InvalidResponseError(f"alphavantage: {msg}")
        return corpo

    def dividendos(self, simbolo: str) -> list[ProventoNormalizado]:
        """DIVIDENDS: histórico de dividendos por ação (USD, valores brutos)."""
        corpo = self._consultar("DIVIDENDS", simbolo)
        itens = corpo.get("data")
        if not isinstance(itens, list):
            raise InvalidResponseError("alphavantage: dividendos sem 'data'")
        saida = []
        for item in itens:
            valor = _num_av(item.get("amount"))
            if valor is None or valor <= 0:
                continue
            saida.append(
                ProventoNormalizado(
                    simbolo=simbolo.upper(),
                    fonte=self.info.codigo,
                    tipo="DIVIDENDO",
                    valor=valor,
                    moeda="USD",
                    data_com=_data(item.get("ex_dividend_date")),
                    data_pagamento=_data(item.get("payment_date")),
                    data_aprovacao=_data(item.get("declaration_date")),
                )
            )
        return saida


class FocusBcbAdapter(BaseAdapter):
    base_url = "https://olinda.bcb.gov.br/olinda/servico/Expectativas/versao/v1/odata"
    info = FonteInfo(
        codigo="focus_bcb",
        nome="Focus — Banco Central (expectativas de mercado)",
        tipo_dado=QuoteType.UNDECLARED,
        atraso_declarado_min=None,
        atraso_declarado_texto="pesquisa semanal (publicada às segundas-feiras)",
        limites="dados abertos; limites de requisição não informados",
        termos_url="https://dadosabertos.bcb.gov.br/dataset/expectativas-mercado",
        homologada=False,
        observacoes=(
            "Mediana das projeções de instituições financeiras para o câmbio no fim de cada ano. É "
            "expectativa de mercado (projeção), não previsão do portal."
        ),
    )

    def expectativas_cambio(self, limite: int = 40) -> list[ExpectativaFocus]:
        return self.expectativas(("Câmbio",), limite)

    def expectativas(
        self, indicadores=("Câmbio", "Selic", "IPCA"), limite: int = 120
    ) -> list[ExpectativaFocus]:
        filtro = " or ".join(f"Indicador eq '{i}'" for i in indicadores)
        parametros = {
            "$top": str(limite),
            "$filter": f"({filtro})" if len(indicadores) > 1 else filtro,
            "$orderby": "Data desc",
            "$format": "json",
        }
        # OData do BCB rejeita '+' como espaço (HTTP 400: URI malformed).
        consulta = urlencode(parametros, quote_via=quote)
        resposta = self._get(f"{self.base_url}/ExpectativasMercadoAnuais?{consulta}")
        corpo = self._json(resposta)
        valores = corpo.get("value") if isinstance(corpo, dict) else None
        if not isinstance(valores, list):
            raise InvalidResponseError("focus_bcb: resposta sem 'value'")
        saida = []
        for v in valores:
            data = _data(v.get("Data"))
            if data is None:
                continue
            respondentes = v.get("numeroRespondentes")
            saida.append(
                ExpectativaFocus(
                    indicador=str(v.get("Indicador") or "Câmbio"),
                    data=data,
                    data_referencia=str(v.get("DataReferencia") or "")[:10],
                    base_calculo=_inteiro(v.get("baseCalculo")),
                    media=decimal_ou_none(v.get("Media")),
                    mediana=decimal_ou_none(v.get("Mediana")),
                    minimo=decimal_ou_none(v.get("Minimo")),
                    maximo=decimal_ou_none(v.get("Maximo")),
                    desvio=decimal_ou_none(v.get("DesvioPadrao")),
                    respondentes=respondentes if isinstance(respondentes, int) else None,
                )
            )
        return saida


class SgsBcbAdapter(BaseAdapter):
    """Séries temporais do Banco Central (SGS), dados abertos sem chave.

    GET https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados/ultimos/{N}?formato=json
    → [{"data": "dd/mm/aaaa", "valor": "10.50"}]
    Séries usadas: 432 meta Selic (% a.a.), 433 IPCA mensal (%), 13522 IPCA acumulado em 12 meses (%),
    12 CDI diário (% a.d., referência da evolução da carteira).
    """

    base_url = "https://api.bcb.gov.br/dados/serie"
    info = FonteInfo(
        codigo="sgs_bcb",
        nome="SGS — Banco Central (Selic e IPCA)",
        tipo_dado=QuoteType.UNDECLARED,
        atraso_declarado_min=None,
        atraso_declarado_texto="indicadores oficiais publicados pelo BCB/IBGE (datas de referência da série)",
        limites="dados abertos; limites de requisição não informados",
        termos_url="https://dadosabertos.bcb.gov.br/",
        homologada=False,
        observacoes="Meta Selic (432), IPCA mensal (433), IPCA em 12 meses (13522) e CDI diário (12).",
    )

    SERIES = {432: "Selic meta (% a.a.)", 433: "IPCA no mês (%)", 13522: "IPCA em 12 meses (%)",
              12: "CDI diário (% a.d.)"}

    def serie(self, codigo: int, ultimos: int = 20) -> list[tuple[dt.date, Decimal]]:
        # Limite confirmado pela própria API: no máximo 20 valores nesta rota.
        if not 1 <= int(ultimos) <= 20:
            raise ValueError("SGS: a consulta de últimos valores aceita de 1 a 20 registros.")
        resposta = self._get(
            f"{self.base_url}/bcdata.sgs.{int(codigo)}/dados/ultimos/{int(ultimos)}",
            params={"formato": "json"},
        )
        corpo = self._json(resposta)
        if not isinstance(corpo, list):
            raise InvalidResponseError("sgs_bcb: resposta inesperada")
        saida = []
        for item in corpo:
            try:
                data = dt.datetime.strptime(str(item.get("data")), "%d/%m/%Y").date()
            except ValueError:
                continue
            valor = decimal_ou_none(item.get("valor"))
            if valor is not None:
                saida.append((data, valor))
        return saida
