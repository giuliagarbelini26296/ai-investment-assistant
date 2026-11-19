"""
Serviços da carteira: operações sobre lotes com trilha de revisões, e montagem de posições
usando o cálculo puro (`apps.valuation.core` e `apps.valuation.fx`).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.valuation import core, fx

from .models import Asset, Lot, LotRevision, PositionBalance

CAMPOS_EDITAVEIS = ("trade_date", "quantity", "unit_price", "fx_rate", "costs", "notes")
MOEDAS_VISAO = ("USD", "BRL")


@transaction.atomic
def registrar_saldo(asset, *, reference_date, quantity, average_price, source_key, evidence, invested_cost=None):
    """Concilia sem criar compras; a mesma fonte é idempotente e não sobrescreve dados."""
    asset = Asset.objects.select_for_update().get(pk=asset.pk)
    existing = PositionBalance.objects.filter(asset=asset, source_key=source_key).first()
    if existing:
        if (existing.reference_date, existing.quantity, existing.average_price, existing.invested_cost) != (
            reference_date, quantity, average_price, invested_cost
        ):
            raise ValueError("A fonte já foi conciliada com outros valores; use uma nova referência.")
        return existing
    balance = PositionBalance(
        asset=asset, reference_date=reference_date, quantity=quantity, average_price=average_price,
        source_key=source_key, evidence=evidence, invested_cost=invested_cost,
        covered_lots=[{"id": lot.pk, "revision": lot.revision, **lot.snapshot()} for lot in asset.lots.all()],
    )
    balance.full_clean()
    balance.save()
    return balance


@transaction.atomic
def criar_lote(asset: Asset, user, **dados) -> Lot:
    # Serialize purchase creation with balance reconciliation for the same asset.
    asset = Asset.objects.select_for_update().get(pk=asset.pk)
    lot = Lot(asset=asset, created_by=user, **dados)
    lot.full_clean()
    lot.save()
    if asset.acompanhamento:  # comprou: passa a ser ativo da carteira (coleta de 15 min)
        asset.acompanhamento = False
        asset.save(update_fields=["acompanhamento", "updated_at"])
    return lot


@transaction.atomic
def alterar_lote(lot: Lot, user, reason: str = "", **dados) -> Lot:
    """Aplica alterações a um lote guardando o estado anterior como revisão."""
    if lot.is_cancelled:
        raise ValueError("Lote cancelado não pode ser alterado.")
    # O estado anterior vem do banco: o objeto em memória pode já ter sido alterado por um formulário.
    original = Lot.objects.get(pk=lot.pk)
    LotRevision.objects.create(
        lot=lot, number=original.revision, previous=original.snapshot(), reason=reason, changed_by=user
    )
    lot.revision = original.revision
    for campo in CAMPOS_EDITAVEIS:
        if campo in dados:
            setattr(lot, campo, dados[campo])
    lot.revision += 1
    lot.full_clean()
    lot.save()
    return lot


@transaction.atomic
def cancelar_lote(lot: Lot, user, reason: str = "") -> Lot:
    if lot.is_cancelled:
        return lot
    LotRevision.objects.create(
        lot=lot,
        number=lot.revision,
        previous=lot.snapshot(),
        reason=reason or "cancelamento",
        changed_by=user,
    )
    lot.revision += 1
    lot.cancelado_em = timezone.now()
    lot.save(update_fields=["revision", "cancelado_em", "updated_at"])
    return lot


# --- Posições e visão da carteira ------------------------------------------------------------


@dataclass
class LinhaCarteira:
    asset: Asset
    posicao: core.Posicao | None
    avaliacao: core.Avaliacao | None  # None quando não há cotação válida
    motivo_sem_preco: str = "sem cotação (fonte: nenhuma)"
    atualidade: object = None  # apps.marketdata.freshness.Atualidade
    conversao: fx.Conversao | None = None  # None: moeda nativa == visão, sem preço, ou sem base cambial
    sem_base_cambial: bool = False
    alerta_ativo: bool = False
    lotes_fx: list[fx.LoteComCambio] = field(default_factory=list)

    @property
    def moeda_nativa(self) -> str:
        return self.asset.native_currency

    # Valores na moeda de visualização (nativos quando não há conversão)
    @property
    def investido_visao(self) -> Decimal | None:
        if self.conversao:
            return self.conversao.investido
        return self.posicao.investido if self.posicao and not self.sem_base_cambial else None

    @property
    def valor_atual_visao(self) -> Decimal | None:
        if self.conversao:
            return self.conversao.valor_atual
        return self.avaliacao.valor_atual if self.avaliacao and not self.sem_base_cambial else None

    @property
    def resultado_visao(self) -> Decimal | None:
        if self.conversao:
            return self.conversao.resultado
        return self.avaliacao.resultado if self.avaliacao and not self.sem_base_cambial else None

    @property
    def resultado_para_filtro(self) -> Decimal | None:
        """Sem custo cambial, lucro/prejuízo ainda é conhecido na moeda nativa."""
        resultado = self.resultado_visao
        if resultado is not None:
            return resultado
        return self.avaliacao.resultado if self.avaliacao else None

    @property
    def dados_totais(self) -> dict:
        """Totais na visão disponível; sem câmbio de custo, conserva a moeda original."""
        if self.posicao is None:
            return {}
        convertida = self.conversao is not None
        moeda = self.conversao.moeda_visao if convertida else self.moeda_nativa
        investido = self.conversao.investido if convertida else self.posicao.investido
        valor = self.conversao.valor_atual if convertida else (
            self.avaliacao.valor_atual if self.avaliacao else None
        )
        resultado = valor - investido if valor is not None else None
        dia = dia_base = None
        if self.avaliacao and self.avaliacao.variacao_dia_pct is not None:
            fator = Decimal(1) + self.avaliacao.variacao_dia_pct / Decimal(100)
            if fator > 0:
                dia_base = valor / fator
                dia = valor - dia_base
        return {"moeda": moeda, "investido": investido, "valor": valor,
                "resultado": resultado, "dia": dia, "dia_base": dia_base}

    @property
    def retorno_visao(self) -> Decimal | None:
        if self.conversao:
            return self.conversao.retorno_pct
        return self.avaliacao.retorno_pct if self.avaliacao and not self.sem_base_cambial else None


@dataclass
class VisaoCarteira:
    moeda: str
    cambio: object  # apps.marketdata.fxservice.CambioAtual | None
    linhas: list[LinhaCarteira]
    consolidado: core.Consolidado
    sem_base: list[str]  # símbolos excluídos por falta de câmbio de compra
    sem_cambio_atual: bool  # conversão impossível por falta de câmbio atual
    por_classe: dict[str, core.Consolidado]


def totais_das_linhas(linhas) -> list[dict]:
    """Soma por moeda. Retorno ponderado apenas pelo custo das posições avaliadas."""
    grupos = {}
    for linha in linhas:
        dados = linha.dados_totais
        if not dados:
            continue
        grupo = grupos.setdefault(dados["moeda"], {
            "moeda": dados["moeda"], "investido": Decimal(0), "investido_avaliado": Decimal(0),
            "valor": Decimal(0), "resultado": Decimal(0), "dia": Decimal(0), "dia_base": Decimal(0),
            "posicoes": 0, "avaliadas": 0, "com_dia": 0,
        })
        grupo["posicoes"] += 1
        grupo["investido"] += dados["investido"]
        if dados["valor"] is not None:
            grupo["avaliadas"] += 1
            grupo["investido_avaliado"] += dados["investido"]
            grupo["valor"] += dados["valor"]
            grupo["resultado"] += dados["resultado"]
        if dados["dia"] is not None:
            grupo["com_dia"] += 1
            grupo["dia"] += dados["dia"]
            grupo["dia_base"] += dados["dia_base"]
    for grupo in grupos.values():
        grupo["retorno"] = (
            Decimal(100) * grupo["resultado"] / grupo["investido_avaliado"]
            if grupo["investido_avaliado"] else None
        )
        grupo["dia_pct"] = Decimal(100) * grupo["dia"] / grupo["dia_base"] if grupo["dia_base"] else None
        if not grupo["avaliadas"]:
            grupo["valor"] = grupo["resultado"] = None
        if not grupo["com_dia"]:
            grupo["dia"] = None
    return [grupos[moeda] for moeda in sorted(grupos)]


def posicao_do_ativo(asset: Asset) -> core.Posicao | None:
    return _posicao_com_saldo(asset, list(asset.active_lots))


def lotes_apos_saldo(asset, lots):
    balance = asset.position_balance
    covered = balance.covered_lot_ids if balance else set()
    return [lot for lot in lots if not lot.is_cancelled and lot.pk not in covered]


def _posicao_com_saldo(asset, lots):
    effective = lotes_apos_saldo(asset, lots)
    lotes = [core.Lote(lot.quantity, lot.unit_price) for lot in effective]
    balance = asset.position_balance
    if balance and balance.quantity > 0:
        lotes.insert(0, core.Lote(balance.quantity, balance.average_price))
    pos = core.posicao(lotes)
    if pos and balance and balance.invested_cost is not None:
        invested = balance.invested_cost + sum((lot.quantity * lot.unit_price for lot in effective), Decimal(0))
        pos = dataclasses.replace(pos, investido=invested, preco_medio=invested / pos.quantidade)
    # O saldo não conta como uma negociação ou um lote comprado.
    return dataclasses.replace(pos, lotes=len(effective)) if pos and balance else pos


def lotes_com_cambio(asset: Asset, lots) -> list[fx.LoteComCambio]:
    from apps.marketdata import fxservice

    saida = []
    balance = asset.position_balance
    if balance and balance.quantity > 0:
        # Sem datas/câmbio de aquisição, a conversão do custo permanece indisponível.
        # Não usar PTAX da referência do saldo como se fosse câmbio de compra.
        preco_base = (balance.invested_cost / balance.quantity
                      if balance.invested_cost is not None else balance.average_price)
        saida.append(fx.LoteComCambio(core.Lote(balance.quantity, preco_base), None, "sem_base"))
    for lot in lotes_apos_saldo(asset, lots):
        valor, origem = fxservice.cambio_de_compra_para_visao(lot)
        saida.append(fx.LoteComCambio(core.Lote(lot.quantity, lot.unit_price), valor, origem))
    return saida


def linhas_da_carteira(
    precos: dict[int, tuple] | None = None, agora=None, moeda_visao: str | None = None, cambio=None
) -> list[LinhaCarteira]:
    """Monta as linhas do dashboard.

    `precos`: {asset_id: (preco_atual, fechamento_anterior)} — se None, usa a última cotação válida
    de cada ativo (apps.marketdata). `moeda_visao`: converte as linhas cuja moeda nativa difere, usando
    `cambio` (CambioAtual) e o câmbio de compra de cada lote; sem câmbio de compra → "sem base cambial".
    """
    from apps.alerts import services as alert_services
    from apps.marketdata import freshness

    agora = agora or timezone.now()
    com_alerta = alert_services.ativos_com_alerta()
    linhas: list[LinhaCarteira] = []
    for asset in Asset.objects.filter(is_active=True).exclude(market="CAMBIO").prefetch_related(
        "lots", "position_balances"
    ):
        lots = [lot for lot in asset.lots.all() if not lot.is_cancelled]
        pos = _posicao_com_saldo(asset, lots)
        aval = None
        atual = None
        motivo = "sem cotação (fonte: nenhuma)"
        if precos is not None:
            if pos is not None and asset.id in precos:
                preco, fechamento = precos[asset.id]
                aval = core.avaliar(pos, preco, fechamento)
        else:
            atual = freshness.atualidade(asset, agora)
            if atual.cotacao is not None and pos is not None:
                q = atual.cotacao
                aval = core.avaliar(pos, q.price, q.previous_close)
                if aval.variacao_dia_pct is None and q.change_24h_pct is not None:
                    # cripto: a exchange informa a variação 24 h em vez de fechamento anterior
                    aval = dataclasses.replace(aval, variacao_dia_pct=q.change_24h_pct)
                if not atual.permite_alertas:
                    aval = dataclasses.replace(aval, variacao_dia_pct=None)
            motivo = atual.descricao
        linha = LinhaCarteira(
            asset=asset,
            posicao=pos,
            avaliacao=aval,
            motivo_sem_preco=motivo,
            atualidade=atual,
            alerta_ativo=asset.id in com_alerta,
        )
        if moeda_visao and pos is not None and asset.native_currency != moeda_visao:
            linha.lotes_fx = lotes_com_cambio(asset, lots)
            if any(item.cambio is None for item in linha.lotes_fx):
                linha.sem_base_cambial = True
            elif aval is not None and cambio is not None:
                linha.conversao = fx.converter(
                    linha.lotes_fx, pos, aval, asset.native_currency, moeda_visao, cambio.valor
                )
            elif aval is not None and cambio is None:
                linha.sem_base_cambial = True  # sem câmbio atual: visão convertida indisponível
        linhas.append(linha)
    return linhas


def consolidar_por_moeda(linhas: list[LinhaCarteira]) -> dict[str, core.Consolidado]:
    """Totais por moeda nativa."""
    por_moeda: dict[str, list] = {}
    for linha in linhas:
        if linha.posicao is None:
            continue
        por_moeda.setdefault(linha.asset.native_currency, []).append(
            (linha.asset.symbol, linha.posicao, linha.avaliacao)
        )
    return {moeda: core.consolidar(moeda, itens) for moeda, itens in sorted(por_moeda.items())}


def _consolidar_visao(moeda: str, linhas: list[LinhaCarteira]) -> core.Consolidado:
    """Consolida na moeda de visualização: posições sem base cambial ficam fora (contadas à parte)."""
    c = core.Consolidado(moeda=moeda)
    for linha in linhas:
        if linha.posicao is None or linha.sem_base_cambial:
            continue
        c.posicoes_total += 1
        investido = linha.investido_visao
        c.investido_total += investido
        if linha.valor_atual_visao is None:
            c.sem_preco.append(linha.asset.symbol)
            continue
        c.posicoes_avaliadas += 1
        c.investido_avaliado += investido
        c.valor_atual += linha.valor_atual_visao
        c.resultado += linha.resultado_visao
    if c.investido_avaliado > 0:
        c.retorno_pct = Decimal("100") * c.resultado / c.investido_avaliado
    return c


def visao_da_carteira(moeda: str, agora=None) -> VisaoCarteira:
    from apps.marketdata import fxservice

    if moeda not in MOEDAS_VISAO:
        raise ValueError(f"moeda de visualização inválida: {moeda}")
    agora = agora or timezone.now()
    cambio = fxservice.cambio_atual(agora)
    linhas = linhas_da_carteira(agora=agora, moeda_visao=moeda, cambio=cambio)
    com_posicao = [linha for linha in linhas if linha.posicao]
    sem_base = [linha.asset.symbol for linha in com_posicao if linha.sem_base_cambial]
    precisa_cambio = any(linha.asset.native_currency != moeda for linha in com_posicao)
    por_classe = {}
    for classe in ("acao", "cripto"):
        grupo = [linha for linha in com_posicao if linha.asset.asset_class == classe]
        if grupo:
            por_classe[classe] = _consolidar_visao(moeda, grupo)
    return VisaoCarteira(
        moeda=moeda,
        cambio=cambio,
        linhas=com_posicao,
        consolidado=_consolidar_visao(moeda, com_posicao),
        sem_base=sem_base,
        sem_cambio_atual=precisa_cambio and cambio is None,
        por_classe=por_classe,
    )
