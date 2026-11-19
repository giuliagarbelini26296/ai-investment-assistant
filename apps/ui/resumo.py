"""
Resumo da carteira para o painel: resultado do dia, alocação, evolução do patrimônio e sparklines.

Regras de transparência:
- Evolução do patrimônio usa fechamentos diários (DailyBar) e o PTAX de cada data. Em dias sem fechamento
  do ativo (fim de semana, feriado, lacuna da fonte) repete-se o ÚLTIMO fechamento conhecido, até
  RECUO_MAX_DIAS; passado isso, o ativo fica fora daquele dia e a cobertura informa. Nada é interpolado
  nem estimado.
- O resultado do dia deriva da variação já exibida na linha (preço atual vs. fechamento anterior ou
  variação 24 h da exchange), na moeda de visualização.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from django.utils import timezone

from apps.marketdata.models import DailyBar, FxDailyRate, FxRateType
from apps.portfolio.models import Asset, Lot
from apps.portfolio.services import LinhaCarteira, VisaoCarteira, lotes_apos_saldo
from apps.valuation import fx

RECUO_MAX_DIAS = 7
CEM = Decimal("100")


# --- Resultado do dia e alocação ------------------------------------------------------------------


def resultado_do_dia(linha: LinhaCarteira) -> Decimal | None:
    """Valor do dia na moeda de visualização: valor_atual − valor ao fechamento anterior."""
    aval = linha.avaliacao
    valor = linha.valor_atual_visao
    if aval is None or valor is None or aval.variacao_dia_pct is None:
        return None
    fator = CEM + aval.variacao_dia_pct
    if fator <= 0:
        return None
    return valor - valor * CEM / fator


@dataclass
class ResumoDia:
    valor: Decimal = Decimal(0)
    base: Decimal = Decimal(0)  # valor ao fechamento anterior das posições com variação
    posicoes: int = 0
    sem_variacao: list[str] = field(default_factory=list)

    @property
    def pct(self) -> Decimal | None:
        return CEM * self.valor / self.base if self.base > 0 else None


def resumo_do_dia(visao: VisaoCarteira) -> ResumoDia:
    r = ResumoDia()
    for linha in visao.linhas:
        if linha.sem_base_cambial or linha.valor_atual_visao is None:
            continue
        d = resultado_do_dia(linha)
        if d is None:
            r.sem_variacao.append(linha.asset.symbol)
            continue
        r.valor += d
        r.base += linha.valor_atual_visao - d
        r.posicoes += 1
    return r


def alocacao(visao: VisaoCarteira) -> list[dict]:
    """Participação de cada posição no valor atual (moeda de visualização)."""
    total = sum((linha.valor_atual_visao for linha in visao.linhas if linha.valor_atual_visao), Decimal(0))
    itens = []
    for linha in visao.linhas:
        v = linha.valor_atual_visao
        if not v:
            continue
        itens.append(
            {
                "simbolo": linha.asset.symbol,
                "classe": linha.asset.asset_class,
                "valor": v,
                "pct": (CEM * v / total) if total else Decimal(0),
            }
        )
    itens.sort(key=lambda i: i["valor"], reverse=True)
    return itens


# --- Evolução do patrimônio -----------------------------------------------------------------------


@dataclass
class PontoPatrimonio:
    data: dt.date
    valor: Decimal
    investido: Decimal
    ativos: int  # posições avaliadas no dia
    repetidos: int  # posições usando fechamento anterior (sem fechamento na data)


@dataclass
class EvolucaoPatrimonio:
    moeda: str
    dias: int
    pontos: list[PontoPatrimonio] = field(default_factory=list)
    sem_historico: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)


def _fechamentos(asset: Asset, inicio: dt.date, fim: dt.date) -> dict[dt.date, Decimal]:
    barras = DailyBar.objects.filter(
        asset=asset, session_date__gte=inicio - dt.timedelta(days=RECUO_MAX_DIAS), session_date__lte=fim
    ).values_list("session_date", "close")
    return dict(barras)


def _ptax(inicio: dt.date, fim: dt.date) -> dict[dt.date, Decimal]:
    regs = FxDailyRate.objects.filter(
        pair="USD-BRL",
        rate_type=FxRateType.PTAX_CLOSE,
        date__gte=inicio - dt.timedelta(days=RECUO_MAX_DIAS),
        date__lte=fim,
    ).values_list("date", "bid")
    return dict(regs)


def _ultimo_ate(serie: dict[dt.date, Decimal], data: dt.date) -> tuple[Decimal | None, int]:
    """Valor na data ou o último anterior dentro do recuo; devolve (valor, dias de recuo)."""
    for recuo in range(RECUO_MAX_DIAS + 1):
        v = serie.get(data - dt.timedelta(days=recuo))
        if v is not None:
            return v, recuo
    return None, 0


def evolucao_patrimonio(moeda: str, dias: int = 90, agora: dt.datetime | None = None) -> EvolucaoPatrimonio:
    agora = agora or timezone.now()
    hoje = timezone.localtime(agora).date()
    inicio = hoje - dt.timedelta(days=dias - 1)
    ev = EvolucaoPatrimonio(moeda=moeda, dias=dias)
    ativos = list(Asset.objects.filter(is_active=True).prefetch_related("position_balances"))
    lotes = list(Lot.objects.filter(asset__in=ativos, cancelado_em__isnull=True).select_related("asset"))
    balances = {a.pk: a.position_balance for a in ativos if a.position_balance}
    if not lotes and not balances:
        return ev
    if balances:
        ev.avisos.append(
            "saldos conciliados entram apenas a partir da data de referência; "
            "datas de aquisição e evolução anterior não foram inferidas"
        )
    fech = {a.id: _fechamentos(a, inicio, hoje) for a in ativos}
    ptax = _ptax(inicio, hoje)
    for a in ativos:
        if not fech[a.id] and (a.pk in balances or any(lot.asset_id == a.id for lot in lotes)):
            ev.sem_historico.append(a.symbol)
    faltou_ptax = False
    data = inicio
    while data <= hoje:
        valor = Decimal(0)
        investido = Decimal(0)
        n = repetidos = 0
        usd_brl, _ = _ultimo_ate(ptax, data)
        for a in ativos:
            lts = [lot for lot in lotes if lot.asset_id == a.id and lot.trade_date <= data]
            balance = balances.get(a.pk)
            saldo_vigente = balance is not None and data >= balance.reference_date
            if saldo_vigente:
                lts = lotes_apos_saldo(a, lts)
            if not lts and not saldo_vigente:
                continue
            qtd = sum((lot.quantity for lot in lts), Decimal(0))
            custo = sum((lot.quantity * lot.unit_price for lot in lts), Decimal(0))
            if saldo_vigente:
                qtd += balance.quantity
                custo += (balance.invested_cost if balance.invested_cost is not None
                          else balance.quantity * balance.average_price)
            close, recuo = _ultimo_ate(fech[a.id], data)
            if close is None:
                continue
            if a.native_currency == moeda:
                taxa = Decimal(1)
            elif usd_brl is None:
                faltou_ptax = True
                continue
            else:
                taxa = fx.taxa_para_visao(a.native_currency, moeda, usd_brl)
            valor += qtd * close * taxa
            # investido convertido pela mesma taxa do dia: mede a variação de preço + câmbio do dia
            investido += custo * taxa
            n += 1
            repetidos += 1 if recuo else 0
        if n:
            ev.pontos.append(PontoPatrimonio(data, valor, investido, n, repetidos))
        data += dt.timedelta(days=1)
    if ev.sem_historico:
        ev.avisos.append("sem histórico de fechamentos: " + ", ".join(ev.sem_historico))
    if faltou_ptax:
        ev.avisos.append("dias sem PTAX disponível ficaram sem as posições em outra moeda")
    if any(p.repetidos for p in ev.pontos):
        ev.avisos.append("dias sem fechamento repetem o último fechamento conhecido (até 7 dias)")
    return ev


def evolucao_como_json(ev: EvolucaoPatrimonio) -> dict:
    return {
        "moeda": ev.moeda,
        "dias": ev.dias,
        "pontos": [
            {
                "data": p.data.isoformat(),
                "valor": str(p.valor.quantize(Decimal("0.01"))),
                "investido": str(p.investido.quantize(Decimal("0.01"))),
                "ativos": p.ativos,
                "repetidos": p.repetidos,
            }
            for p in ev.pontos
        ],
        "avisos": ev.avisos,
        "metodo": "fechamentos diários × PTAX da data; sem interpolação",
    }


# --- Sparklines -----------------------------------------------------------------------------------


def fechamentos_recentes(asset: Asset, dias: int = 7) -> list[Decimal]:
    barras = (
        DailyBar.objects.filter(asset=asset).order_by("-session_date").values_list("close", flat=True)[:dias]
    )
    return list(reversed(list(barras)))


def sparkline_svg(valores: list[Decimal], largura: int = 90, altura: int = 24) -> str:
    """Polilinha SVG inline com os fechamentos; cor pela direção (primeiro → último). Vazio se < 2 pontos."""
    if len(valores) < 2:
        return ""
    vmin, vmax = min(valores), max(valores)
    amplitude = vmax - vmin or Decimal(1)
    passo = Decimal(largura - 2) / (len(valores) - 1)
    pontos = []
    for i, v in enumerate(valores):
        x = Decimal(1) + passo * i
        y = Decimal(altura - 2) - (v - vmin) / amplitude * Decimal(altura - 4) + 1
        pontos.append(f"{x:.1f},{y:.1f}")
    classe = "alta" if valores[-1] >= valores[0] else "queda"
    rotulo = f"últimos {len(valores)} fechamentos"
    return (
        f'<svg class="sparkline {classe}" viewBox="0 0 {largura} {altura}" '
        f'width="{largura}" height="{altura}" role="img" aria-label="{rotulo}">'
        f'<polyline fill="none" stroke="currentColor" stroke-width="1.5" points="{" ".join(pontos)}"/></svg>'
    )
