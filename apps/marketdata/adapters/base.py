"""
Contrato dos adaptadores de fontes de dados.

Cada adaptador:
- declara `FonteInfo` (nome, tipo de dado, atraso declarado, limites, termos, atribuição, homologação);
- implementa `cotar(simbolos)` → `ResultadoCotacao` (cotações válidas + falhas por símbolo);
- implementa `historico(simbolo, dias)` → lista de `CandleNormalizado`;
- adaptadores de câmbio implementam `cambio()` / `cambio_diario(data)`.

Toda resposta passa por validação: preço positivo e finito, moeda esperada, horário de mercado não
futuro (tolerância configurável). Falha de validação vira falha por símbolo — nunca cotação.
Erros de transporte/autenticação/cota são exceções classificadas para o coletor decidir a retentativa.
Nenhum adaptador grava no banco: isso é papel do coletor (E4).
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

import httpx

from apps.marketdata.models import FxRateType, QuoteType, TimeBasis

log = logging.getLogger("fontes")

TOLERANCIA_FUTURO = dt.timedelta(minutes=2)
TIMEOUT_PADRAO = 10.0


# --- Erros classificados --------------------------------------------------------------


class AdapterError(Exception):
    """Erro do adaptador. `retryable` orienta o coletor; a mensagem nunca contém chaves."""

    kind = "erro"
    retryable = False

    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


class AuthError(AdapterError):
    kind = "autenticacao"


class RateLimitError(AdapterError):
    kind = "cota"
    retryable = True

    def __init__(self, message: str, *, status: int | None = None, retry_after: int | None = None):
        super().__init__(message, status=status)
        self.retry_after = retry_after


class NetworkError(AdapterError):
    kind = "rede"
    retryable = True


class InvalidResponseError(AdapterError):
    kind = "resposta_invalida"


class SymbolNotFoundError(AdapterError):
    kind = "simbolo_inexistente"


# --- Estruturas normalizadas -----------------------------------------------------------


@dataclass(frozen=True)
class FonteInfo:
    codigo: str
    nome: str
    tipo_dado: str  # QuoteType
    atraso_declarado_min: int | None  # None = não declarado
    atraso_declarado_texto: str
    limites: str
    termos_url: str
    atribuicao: str = ""
    homologada: bool = False
    observacoes: str = ""
    limite_dia: int | None = None
    limite_mes: int | None = None
    limite_minuto: int | None = None


@dataclass
class CotacaoNormalizada:
    simbolo: str
    preco: Decimal
    moeda: str
    horario_coleta: dt.datetime
    fonte: str
    tipo: str
    horario_mercado: dt.datetime | None = None
    fechamento_anterior: Decimal | None = None
    variacao_24h_pct: Decimal | None = None
    atraso_declarado_min: int | None = None
    fonte_versao: str = ""
    bruto: dict = field(default_factory=dict)


@dataclass
class CandleNormalizado:
    simbolo: str
    data: dt.date
    base_tempo: str
    close: Decimal
    fonte: str
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    volume: Decimal | None = None
    provisorio: bool = False


@dataclass
class CambioNormalizado:
    par: str
    compra: Decimal
    tipo: str
    fonte: str
    horario_coleta: dt.datetime
    venda: Decimal | None = None
    horario: dt.datetime | None = None
    data: dt.date | None = None
    bruto: dict = field(default_factory=dict)


@dataclass
class ResultadoCotacao:
    cotacoes: list[CotacaoNormalizada] = field(default_factory=list)
    falhas: dict[str, str] = field(default_factory=dict)

    @property
    def parcial(self) -> bool:
        return bool(self.falhas) and bool(self.cotacoes)


# --- Utilidades -------------------------------------------------------------------------


def decimal_ou_none(valor) -> Decimal | None:
    if valor is None or valor == "":
        return None
    try:
        d = Decimal(str(valor))
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def agora_utc() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def validar_cotacao(cot: CotacaoNormalizada, moeda_esperada: str, agora: dt.datetime) -> str | None:
    """Devolve o motivo de rejeição, ou None se a cotação é válida."""
    if cot.preco is None or not cot.preco.is_finite() or cot.preco <= 0:
        return f"preço inválido: {cot.preco!r}"
    if cot.moeda != moeda_esperada:
        return f"moeda {cot.moeda} diferente da esperada {moeda_esperada}"
    if cot.horario_mercado is not None:
        if cot.horario_mercado.tzinfo is None:
            return "horário de mercado sem fuso"
        if cot.horario_mercado > agora + TOLERANCIA_FUTURO:
            return f"horário de mercado no futuro: {cot.horario_mercado.isoformat()}"
    if cot.fechamento_anterior is not None and cot.fechamento_anterior <= 0:
        cot.fechamento_anterior = None  # ignora, não invalida
    return None


class BaseAdapter:
    info: FonteInfo
    base_url: str = ""
    moeda_cotacao: str = "USD"
    base_tempo: str = TimeBasis.LOCAL_SESSION

    def __init__(
        self,
        client: httpx.Client | None = None,
        api_key: str | None = None,
        agora: Callable[[], dt.datetime] = agora_utc,
    ):
        self.client = client or httpx.Client(timeout=TIMEOUT_PADRAO)
        self.api_key = api_key
        self.agora = agora

    # -- transporte -----------------------------------------------------------------------

    def _get(self, url: str, params: dict | None = None, headers: dict | None = None) -> httpx.Response:
        cabecalhos = dict(headers or {})
        try:
            try:
                resposta = self.client.get(url, params=params, headers=cabecalhos, timeout=TIMEOUT_PADRAO)
            except httpx.DecodingError:
                # corpo com compressão que não conseguimos decodificar (ex.: br/zstd sem a biblioteca):
                # repete pedindo resposta sem compressão
                cabecalhos["Accept-Encoding"] = "identity"
                resposta = self.client.get(url, params=params, headers=cabecalhos, timeout=TIMEOUT_PADRAO)
        except httpx.TimeoutException as exc:
            raise NetworkError(f"timeout ao consultar {self.info.codigo}") from exc
        except httpx.HTTPError as exc:
            detalhe = f"{type(exc).__name__}: {str(exc)[:120]}" if str(exc) else type(exc).__name__
            raise NetworkError(f"erro de rede ao consultar {self.info.codigo}: {detalhe}") from exc
        self._verificar_status(resposta)
        return resposta

    def _verificar_status(self, resposta: httpx.Response) -> None:
        st = resposta.status_code
        if st == 429:
            retry = resposta.headers.get("Retry-After")
            raise RateLimitError(
                f"{self.info.codigo}: limite de requisições (429)",
                status=st,
                retry_after=int(retry) if retry and retry.isdigit() else None,
            )
        if st in (401, 403):
            raise AuthError(
                f"{self.info.codigo}: credencial ausente, inválida ou sem permissão ({st})", status=st
            )
        if st == 404:
            raise SymbolNotFoundError(f"{self.info.codigo}: recurso não encontrado (404)", status=st)
        if 500 <= st < 600:
            raise NetworkError(f"{self.info.codigo}: erro do servidor ({st})", status=st)
        if st >= 400:
            raise InvalidResponseError(f"{self.info.codigo}: HTTP {st}", status=st)

    def _json(self, resposta: httpx.Response):
        try:
            return resposta.json()
        except ValueError as exc:
            raise InvalidResponseError(f"{self.info.codigo}: corpo não é JSON") from exc

    # -- contrato ---------------------------------------------------------------------------

    def cotar(self, simbolos: list[str]) -> ResultadoCotacao:  # pragma: no cover - abstrato
        raise NotImplementedError

    def historico(self, simbolo: str, dias: int = 30) -> list[CandleNormalizado]:  # pragma: no cover
        raise NotImplementedError

    def _finalizar(self, brutas: list[CotacaoNormalizada], falhas: dict[str, str]) -> ResultadoCotacao:
        """Aplica validação e separa válidas de rejeitadas."""
        agora = self.agora()
        resultado = ResultadoCotacao(falhas=dict(falhas))
        for cot in brutas:
            motivo = validar_cotacao(cot, self.moeda_cotacao, agora)
            if motivo:
                resultado.falhas[cot.simbolo] = motivo
                log.warning(
                    "cotação descartada | fonte=%s simbolo=%s motivo=%s", cot.fonte, cot.simbolo, motivo
                )
            else:
                resultado.cotacoes.append(cot)
        return resultado


class BaseFxAdapter(BaseAdapter):
    par = "USD-BRL"
    tipo_cambio: str = FxRateType.INTRADAY

    def cambio(self) -> CambioNormalizado:  # pragma: no cover - abstrato
        raise NotImplementedError

    def cambio_diario(self, data: dt.date) -> CambioNormalizado | None:  # pragma: no cover
        raise NotImplementedError

    def _validar_cambio(self, c: CambioNormalizado) -> CambioNormalizado:
        if c.compra is None or not c.compra.is_finite() or c.compra <= 0:
            raise InvalidResponseError(f"{self.info.codigo}: câmbio inválido {c.compra!r}")
        if c.venda is not None and (not c.venda.is_finite() or c.venda <= 0):
            c.venda = None
        if c.horario is not None and c.horario > self.agora() + TOLERANCIA_FUTURO:
            raise InvalidResponseError(f"{self.info.codigo}: horário do câmbio no futuro")
        return c


__all__ = [
    "AdapterError",
    "AuthError",
    "BaseAdapter",
    "BaseFxAdapter",
    "CambioNormalizado",
    "CandleNormalizado",
    "CotacaoNormalizada",
    "FonteInfo",
    "InvalidResponseError",
    "NetworkError",
    "QuoteType",
    "RateLimitError",
    "ResultadoCotacao",
    "SymbolNotFoundError",
    "decimal_ou_none",
]
