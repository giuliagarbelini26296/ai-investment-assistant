"""
Conversão de posições para a moeda de visualização, com decomposição resultado do ativo × efeito cambial.

Notação (proposta técnica v1.1, seção 7), com `rᵢ` = taxa nativa→visão de cada lote e `r` = taxa atual:
    I_v  = Σ qᵢ·pᵢ·rᵢ              investido na moeda de visualização
    R̄    = I_v / I                   taxa média ponderada
    V_v  = Q·P·r                     valor atual na moeda de visualização
    V_v − I_v = Q·(P − PM)·r  [resultado do ativo ao câmbio atual]  +  Q·PM·(r − R̄)  [efeito cambial]

Para ativo em USD visto em BRL, r = USD/BRL. Para ativo em BRL visto em USD, r = 1 / (USD/BRL).
Lote sem taxa resolvida ("sem base cambial") impede a conversão da posição inteira: a função devolve
None e o chamador exclui a posição dos totais convertidos, informando a cobertura. Nunca se usa a
taxa atual como substituta da taxa de compra (zeraria artificialmente o efeito cambial).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .core import ZERO, Avaliacao, ErroDeCalculo, Lote, Posicao, _decimal

UM = Decimal("1")


@dataclass(frozen=True)
class LoteComCambio:
    lote: Lote
    cambio: Decimal | None  # USD/BRL na compra; None = sem base
    origem: str = ""


@dataclass(frozen=True)
class Conversao:
    moeda_visao: str
    taxa_atual: Decimal  # USD/BRL usada agora
    investido: Decimal
    valor_atual: Decimal
    resultado: Decimal
    resultado_ativo: Decimal
    efeito_cambial: Decimal
    retorno_pct: Decimal
    taxa_media_compra: Decimal  # USD/BRL médio ponderado das compras
    lotes_por_origem: dict[str, int]


def taxa_para_visao(moeda_nativa: str, moeda_visao: str, usd_brl: Decimal) -> Decimal:
    """Taxa multiplicativa nativa→visão a partir do USD/BRL."""
    if moeda_nativa == moeda_visao:
        return UM
    if (moeda_nativa, moeda_visao) == ("USD", "BRL"):
        return usd_brl
    if (moeda_nativa, moeda_visao) == ("BRL", "USD"):
        return UM / usd_brl
    raise ErroDeCalculo(f"par de moedas não suportado: {moeda_nativa}→{moeda_visao}")


def converter(
    lotes: list[LoteComCambio],
    posicao: Posicao,
    avaliacao: Avaliacao,
    moeda_nativa: str,
    moeda_visao: str,
    usd_brl_atual,
) -> Conversao | None:
    """Converte uma posição avaliada. None se algum lote não tem câmbio de compra (sem base)."""
    if moeda_nativa == moeda_visao:
        raise ErroDeCalculo("converter() exige moedas diferentes; use a avaliação nativa")
    f = _decimal(usd_brl_atual, "câmbio atual")
    if f <= ZERO:
        raise ErroDeCalculo(f"câmbio atual deve ser positivo: {f}")
    origens: dict[str, int] = {}
    investido_v = ZERO
    for item in lotes:
        if item.cambio is None or item.cambio <= ZERO:
            return None
        r_i = taxa_para_visao(moeda_nativa, moeda_visao, item.cambio)
        investido_v += item.lote.investido * r_i
        origens[item.origem] = origens.get(item.origem, 0) + 1
    if posicao.investido <= ZERO:
        return None
    r = taxa_para_visao(moeda_nativa, moeda_visao, f)
    taxa_media = investido_v / posicao.investido  # R̄ na direção nativa→visão
    valor_v = posicao.quantidade * avaliacao.preco_atual * r
    resultado_ativo = posicao.quantidade * (avaliacao.preco_atual - posicao.preco_medio) * r
    efeito = posicao.quantidade * posicao.preco_medio * (r - taxa_media)
    resultado = valor_v - investido_v
    # USD/BRL médio das compras, para exibição (independe da direção)
    usd_brl_medio = taxa_media if moeda_nativa == "USD" else UM / taxa_media
    return Conversao(
        moeda_visao=moeda_visao,
        taxa_atual=f,
        investido=investido_v,
        valor_atual=valor_v,
        resultado=resultado,
        resultado_ativo=resultado_ativo,
        efeito_cambial=efeito,
        retorno_pct=Decimal("100") * resultado / investido_v,
        taxa_media_compra=usd_brl_medio,
        lotes_por_origem=origens,
    )
