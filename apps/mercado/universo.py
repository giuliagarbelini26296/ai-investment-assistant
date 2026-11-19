"""
Universo monitorado pelo menu Mercado: lista curada editável + carteira + favoritos.

A lista sugerida é só um ponto de partida (Big Techs, ETFs amplos e setoriais, principais criptos);
o usuário inclui e remove tickers. Ativos criados aqui ficam como "só acompanhamento" (sem compra):
ações dos EUA nessa condição recebem preço pelo rodízio horário de candles (ver coleta.py).
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction

from apps.portfolio.models import Asset, Custody, Market


@dataclass(frozen=True)
class ItemUniverso:
    simbolo: str
    nome: str
    mercado: str
    setor: str
    etf: bool = False
    grupo: str = ""


UNIVERSO_SUGERIDO: tuple[ItemUniverso, ...] = (
    ItemUniverso("AAPL", "Apple Inc.", Market.NASDAQ, "Tecnologia", grupo="Big Techs"),
    ItemUniverso("MSFT", "Microsoft Corporation", Market.NASDAQ, "Tecnologia", grupo="Big Techs"),
    ItemUniverso("NVDA", "NVIDIA Corporation", Market.NASDAQ, "Semicondutores", grupo="Big Techs"),
    ItemUniverso("AMZN", "Amazon.com Inc.", Market.NASDAQ, "Consumo e nuvem", grupo="Big Techs"),
    ItemUniverso(
        "GOOGL", "Alphabet Inc. (Classe A)", Market.NASDAQ, "Comunicação e internet", grupo="Big Techs"
    ),
    ItemUniverso("META", "Meta Platforms Inc.", Market.NASDAQ, "Comunicação e internet", grupo="Big Techs"),
    ItemUniverso("TSLA", "Tesla Inc.", Market.NASDAQ, "Automotivo", grupo="Big Techs"),
    ItemUniverso("AVGO", "Broadcom Inc.", Market.NASDAQ, "Semicondutores", grupo="Big Techs"),
    ItemUniverso("NFLX", "Netflix Inc.", Market.NASDAQ, "Comunicação e internet"),
    ItemUniverso("AMD", "Advanced Micro Devices Inc.", Market.NASDAQ, "Semicondutores"),
    ItemUniverso("ORCL", "Oracle Corporation", Market.NYSE, "Tecnologia"),
    ItemUniverso("CRM", "Salesforce Inc.", Market.NYSE, "Tecnologia"),
    ItemUniverso("ADBE", "Adobe Inc.", Market.NASDAQ, "Tecnologia"),
    ItemUniverso("INTC", "Intel Corporation", Market.NASDAQ, "Semicondutores"),
    ItemUniverso("TSM", "Taiwan Semiconductor Manufacturing (ADR)", Market.NYSE, "Semicondutores"),
    ItemUniverso("PLTR", "Palantir Technologies Inc.", Market.NASDAQ, "Tecnologia"),
    ItemUniverso("SPY", "SPDR S&P 500 ETF Trust", Market.NYSE, "ETF — índice amplo", True, "ETFs"),
    ItemUniverso("VOO", "Vanguard S&P 500 ETF", Market.NYSE, "ETF — índice amplo", True, "ETFs"),
    ItemUniverso("VTI", "Vanguard Total Stock Market ETF", Market.NYSE, "ETF — índice amplo", True, "ETFs"),
    ItemUniverso("QQQ", "Invesco QQQ Trust (Nasdaq-100)", Market.NASDAQ, "ETF — tecnologia", True, "ETFs"),
    ItemUniverso("DIA", "SPDR Dow Jones Industrial Average ETF", Market.NYSE, "ETF — índice amplo", True),
    ItemUniverso("IWM", "iShares Russell 2000 ETF", Market.NYSE, "ETF — small caps", True),
    ItemUniverso("XLK", "Technology Select Sector SPDR Fund", Market.NYSE, "ETF — tecnologia", True),
    ItemUniverso("SMH", "VanEck Semiconductor ETF", Market.NASDAQ, "ETF — semicondutores", True),
    ItemUniverso("ARKK", "ARK Innovation ETF", Market.NYSE, "ETF — inovação", True),
    ItemUniverso("GLD", "SPDR Gold Shares", Market.NYSE, "ETF — ouro", True),
    ItemUniverso("TLT", "iShares 20+ Year Treasury Bond ETF", Market.NASDAQ, "ETF — renda fixa EUA", True),
    ItemUniverso("VNQ", "Vanguard Real Estate ETF", Market.NYSE, "ETF — imobiliário", True),
    ItemUniverso("BTC", "Bitcoin", Market.CRYPTO, "Cripto", grupo="Criptomoedas"),
    ItemUniverso("ETH", "Ethereum", Market.CRYPTO, "Cripto", grupo="Criptomoedas"),
    ItemUniverso("SOL", "Solana", Market.CRYPTO, "Cripto", grupo="Criptomoedas"),
    ItemUniverso("BNB", "BNB", Market.CRYPTO, "Cripto"),
    ItemUniverso("XRP", "XRP", Market.CRYPTO, "Cripto"),
    ItemUniverso("ADA", "Cardano", Market.CRYPTO, "Cripto"),
    ItemUniverso("DOGE", "Dogecoin", Market.CRYPTO, "Cripto"),
    ItemUniverso("AVAX", "Avalanche", Market.CRYPTO, "Cripto"),
    ItemUniverso("LINK", "Chainlink", Market.CRYPTO, "Cripto"),
    ItemUniverso("DOT", "Polkadot", Market.CRYPTO, "Cripto"),
)

MERCADOS_DO_UNIVERSO = (Market.NYSE, Market.NASDAQ, Market.CRYPTO, Market.B3)


def buscar(simbolo: str, mercado: str) -> Asset | None:
    """Mesmo ticker em NYSE/Nasdaq é o mesmo papel: procura nos dois."""
    simbolo = simbolo.strip().upper()
    mercados = (Market.NYSE, Market.NASDAQ) if mercado in (Market.NYSE, Market.NASDAQ) else (mercado,)
    return Asset.objects.filter(symbol=simbolo, market__in=mercados).order_by("-is_active", "id").first()


@transaction.atomic
def garantir_ativo(
    simbolo: str, mercado: str, nome: str = "", setor: str = "", etf: bool = False
) -> tuple[Asset, bool]:
    """Devolve o ativo existente (reativando se preciso) ou cria um "só acompanhamento"."""
    existente = buscar(simbolo, mercado)
    if existente is not None:
        mudou = []
        if not existente.is_active:
            existente.is_active = True
            mudou.append("is_active")
        if setor and not existente.sector:
            existente.sector = setor
            mudou.append("sector")
        if etf and not existente.is_etf:
            existente.is_etf = True
            mudou.append("is_etf")
        if mudou:
            existente.save(update_fields=[*mudou, "updated_at"])
        return existente, False
    asset = Asset(
        symbol=simbolo.strip().upper(),
        name=nome or simbolo.strip().upper(),
        market=mercado,
        custody=Custody.NONE,
        sector=setor,
        is_etf=etf,
        acompanhamento=True,
    )
    asset.full_clean()
    asset.save()
    return asset, True


def semear_universo(user=None) -> dict:
    """Cria os ativos da lista sugerida que ainda não existem; com `user`, cria os grupos de favoritos."""
    from .models import Favorito, GrupoFavoritos

    criados, existentes = [], []
    grupos: dict[str, GrupoFavoritos] = {}
    for item in UNIVERSO_SUGERIDO:
        asset, novo = garantir_ativo(item.simbolo, item.mercado, item.nome, item.setor, item.etf)
        (criados if novo else existentes).append(asset.symbol)
        if user is not None and item.grupo:
            if item.grupo not in grupos:
                grupos[item.grupo], _ = GrupoFavoritos.objects.get_or_create(
                    user=user, nome=item.grupo, defaults={"ordem": len(grupos)}
                )
            fav, _ = Favorito.objects.get_or_create(user=user, asset=asset)
            fav.grupos.add(grupos[item.grupo])
    return {"criados": criados, "existentes": existentes}


def ativos_do_universo():
    """Todos os ativos ativos que o Mercado avalia (exceto o pseudo-ativo do dólar)."""
    return Asset.objects.filter(is_active=True, market__in=MERCADOS_DO_UNIVERSO).order_by("symbol")
