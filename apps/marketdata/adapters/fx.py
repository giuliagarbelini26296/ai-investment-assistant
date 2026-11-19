"""
Câmbio USD/BRL.

PTAX (Banco Central, dados abertos, sem chave):
  GET https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/CotacaoDolarDia(dataCotacao=@dataCotacao)
      ?@dataCotacao='MM-DD-YYYY'&$format=json
  → {"value": [{"cotacaoCompra": 5.3271, "cotacaoVenda": 5.3277,
                "dataHoraCotacao": "2026-10-28 13:09:37.513"}]}
  Lista vazia = dia sem cotação (fim de semana/feriado). Fechamento oficial diário; NÃO é intradiário.

AwesomeAPI (intradiário; chave opcional — sem chave há cache de 1 min):
  GET https://economia.awesomeapi.com.br/last/USD-BRL
  → {"USDBRL": {"bid","ask","high","low","pctChange","timestamp"(epoch s),"create_date"}}
  Atraso não quantificado pela fonte.

Formatos conforme documentação oficial; endpoints não acessíveis do ambiente de desenvolvimento.
NÃO HOMOLOGADOS até captura real.
"""

from __future__ import annotations

import datetime as dt

from apps.marketdata.models import FxRateType, QuoteType

from .base import (
    BaseFxAdapter,
    CambioNormalizado,
    FonteInfo,
    InvalidResponseError,
    decimal_ou_none,
)

_BRT = dt.timezone(dt.timedelta(hours=-3))


class PtaxBcbAdapter(BaseFxAdapter):
    base_url = "https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata"
    tipo_cambio = FxRateType.PTAX_CLOSE
    info = FonteInfo(
        codigo="ptax_bcb",
        nome="PTAX — Banco Central do Brasil",
        tipo_dado=QuoteType.CLOSE,
        atraso_declarado_min=None,
        atraso_declarado_texto="fechamento oficial diário (média dos boletins do dia)",
        limites="dados abertos (ODbL); limites de requisição não informados",
        termos_url="https://dadosabertos.bcb.gov.br/dataset/dolar-americano-usd-todos-os-boletins-diarios",
        homologada=True,
        observacoes=(
            "Homologada em 30/10/2026 (captura real): CotacaoDolarDia com cotacaoCompra/cotacaoVenda e "
            "dataHoraCotacao (boletim de fechamento ~13:04); 249 datas carregadas sem falhas."
        ),
    )

    def cambio_diario(self, data: dt.date) -> CambioNormalizado | None:
        resposta = self._get(
            f"{self.base_url}/CotacaoDolarDia(dataCotacao=@dataCotacao)",
            params={"@dataCotacao": f"'{data:%m-%d-%Y}'", "$format": "json"},
        )
        corpo = self._json(resposta)
        valores = corpo.get("value") if isinstance(corpo, dict) else None
        if not isinstance(valores, list):
            raise InvalidResponseError("ptax_bcb: resposta sem 'value'")
        if not valores:
            return None  # sem cotação nessa data
        item = valores[-1]
        compra = decimal_ou_none(item.get("cotacaoCompra"))
        if compra is None:
            raise InvalidResponseError("ptax_bcb: cotacaoCompra ausente")
        horario = None
        texto = str(item.get("dataHoraCotacao") or "")
        if texto:
            try:
                horario = dt.datetime.fromisoformat(texto.replace(" ", "T")).replace(tzinfo=_BRT)
            except ValueError:
                horario = None
        return self._validar_cambio(
            CambioNormalizado(
                par=self.par,
                compra=compra,
                venda=decimal_ou_none(item.get("cotacaoVenda")),
                tipo=self.tipo_cambio,
                fonte=self.info.codigo,
                horario_coleta=self.agora(),
                horario=horario,
                data=data,
                bruto=item,
            )
        )

    def cambio(self) -> CambioNormalizado:
        """PTAX 'atual' = fechamento mais recente disponível (hoje, senão dias anteriores)."""
        hoje = self.agora().astimezone(_BRT).date()
        for recuo in range(0, 7):
            c = self.cambio_diario(hoje - dt.timedelta(days=recuo))
            if c is not None:
                return c
        raise InvalidResponseError("ptax_bcb: nenhuma cotação nos últimos 7 dias")


class AwesomeApiAdapter(BaseFxAdapter):
    base_url = "https://economia.awesomeapi.com.br"
    tipo_cambio = FxRateType.INTRADAY
    info = FonteInfo(
        codigo="awesomeapi",
        nome="AwesomeAPI (câmbio intradiário)",
        tipo_dado=QuoteType.UNDECLARED,
        atraso_declarado_min=None,
        atraso_declarado_texto="não quantificado pela fonte; sem chave, cache de 1 min",
        limites="sem chave: 100 req sequenciais; com chave: 1.500; até 100.000 req gratuitas",
        termos_url="https://docs.awesomeapi.com.br/api-de-moedas",
        homologada=False,
        observacoes=(
            "Em uso na máquina do usuário desde 30/10/2026 (respostas 200 a cada 15 min); captura formal "
            "com `homologar_fonte awesomeapi` ainda não registrada."
        ),
    )

    def cambio(self) -> CambioNormalizado:
        headers = {"x-api-key": self.api_key} if self.api_key else {}
        resposta = self._get(f"{self.base_url}/last/{self.par}", headers=headers)
        corpo = self._json(resposta)
        item = corpo.get(self.par.replace("-", "")) if isinstance(corpo, dict) else None
        if not isinstance(item, dict):
            raise InvalidResponseError("awesomeapi: par ausente na resposta")
        compra = decimal_ou_none(item.get("bid"))
        if compra is None:
            raise InvalidResponseError("awesomeapi: 'bid' ausente")
        ts = item.get("timestamp")
        horario = dt.datetime.fromtimestamp(int(ts), tz=dt.UTC) if ts and str(ts).isdigit() else None
        return self._validar_cambio(
            CambioNormalizado(
                par=self.par,
                compra=compra,
                venda=decimal_ou_none(item.get("ask")),
                tipo=self.tipo_cambio,
                fonte=self.info.codigo,
                horario_coleta=self.agora(),
                horario=horario,
                data=horario.astimezone(_BRT).date() if horario else None,
                bruto={
                    k: item.get(k)
                    for k in ("bid", "ask", "high", "low", "varBid", "pctChange", "timestamp", "create_date")
                },
            )
        )

    def cambio_diario(self, data: dt.date) -> CambioNormalizado | None:
        return None  # histórico diário oficial vem do PTAX
