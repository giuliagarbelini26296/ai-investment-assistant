"""
Avaliação de regras e ciclo de vida dos alertas.

Semântica (proposta v1.1, seção 9):
- Nova versão de regra → estado zerado; a primeira cotação elegível pode disparar.
- Depois, dispara apenas na transição falso → verdadeiro. Condição persistente mantém o alerta ativo;
  retorno a falso resolve o alerta e rearma a regra. Leitura ("visto") não resolve nem rearma.
- Atualidade que não permite alertas (desatualizado, desconhecido, sem cotação) → avaliação suspensa:
  não resolve nem rearma. Na retomada, avalia contra o último estado válido.
- Percentuais na moeda nativa; preço absoluto compara na moeda da regra (USDT é tratado como USD).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.marketdata import freshness
from apps.marketdata.models import Quote
from apps.portfolio import services as portfolio_services
from apps.portfolio.models import Asset
from apps.valuation import core

from .models import CONDICOES_SEM_PRECO, Alert, Condition, Family, Rule, RuleState, RuleVersion

MOEDA_EQUIVALENTE = {"USDT": "USD"}


# --- Regras -------------------------------------------------------------------------------------


@transaction.atomic
def criar_regra(user, *, name, asset=None, reason="", **parametros) -> Rule:
    rule = Rule.objects.create(name=name, asset=asset)
    nova_versao(rule, user, reason=reason or "criação", **parametros)
    return rule


@transaction.atomic
def nova_versao(rule: Rule, user, *, reason="", **parametros) -> RuleVersion:
    """Edição = nova versão imutável; estados anteriores deixam de ser avaliados."""
    versao = RuleVersion(
        rule=rule, number=rule.current_version + 1, created_by=user, reason=reason, **parametros
    )
    versao.full_clean()
    versao.save()
    rule.current_version = versao.number
    rule.save(update_fields=["current_version", "updated_at"])
    # alertas ativos da versão anterior são resolvidos: a regra mudou
    agora = timezone.now()
    for estado in RuleState.objects.filter(version__rule=rule, active_alert__isnull=False).select_related(
        "active_alert"
    ):
        estado.active_alert.resolved_at = agora
        estado.active_alert.save(update_fields=["resolved_at"])
        estado.active_alert = None
        estado.save(update_fields=["active_alert"])
    return versao


@transaction.atomic
def desativar_regra(rule: Rule) -> None:
    rule.is_active = False
    rule.save(update_fields=["is_active", "updated_at"])
    agora = timezone.now()
    for estado in RuleState.objects.filter(version__rule=rule, active_alert__isnull=False).select_related(
        "active_alert"
    ):
        estado.active_alert.resolved_at = agora
        estado.active_alert.save(update_fields=["resolved_at"])
        estado.active_alert = None
        estado.save(update_fields=["active_alert"])


def versoes_aplicaveis(asset: Asset) -> list[RuleVersion]:
    regras = (
        Rule.objects.filter(is_active=True).filter(models_q_global_ou_ativo(asset)).select_related("asset")
    )
    saida = []
    for rule in regras:
        v = rule.versao_atual
        if v is not None:
            saida.append(v)
    return saida


def models_q_global_ou_ativo(asset: Asset):
    from django.db.models import Q

    return Q(asset__isnull=True) | Q(asset=asset)


# --- Avaliação ------------------------------------------------------------------------------------


@dataclass
class ResultadoCondicao:
    avaliavel: bool
    satisfeita: bool
    valores: dict
    motivo: str = ""


def avaliar_condicao(
    versao: RuleVersion,
    aval: core.Avaliacao | None,
    cotacao: Quote | None,
    asset: Asset | None = None,
    agora: dt.datetime | None = None,
) -> ResultadoCondicao:
    """Compara valores exatos (sem arredondamento) com o limite da versão.

    `aval` é None para ativos sem posição (favoritos, universo do Mercado, dólar): nesse caso só as famílias
    que não dependem do preço médio são avaliáveis.
    """
    x = versao.threshold
    base = {"limite": str(x)}
    if cotacao is not None:
        base.update(
            {
                "preco": str(cotacao.price),
                "moeda_cotacao": cotacao.currency,
                "fonte": cotacao.source,
                "horario_mercado": cotacao.market_time.isoformat() if cotacao.market_time else None,
                "horario_coleta": cotacao.collected_at.isoformat(),
                "tipo_cotacao": cotacao.quote_type,
            }
        )
    if aval is not None:
        base.update(
            {"preco_medio": str(aval.posicao.preco_medio), "quantidade": str(aval.posicao.quantidade)}
        )
    if versao.family == Family.MARKET:
        return _avaliar_mercado(versao, asset, cotacao, agora or timezone.now(), base)
    if cotacao is None:
        return ResultadoCondicao(False, False, base, "sem cotação")
    if versao.family == Family.SINCE_PURCHASE:
        if aval is None:
            return ResultadoCondicao(False, False, base, "sem posição no ativo")
        r = aval.retorno_pct
        base["retorno_pct"] = str(r)
        ok = r <= -x if versao.condition == Condition.DROP else r >= x
        return ResultadoCondicao(True, ok, base)
    if versao.family == Family.DAY:
        v = cotacao.change_24h_pct
        if v is None and aval is not None:
            v = aval.variacao_dia_pct
        if v is None and cotacao.previous_close:
            v = (cotacao.price / cotacao.previous_close - 1) * 100
        if v is None:
            return ResultadoCondicao(False, False, base, "sem variação do dia/24 h na cotação")
        base["variacao_pct"] = str(v)
        ok = v <= -x if versao.condition == Condition.DROP else v >= x
        return ResultadoCondicao(True, ok, base)
    moeda = MOEDA_EQUIVALENTE.get(cotacao.currency, cotacao.currency)
    if moeda != versao.currency:
        return ResultadoCondicao(
            False, False, base, f"moeda da cotação ({cotacao.currency}) ≠ moeda da regra ({versao.currency})"
        )
    p = cotacao.price
    ok = p < x if versao.condition == Condition.BELOW else p > x
    return ResultadoCondicao(True, ok, base)


def _avaliar_mercado(versao: RuleVersion, asset: Asset | None, cotacao: Quote | None, agora, base: dict):
    """Sinais de mercado a partir dos candles diários, do calendário de resultados e das recomendações."""
    from apps.mercado import analise
    from apps.mercado import indicadores as ind_mod

    if asset is None:
        return ResultadoCondicao(False, False, base, "regra de mercado precisa de um ativo")
    x = versao.threshold
    cond = versao.condition
    hoje = timezone.localdate(agora)
    if cond == Condition.EARNINGS:
        from apps.mercado.models import EarningsEvent

        prox = EarningsEvent.objects.filter(asset=asset, date__gte=hoje).order_by("date").first()
        if prox is None:
            return ResultadoCondicao(
                False, False, base, "sem data de resultado (Finnhub sem chave ou sem agenda)"
            )
        dias = (prox.date - hoje).days
        base.update({"data_resultado": prox.date.isoformat(), "dias": dias, "fonte": prox.source})
        return ResultadoCondicao(True, dias <= x, base)
    if cond == Condition.RATING:
        from apps.mercado.models import AnalystSnapshot

        snaps = list(AnalystSnapshot.objects.filter(asset=asset, source="finnhub").order_by("-period")[:2])
        if len(snaps) < 2 or snaps[0].pct_compra is None or snaps[1].pct_compra is None:
            return ResultadoCondicao(False, False, base, "menos de dois meses de recomendações (Finnhub)")
        mudanca = snaps[0].pct_compra - snaps[1].pct_compra
        base.update(
            {
                "pct_compra": str(round(snaps[0].pct_compra, 1)),
                "pct_compra_anterior": str(round(snaps[1].pct_compra, 1)),
                "mudanca_pp": str(round(mudanca, 1)),
                "periodo": snaps[0].period.isoformat(),
                "fonte": "finnhub",
            }
        )
        return ResultadoCondicao(True, abs(mudanca) >= x, base)
    if cotacao is None:
        return ResultadoCondicao(False, False, base, "sem cotação")
    barras, fonte, _ = analise._barras(asset)
    preco = float(cotacao.price)
    if cond == Condition.NEAR_LOW:
        ind = ind_mod.calcular(barras, preco, hoje, asset.market == "CRIPTO")
        if ind.minima_52s is None or ind.janela_52s_dias < 180:
            return ResultadoCondicao(
                False, False, base, "menos de 6 meses de candles para a mínima de 52 semanas"
            )
        dist = ind.dist_minima_52s_pct
        base.update(
            {"minima_52s": f"{ind.minima_52s:.6f}", "distancia_pct": f"{dist:.4f}", "fonte_candles": fonte}
        )
        return ResultadoCondicao(True, dist <= float(x), base)
    n = int(x)
    suporte, resistencia = ind_mod.suporte_resistencia(barras, hoje, n)
    if cond == Condition.SUPPORT:
        if suporte is None:
            return ResultadoCondicao(False, False, base, f"menos de {n} sessões completas de candles")
        base.update({"suporte": f"{suporte:.6f}", "sessoes": n, "fonte_candles": fonte})
        return ResultadoCondicao(True, preco < suporte, base)
    if resistencia is None:
        return ResultadoCondicao(False, False, base, f"menos de {n} sessões completas de candles")
    base.update({"resistencia": f"{resistencia:.6f}", "sessoes": n, "fonte_candles": fonte})
    return ResultadoCondicao(True, preco > resistencia, base)


def _assinatura_posicao(asset: Asset) -> str:
    lots = asset.active_lots.values_list("id", "revision")
    signature = ",".join(f"{i}:{r}" for i, r in lots)
    balance = asset.position_balance
    return f"saldo:{balance.pk};{signature}" if balance else signature


@transaction.atomic
def avaliar_ativo(asset: Asset, agora: dt.datetime | None = None) -> list[Alert]:
    """Avalia todas as versões aplicáveis ao ativo e devolve os alertas disparados agora."""
    agora = agora or timezone.now()
    versoes = versoes_aplicaveis(asset)
    if not versoes:
        return []
    atual = freshness.atualidade(asset, agora)
    pos = portfolio_services.posicao_do_ativo(asset)
    disparados: list[Alert] = []
    for versao in versoes:
        global_ = versao.rule.asset_id is None
        if global_ and pos is None and (asset.acompanhamento or asset.market == "CAMBIO"):
            continue  # regra "qualquer ativo da carteira" não se aplica ao que só é acompanhado
        estado, _ = RuleState.objects.select_for_update().get_or_create(version=versao, asset=asset)
        precisa_posicao = global_ or versao.family == Family.SINCE_PURCHASE
        sem_preco = versao.family == Family.MARKET and versao.condition in CONDICOES_SEM_PRECO
        if precisa_posicao and pos is None:
            estado.suspended, estado.suspended_reason = True, "sem posição no ativo"
            estado.save(update_fields=["suspended", "suspended_reason"])
            continue
        if not sem_preco and (not atual.permite_alertas or atual.cotacao is None):
            estado.suspended, estado.suspended_reason = True, atual.descricao[:200]
            estado.save(update_fields=["suspended", "suspended_reason"])
            continue  # não resolve nem rearma
        q = atual.cotacao
        aval = core.avaliar(pos, q.price, q.previous_close) if (pos is not None and q is not None) else None
        res = avaliar_condicao(versao, aval, q, asset, agora)
        if not res.avaliavel:
            estado.suspended, estado.suspended_reason = True, res.motivo[:200]
            estado.save(update_fields=["suspended", "suspended_reason"])
            continue
        anterior = estado.last_condition
        estado.suspended, estado.suspended_reason = False, ""
        estado.last_condition = res.satisfeita
        estado.last_evaluated_at = agora
        if res.satisfeita and not anterior:  # None→True ou False→True dispara
            if estado.active_alert is None:
                alerta = Alert.objects.create(
                    version=versao,
                    asset=asset,
                    quote=q,
                    triggered_at=agora,
                    classification=versao.classification,
                    observed={
                        **res.valores,
                        "atualidade": atual.estado if q is not None else "nao_se_aplica",
                    },
                    position_revision=_assinatura_posicao(asset),
                )
                estado.active_alert = alerta
                disparados.append(alerta)
        elif not res.satisfeita and estado.active_alert is not None:  # True→False resolve e rearma
            estado.active_alert.resolved_at = agora
            estado.active_alert.save(update_fields=["resolved_at"])
            estado.active_alert = None
        estado.save()
    return disparados


def avaliar_ativos(assets, agora: dt.datetime | None = None) -> list[Alert]:
    agora = agora or timezone.now()
    saida = []
    for asset in assets:
        saida.extend(avaliar_ativo(asset, agora))
    return saida


def avaliar_todos(agora: dt.datetime | None = None) -> list[Alert]:
    return avaliar_ativos(Asset.objects.filter(is_active=True), agora)


# --- Painel -------------------------------------------------------------------------------------------


def alertas_ativos():
    return Alert.objects.filter(resolved_at__isnull=True).select_related(
        "asset", "version", "version__rule", "quote"
    )


def marcar_vistos(ids: list[int], agora: dt.datetime | None = None) -> int:
    agora = agora or timezone.now()
    return Alert.objects.filter(id__in=ids, seen_at__isnull=True).update(seen_at=agora)


def ativos_com_alerta() -> set[int]:
    return set(Alert.objects.filter(resolved_at__isnull=True).values_list("asset_id", flat=True))


def nao_vistos(agora: dt.datetime | None = None) -> int:
    agora = agora or timezone.now()
    return (
        Alert.objects.filter(resolved_at__isnull=True, seen_at__isnull=True)
        .exclude(silenced_until__gt=agora)
        .count()
    )


def silenciar(alert_id: int, horas: int = 24, agora: dt.datetime | None = None) -> Alert | None:
    """Marca visto e esconde o destaque por N horas. Não resolve nem rearma a regra."""
    agora = agora or timezone.now()
    alerta = Alert.objects.filter(pk=alert_id, resolved_at__isnull=True).first()
    if alerta is None:
        return None
    alerta.silenced_until = agora + dt.timedelta(hours=max(1, min(horas, 24 * 30)))
    if alerta.seen_at is None:
        alerta.seen_at = agora
    alerta.save(update_fields=["silenced_until", "seen_at"])
    return alerta


def ajustar_limite(rule: Rule, user, novo_limite: Decimal, reason: str = "ajuste rápido") -> RuleVersion:
    """Nova versão só com o limite alterado (demais parâmetros iguais)."""
    v = rule.versao_atual
    return nova_versao(
        rule,
        user,
        reason=reason,
        family=v.family,
        condition=v.condition,
        threshold=novo_limite,
        currency=v.currency,
        classification=v.classification,
    )


def limite_como_texto(valor: Decimal) -> str:
    return f"{valor.normalize():f}"
