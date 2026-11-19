"""
Cálculo puro de posições, na moeda nativa do ativo.

Regras (proposta técnica v1.1, seção 7):
    Q  = Σ qᵢ                      quantidade
    I  = Σ (qᵢ · pᵢ)               valor investido
    PM = I / Q                     preço médio
    V  = Q · P                     valor de mercado (P = preço atual válido)
    resultado = V − I
    retorno % = 100 · (V − I) / I
    variação diária % = 100 · (P / fechamento_anterior − 1)

Totais consolidados: soma dos resultados; retorno consolidado = resultado total / investido total.
Nunca média de percentuais. Sem preço válido para algum ativo, o total é parcial e a cobertura informa.

Este módulo não conhece Django, câmbio nem fontes: recebe números e devolve números.
Toda aritmética é Decimal; arredondamento só na apresentação.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

ZERO = Decimal("0")
CEM = Decimal("100")


class ErroDeCalculo(ValueError):
    """Entrada inválida para o cálculo (quantidade ou preço não positivos, etc.)."""


def _decimal(valor, nome: str) -> Decimal:
    try:
        d = Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ErroDeCalculo(f"{nome} inválido: {valor!r}") from exc
    if not d.is_finite():
        raise ErroDeCalculo(f"{nome} não é finito: {valor!r}")
    return d


@dataclass(frozen=True)
class Lote:
    quantidade: Decimal
    preco: Decimal

    def __post_init__(self):
        q = _decimal(self.quantidade, "quantidade")
        p = _decimal(self.preco, "preço")
        if q <= ZERO:
            raise ErroDeCalculo(f"quantidade deve ser positiva: {q}")
        if p <= ZERO:
            raise ErroDeCalculo(f"preço deve ser positivo: {p}")
        object.__setattr__(self, "quantidade", q)
        object.__setattr__(self, "preco", p)

    @property
    def investido(self) -> Decimal:
        return self.quantidade * self.preco


@dataclass(frozen=True)
class Posicao:
    quantidade: Decimal
    investido: Decimal
    preco_medio: Decimal
    lotes: int


def posicao(lotes: list[Lote]) -> Posicao | None:
    """Agrega lotes de um mesmo ativo. Lista vazia → None (sem posição)."""
    if not lotes:
        return None
    q = sum((lote.quantidade for lote in lotes), ZERO)
    investido = sum((lote.investido for lote in lotes), ZERO)
    return Posicao(quantidade=q, investido=investido, preco_medio=investido / q, lotes=len(lotes))


def adicionar_compra(atual: Posicao | None, compra: Lote) -> Posicao:
    """Uses the exact accumulated cost, without reconstructing it from a rounded PM."""
    quantidade = (atual.quantidade if atual else ZERO) + compra.quantidade
    investido = (atual.investido if atual else ZERO) + compra.investido
    return Posicao(quantidade, investido, investido / quantidade, (atual.lotes if atual else 0) + 1)


@dataclass(frozen=True)
class Avaliacao:
    """Posição avaliada a um preço atual válido."""

    posicao: Posicao
    preco_atual: Decimal
    valor_atual: Decimal
    diferenca_unitaria: Decimal
    resultado: Decimal
    retorno_pct: Decimal
    variacao_dia_pct: Decimal | None = None


def avaliar(pos: Posicao, preco_atual, fechamento_anterior=None) -> Avaliacao:
    p = _decimal(preco_atual, "preço atual")
    if p <= ZERO:
        raise ErroDeCalculo(f"preço atual deve ser positivo: {p}")
    valor_atual = pos.quantidade * p
    resultado = valor_atual - pos.investido
    retorno = CEM * resultado / pos.investido
    variacao_dia = None
    if fechamento_anterior is not None:
        fa = _decimal(fechamento_anterior, "fechamento anterior")
        if fa > ZERO:
            variacao_dia = CEM * (p / fa - Decimal("1"))
    return Avaliacao(
        posicao=pos,
        preco_atual=p,
        valor_atual=valor_atual,
        diferenca_unitaria=p - pos.preco_medio,
        resultado=resultado,
        retorno_pct=retorno,
        variacao_dia_pct=variacao_dia,
    )


@dataclass
class Consolidado:
    """Totais de um conjunto de posições na MESMA moeda.

    `investido_total` cobre todas as posições; `valor_atual`, `resultado` e `retorno_pct`
    cobrem apenas as posições com preço válido (ver `cobertura`).
    """

    moeda: str
    posicoes_total: int = 0
    posicoes_avaliadas: int = 0
    investido_total: Decimal = ZERO
    investido_avaliado: Decimal = ZERO
    valor_atual: Decimal = ZERO
    resultado: Decimal = ZERO
    retorno_pct: Decimal | None = None
    sem_preco: list[str] = field(default_factory=list)

    @property
    def completo(self) -> bool:
        return self.posicoes_total > 0 and self.posicoes_avaliadas == self.posicoes_total

    @property
    def cobertura(self) -> str:
        return f"{self.posicoes_avaliadas} de {self.posicoes_total} posições com preço"


def consolidar(moeda: str, itens: list[tuple[str, Posicao, Avaliacao | None]]) -> Consolidado:
    """itens: (identificador, posição, avaliação ou None quando não há preço válido)."""
    c = Consolidado(moeda=moeda)
    for ident, pos, aval in itens:
        c.posicoes_total += 1
        c.investido_total += pos.investido
        if aval is None:
            c.sem_preco.append(ident)
            continue
        c.posicoes_avaliadas += 1
        c.investido_avaliado += pos.investido
        c.valor_atual += aval.valor_atual
        c.resultado += aval.resultado
    if c.investido_avaliado > ZERO:
        c.retorno_pct = CEM * c.resultado / c.investido_avaliado
    return c


def pesos(itens: list[tuple[str, Avaliacao]]) -> dict[str, Decimal]:
    """Peso de cada posição avaliada sobre o valor de mercado total das avaliadas (mesma moeda)."""
    total = sum((aval.valor_atual for _, aval in itens), ZERO)
    if total <= ZERO:
        return {}
    return {ident: CEM * aval.valor_atual / total for ident, aval in itens}


# --- Apresentação -----------------------------------------------------------------


def arredondar_moeda(valor: Decimal | None) -> Decimal | None:
    return None if valor is None else valor.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def arredondar_pct(valor: Decimal | None) -> Decimal | None:
    return None if valor is None else valor.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
