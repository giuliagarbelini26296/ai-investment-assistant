"""Reconstrução da Evolução antes do primeiro registro diário (novembro/2026).

Fonte das posições: "Histórico de movimentações" de cada ativo no BTG (quantidade e valor aplicado
após cada operação), mais os registros do portal (saldos
conciliados, compras e vendas) onde o BTG não foi consultado. Preços: fechamentos reais do portal
(DailyBar); quando o fechamento da B3 ainda não está na base, o 'previous_close' das cotações do
pregão seguinte. BTC em reais: fechamento em dólar × PTAX do dia.

Nada é estimado: um dia sem fechamento de algum ativo fica marcado como incompleto.
"""
import datetime as dt
from collections import defaultdict
from decimal import Decimal as D

from django.utils import timezone

Z = D(0)
_d = lambda dia: dt.date(2026, 11, dia)  # noqa: E731

INICIO = {'ana': _d(3), 'bruno': _d(5)}

# Dados de DEMONSTRAÇÃO (fictícios): (data, quantidade após a operação, valor aplicado após a operação).
# Na instalação pessoal estes valores vinham do histórico da corretora; aqui foram substituídos.
POSICOES_BTG = {
    ('ana', 'ITUB4'): [(_d(3), '100', '3890.00'), (_d(4), '200', '7795.40')],
    ('ana', 'SBSP3'): [(_d(3), '50', '1190.50'), (_d(9), '80', '1905.20')],
    ('ana', 'A1MD34'): [(_d(6), '20', '5980.00'), (_d(13), '12', '3588.00')],
    ('ana', 'NVDC34'): [(_d(9), '150', '3100.50'), (_d(12), '300', '6210.75')],
    ('bruno', 'BBAS3'): [(_d(5), '150', '2445.00')],
    ('bruno', 'ITUB4'): [(_d(5), '120', '4674.00')],
    ('bruno', 'BPAC11'): [(_d(5), '200', '10820.00'), (_d(16), '260', '14050.40')],
    ('bruno', 'NVDC34'): [(_d(5), '80', '1650.40'), (_d(11), '200', '4112.00')],
    ('bruno', 'MSFT34'): [(_d(11), '10', '1210.00')],
    ('bruno', 'BTC'): [(_d(5), '0.01000000', '4300.00'), (_d(17), '0.02500000', '10850.00')],
}
# Proventos de DEMONSTRAÇÃO: (conta, moeda, data) -> valor.
PROVENTOS_BTG = {('ana', 'BRL', _d(10)): D('31.20')}  # SBSP3


class Precos:
    def __init__(self, fim):
        from apps.insights.models import BenchmarkBar
        self.fim = fim
        self.inicio = _d(1) - dt.timedelta(days=5)
        self.ref = {s: {b.date: b.close for b in BenchmarkBar.objects.filter(symbol=s, date__range=(self.inicio, fim))}
                    for s in ('BOVA11', 'SPY')}
        self.cache = {}
        self.de_cotacao = set()

    def pregoes(self, moeda):
        return sorted(self.ref['BOVA11' if moeda == 'BRL' else 'SPY'])

    def _cotacoes(self, asset):
        """Fechamento do pregão d = 'previous_close' das cotações do pregão seguinte."""
        from apps.marketdata.models import Quote
        tz = timezone.get_current_timezone()
        por_dia = {}
        for q in (Quote.objects.filter(asset=asset, market_time__date__gte=self.inicio)
                  .exclude(previous_close=None).order_by('market_time')):
            if q.market_time.astimezone(tz).hour >= 11:  # pré-abertura pode repetir o fechamento antigo
                por_dia[timezone.localdate(q.market_time)] = q.previous_close
        pregoes = self.pregoes('BRL') + [self.fim + dt.timedelta(days=1)]
        out = {}
        for dia, valor in por_dia.items():
            anterior = max((p for p in pregoes if p < dia), default=None)
            if anterior:
                out[anterior] = valor
        return out

    def fechamentos(self, asset):
        if asset.pk not in self.cache:
            from apps.marketdata.history import fonte_principal
            from apps.marketdata.models import DailyBar
            s = {b.session_date: b.close for b in DailyBar.objects.filter(
                asset=asset, source=fonte_principal(asset), provisional=False, session_date__range=(self.inicio, self.fim))}
            if asset.market == 'B3':
                for d, c in self._cotacoes(asset).items():
                    if d <= self.fim and d not in s:
                        s[d] = c
                        self.de_cotacao.add((asset.symbol, d))
            self.cache[asset.pk] = s
        return self.cache[asset.pk]

    def preco(self, asset, d, moeda):
        c = self.fechamentos(asset).get(d)
        if c is None or asset.native_currency == moeda:
            return c
        from apps.marketdata.fxservice import ptax_na_data_ou_anterior
        fx = ptax_na_data_ou_anterior(d)
        return c * fx[0] if fx else None


def _historico(hist):
    def f(d):
        ult = [h for h in hist if h[0] <= d]
        return (D(ult[-1][1]), D(ult[-1][2])) if ult else (Z, Z)
    return f


def _estados_ana():
    """Ativo -> função(data) -> (quantidade, valor aplicado)."""
    from apps.portfolio.models import Asset, Lot, PositionBalance, Sale
    out = {}
    ids = set(PositionBalance.objects.values_list('asset', flat=True)) | set(Lot.objects.filter(cancelado_em=None).values_list('asset', flat=True))
    for a in Asset.objects.filter(pk__in=ids):
        if ('ana', a.symbol) in POSICOES_BTG:
            out[a] = _historico(POSICOES_BTG[('ana', a.symbol)])
            continue
        bals = {}
        for b in PositionBalance.objects.filter(asset=a).order_by('reference_date', 'recorded_at'):
            bals[b.reference_date] = b  # o último registrado no dia vale
        lots = [l for l in Lot.objects.filter(asset=a, cancelado_em=None) if l.trade_date >= dt.date(2026, 10, 1)]
        sales = list(Sale.objects.filter(asset=a, account='ana'))

        def f(d, bals=bals, lots=lots, sales=sales):
            ref = max((r for r in bals if r <= d), default=None)
            if ref is None:
                return (sum((l.quantity for l in lots if l.trade_date <= d), Z),
                        sum((l.quantity * l.unit_price for l in lots if l.trade_date <= d), Z))
            b = bals[ref]
            cobertos = {x['id'] for x in b.covered_lots}
            q = b.quantity
            c = b.invested_cost if b.invested_cost is not None else b.quantity * b.average_price
            for l in lots:
                if ref < l.trade_date <= d and l.pk not in cobertos:
                    q, c = q + l.quantity, c + l.quantity * l.unit_price
            for s in sales:
                if ref < s.trade_date <= d:
                    q, c = q - s.quantity, c - s.allocated_cost
            return q, c
        out[a] = f
    return out


def _estados_bruno():
    from apps.managed_portfolios.models import Balance
    from apps.portfolio.models import Asset
    out = {}
    por_ativo = defaultdict(dict)
    for b in Balance.objects.filter(portfolio__slug='bruno').select_related('asset').order_by('reference_date', 'recorded_at'):
        por_ativo[b.asset][b.reference_date] = b
    for (conta, simbolo), hist in POSICOES_BTG.items():
        if conta == 'bruno':
            por_ativo.setdefault(Asset.objects.get(symbol=simbolo), {})
    for a, bals in por_ativo.items():
        if ('bruno', a.symbol) in POSICOES_BTG:
            out[a] = _historico(POSICOES_BTG[('bruno', a.symbol)])
            continue

        def f(d, bals=bals):
            ref = max((r for r in bals if r <= d), default=None)
            return (bals[ref].quantity, bals[ref].invested_cost) if ref else (Z, Z)
        out[a] = f
    return out


def _moeda(conta, asset):
    return 'BRL' if conta == 'bruno' else asset.native_currency


def serie(conta, moeda, fim, precos=None, estados=None):
    """Linhas diárias (pregões da moeda) de INICIO[conta] até fim, inclusive."""
    from apps.portfolio.models import Sale
    precos = precos or Precos(fim)
    estados = estados or (_estados_ana() if conta == 'ana' else _estados_bruno())
    ativos = [a for a in estados if _moeda(conta, a) == moeda]
    linhas, anterior = [], None
    for d in [p for p in precos.pregoes(moeda) if INICIO[conta] <= p <= fim]:
        valor = custo = fluxo = Z
        posicoes, faltam = 0, []
        for a in ativos:
            q, c = estados[a](d)
            qa, ca = estados[a](anterior) if anterior else (Z, Z)
            if q:
                posicoes += 1
                px = precos.preco(a, d, moeda)
                if px is None:
                    faltam.append(a.symbol)
                else:
                    valor += q * px
            custo += c
            if anterior is None or q == qa:
                continue
            if q > qa:
                fluxo += c - ca  # compra: entra o valor aplicado
            else:
                vendas = list(Sale.objects.filter(account=conta, asset=a, trade_date=d))
                if not vendas:
                    raise ValueError(f'{conta} {a.symbol} {d}: venda sem registro de preço; não reconstruo sem o valor recebido.')
                fluxo -= sum((s.quantity * s.unit_price - (s.fees or Z) for s in vendas), Z)
        linhas.append({'date': d, 'value': valor if not faltam else None, 'invested': custo,
                       'flow': fluxo if anterior is not None else None,
                       'income': PROVENTOS_BTG.get((conta, moeda, d), Z),
                       'positions': posicoes, 'quoted': posicoes - len(faltam), 'missing': faltam})
        anterior = d
    return linhas


def reconstruir(aplicar=False, agora=None):
    """Grava (ou só calcula, com aplicar=False) os dias anteriores ao primeiro registro do portal.

    Também preenche fluxo e proventos do primeiro registro do portal, que dependem do dia anterior.
    Nunca altera valor ou investido de um registro do portal. Retorna o resumo por carteira e moeda.
    """
    from django.db import transaction

    from .models import PatrimonioDiario
    agora = agora or timezone.now()
    resumo = {}
    alvos = [('ana', 'BRL'), ('ana', 'USD'), ('bruno', 'BRL')]
    primeiros = {}
    for conta, moeda in alvos:
        p = (PatrimonioDiario.objects.filter(account=conta, currency=moeda, source='portal').order_by('date').first())
        if p:
            primeiros[(conta, moeda)] = p
    if not primeiros:
        return resumo
    precos = Precos(max(p.date for p in primeiros.values()))
    estados = {'ana': _estados_ana(), 'bruno': _estados_bruno()}
    with transaction.atomic():
        for (conta, moeda), primeiro in primeiros.items():
            linhas = serie(conta, moeda, primeiro.date, precos, estados[conta])
            antes = [l for l in linhas if l['date'] < primeiro.date]
            ponte = next((l for l in linhas if l['date'] == primeiro.date), None)
            incompletos = [l['date'] for l in antes if l['value'] is None]
            resumo[(conta, moeda)] = {'linhas': linhas, 'primeiro': primeiro, 'incompletos': incompletos}
            if not aplicar or incompletos:
                continue
            PatrimonioDiario.objects.filter(account=conta, currency=moeda, source='btg').delete()
            PatrimonioDiario.objects.bulk_create([PatrimonioDiario(
                account=conta, currency=moeda, date=l['date'], value=l['value'], invested=l['invested'],
                gain=l['value'] - l['invested'], positions=l['positions'], quoted=l['quoted'], captured_at=agora,
                source='btg', flow=l['flow'], income=l['income']) for l in antes])
            if ponte and antes:
                PatrimonioDiario.objects.filter(pk=primeiro.pk).update(flow=ponte['flow'], income=ponte['income'])
    return resumo
