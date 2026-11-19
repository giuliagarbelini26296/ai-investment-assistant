"""
Adaptador de DEMONSTRAÇÃO — dados sintéticos, determinísticos (semente fixa).

Nunca representa o mercado. Toda cotação sai com fonte "demo" e tipo "spot"; a interface exibe a
faixa DEMONSTRAÇÃO sempre que este adaptador estiver ativo. Não faz rede.
Preços são gerados por um passeio aleatório suave a partir de um preço-base por símbolo.
"""

from __future__ import annotations

import datetime as dt
import random
from decimal import Decimal

from apps.marketdata.models import FxRateType, QuoteType, TimeBasis

from .base import (
    BaseAdapter,
    BaseFxAdapter,
    CambioNormalizado,
    CandleNormalizado,
    CotacaoNormalizada,
    FonteInfo,
    ResultadoCotacao,
)

PRECO_BASE_PADRAO = Decimal("100")


def _rng(simbolo: str, data: dt.date) -> random.Random:
    return random.Random(f"demo:{simbolo.upper()}:{data.isoformat()}")


def _serie(simbolo: str, ate: dt.date, dias: int, base: Decimal) -> list[tuple[dt.date, Decimal]]:
    """Fechamentos sintéticos dia a dia (inclui fins de semana para simplificar)."""
    inicio = ate - dt.timedelta(days=dias - 1)
    preco = base
    pontos = []
    for i in range(dias):
        data = inicio + dt.timedelta(days=i)
        variacao = Decimal(str(round(_rng(simbolo, data).uniform(-0.025, 0.025), 5)))
        preco = (preco * (1 + variacao)).quantize(Decimal("0.00000001"))
        pontos.append((data, preco))
    return pontos


class DemoAdapter(BaseAdapter):
    moeda_cotacao = "USD"
    base_tempo = TimeBasis.LOCAL_SESSION
    info = FonteInfo(
        codigo="demo",
        nome="DEMONSTRAÇÃO (dados sintéticos)",
        tipo_dado=QuoteType.SPOT,
        atraso_declarado_min=0,
        atraso_declarado_texto="não se aplica — dados sintéticos",
        limites="nenhum",
        termos_url="",
        homologada=True,
        observacoes="Gerador determinístico; nunca exibir como cotação de mercado.",
    )

    def __init__(self, moeda: str = "USD", precos_base: dict[str, Decimal] | None = None, **kwargs):
        super().__init__(client=None, **kwargs)
        self.moeda_cotacao = moeda
        self.precos_base = {k.upper(): Decimal(str(v)) for k, v in (precos_base or {}).items()}

    def _base(self, simbolo: str) -> Decimal:
        return self.precos_base.get(simbolo.upper(), PRECO_BASE_PADRAO)

    def cotar(self, simbolos: list[str]) -> ResultadoCotacao:
        agora = self.agora()
        hoje = agora.date()
        brutas = []
        for s in simbolos:
            serie = _serie(s, hoje, 2, self._base(s))
            (_, ontem), (_, atual) = serie
            brutas.append(
                CotacaoNormalizada(
                    simbolo=s.upper(),
                    preco=atual,
                    moeda=self.moeda_cotacao,
                    horario_coleta=agora,
                    horario_mercado=agora - dt.timedelta(minutes=1),
                    fechamento_anterior=ontem,
                    variacao_24h_pct=(atual / ontem - 1) * 100,
                    fonte=self.info.codigo,
                    fonte_versao="sintetico",
                    tipo=self.info.tipo_dado,
                    atraso_declarado_min=0,
                    bruto={"demo": True},
                )
            )
        return self._finalizar(brutas, {})

    def historico(self, simbolo: str, dias: int = 30) -> list[CandleNormalizado]:
        hoje = self.agora().date()
        candles = []
        for data, close in _serie(simbolo, hoje, dias, self._base(simbolo)):
            r = _rng(simbolo + ":ohlc", data)
            alta = close * Decimal(str(round(1 + r.uniform(0, 0.01), 5)))
            baixa = close * Decimal(str(round(1 - r.uniform(0, 0.01), 5)))
            candles.append(
                CandleNormalizado(
                    simbolo=simbolo.upper(),
                    data=data,
                    base_tempo=self.base_tempo,
                    open=baixa + (alta - baixa) / 2,
                    high=alta,
                    low=baixa,
                    close=close,
                    volume=Decimal(r.randint(1000, 100000)),
                    fonte=self.info.codigo,
                    provisorio=data == hoje,
                )
            )
        return candles


class DemoFxAdapter(BaseFxAdapter):
    tipo_cambio = FxRateType.REFERENCE
    info = FonteInfo(
        codigo="demo_fx",
        nome="DEMONSTRAÇÃO (câmbio sintético)",
        tipo_dado=QuoteType.SPOT,
        atraso_declarado_min=0,
        atraso_declarado_texto="não se aplica — dados sintéticos",
        limites="nenhum",
        termos_url="",
        homologada=True,
    )

    def __init__(self, base: Decimal = Decimal("5.30"), **kwargs):
        super().__init__(client=None, **kwargs)
        self.base = Decimal(str(base))

    def cambio_diario(self, data: dt.date) -> CambioNormalizado | None:
        if data.weekday() >= 5:
            return None  # sem PTAX em fim de semana, como o BCB
        (_, valor) = _serie("USD-BRL", data, 1, self.base)[0]
        return CambioNormalizado(
            par=self.par,
            compra=valor.quantize(Decimal("0.000001")),
            venda=(valor * Decimal("1.0002")).quantize(Decimal("0.000001")),
            tipo=FxRateType.PTAX_CLOSE,
            fonte=self.info.codigo,
            horario_coleta=self.agora(),
            horario=dt.datetime.combine(data, dt.time(13, 0), tzinfo=dt.timezone(dt.timedelta(hours=-3))),
            data=data,
            bruto={"demo": True},
        )

    def cambio(self) -> CambioNormalizado:
        agora = self.agora()
        c = self.cambio_diario(agora.date()) or self.cambio_diario(agora.date() - dt.timedelta(days=2))
        if c is None:
            c = self.cambio_diario(agora.date() - dt.timedelta(days=3))
        assert c is not None
        c.tipo = self.tipo_cambio
        c.horario = agora - dt.timedelta(minutes=1)
        return c
