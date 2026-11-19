"""Decimal calculations and evidence-based planning; no broker orders or purchase writes."""
import datetime as dt
from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.marketdata.history import fonte_principal
from apps.marketdata.models import DailyBar
from apps.marketdata.fxservice import cambio_atual
from apps.mercado.models import EarningsEvent
from apps.portfolio import services as portfolio
from apps.portfolio.models import Lot
from apps.valuation.core import Lote, adicionar_compra
from .models import BenchmarkBar, CalendarReference, CashEntry

ZERO, HUNDRED = Decimal(0), Decimal(100)


def sector_name(asset):
    value = asset.sector.strip()
    return 'Bancos e serviços financeiros' if any(s in value.casefold() for s in ('banc', 'financ')) else value or 'Setor não informado'


def quality_for(line, now=None):
    now = now or timezone.now()
    asset = line.asset
    q = line.atualidade.cotacao if line.atualidade else None
    source = fonte_principal(asset)
    bars = list(DailyBar.objects.filter(asset=asset, source=source, provisional=False)
                .order_by('-session_date')[:65]) if source else []
    warnings, blocks = [], []
    if not q or not line.avaliacao:
        blocks.append('Sem cotação válida para avaliar a posição.')
    else:
        if q.collected_at > now + dt.timedelta(minutes=5) or (q.market_time and q.market_time > now + dt.timedelta(minutes=5)):
            blocks.append('Horário da cotação está no futuro; conferir fonte e relógio.')
        if q.previous_close is None or q.previous_close <= ZERO:
            blocks.append('Fechamento anterior ausente: o resultado do dia não está confirmado.')
        if line.atualidade.estado not in ('atual', 'spot'):
            warnings.append(line.atualidade.descricao)
        if not q.market_time:
            warnings.append('Fonte não informa a hora da última negociação; coleta não é hora de negócio.')
        session = timezone.localtime(q.market_time or q.collected_at).date()
        previous = next((b for b in bars if b.session_date < session), None)
        if previous and q.previous_close and abs(q.previous_close / previous.close - 1) > Decimal('.01'):
            warnings.append('Fechamento anterior difere mais de 1% do histórico: pode haver atualização pendente ou evento societário. Não substituímos os dados.')
        if q.previous_close and q.price == q.previous_close:
            warnings.append('Último preço igual ao fechamento anterior: 0% é coerente com o dado, mas não confirma negociação ao vivo.')
    jumps = []
    for newer, older in zip(bars, bars[1:]):
        change = (newer.close / older.close - 1) * HUNDRED
        if abs(change) >= 20:
            jumps.append({'date': newer.session_date, 'percent': change})
    if jumps:
        warnings.append('Salto diário de pelo menos 20% no histórico. Pode ser movimento real, ajuste societário ou dado inconsistente; indicadores precisam de revisão.')
    if not bars:
        warnings.append('Sem histórico diário disponível para conferir a série.')
    return {'symbol': asset.symbol, 'asset_id': asset.pk, 'currency': asset.native_currency,
        'source': q.source if q else '', 'market_time': q.market_time if q else None,
        'collected_at': q.collected_at if q else None,
        'price': q.price if q else None, 'previous_close': q.previous_close if q else None,
        'warnings': warnings, 'blocks': blocks, 'jumps': jumps,
        'state': 'Revisar' if blocks or jumps else 'Com ressalvas' if warnings else 'Conferido'}


def concentrations(lines, preference):
    groups = []
    for currency in sorted({l.asset.native_currency for l in lines}):
        selected = [l for l in lines if l.asset.native_currency == currency and l.posicao]
        complete = all(l.avaliacao for l in selected)
        values = [(l, l.avaliacao.valor_atual if complete else l.posicao.investido) for l in selected]
        total = sum((v for _, v in values), ZERO)
        sectors = defaultdict(lambda: ZERO)
        assets = []
        for line, value in sorted(values, key=lambda item: item[1], reverse=True):
            weight = value / total * HUNDRED if total else ZERO
            sectors[sector_name(line.asset)] += value
            assets.append({'symbol': line.asset.symbol, 'value': value, 'percent': weight,
                'over': weight > preference.asset_limit})
        groups.append({'currency': currency, 'basis': 'valor de mercado' if complete else 'custo registrado de todas as posições (cotações incompletas)',
            'assets': assets, 'sectors': [{'name': k, 'percent': v / total * HUNDRED if total else ZERO,
                'over': v / total * HUNDRED > preference.sector_limit if total and k != 'Setor não informado' else False} for k, v in sorted(sectors.items(), key=lambda x: -x[1])],
            'total': total})
    fx = cambio_atual()
    countries = defaultdict(lambda: ZERO)
    comparable = all(l.avaliacao and l.asset.native_currency in ('BRL', 'USD') for l in lines)
    has_usd = any(l.asset.native_currency == 'USD' for l in lines)
    if comparable and (not has_usd or (fx and not fx.desatualizado)):
        for line in lines:
            country = 'Brasil' if line.asset.market == 'B3' else 'Estados Unidos' if line.asset.market in ('NYSE', 'NASDAQ') else 'Outros mercados'
            countries[country] += line.avaliacao.valor_atual * (fx.valor if line.asset.native_currency == 'USD' else 1)
    total = sum(countries.values(), ZERO)
    country_rows = [{'name': k, 'percent': v / total * HUNDRED, 'over': v / total * HUNDRED > preference.country_limit}
        for k, v in sorted(countries.items(), key=lambda x: -x[1])] if total else []
    return {'groups': groups, 'countries': country_rows, 'fx': fx,
        'country_note': 'Pesos globais convertidos para BRL apenas para comparar exposição; não é rentabilidade cambial.' if country_rows else 'Comparação por país indisponível: falta cotação ou câmbio recente. Reais e dólares não foram somados.'}


def agenda(lines, today=None):
    today = today or timezone.localdate()
    ids = [l.asset.pk for l in lines]
    end = today + dt.timedelta(days=7)
    rows, verified = [], set()
    for event in CalendarReference.objects.filter(asset_id__in=ids, date__range=(today, end)).select_related('asset'):
        verified.add((event.asset_id, event.date))
        rows.append({'symbol': event.asset.symbol, 'date': event.date, 'title': event.title,
            'timing': event.timing, 'source': event.source_url, 'confirmed': True, 'checked': event.verified_at})
    seen = set(verified)
    for event in EarningsEvent.objects.filter(asset_id__in=ids, date__range=(today, end)).select_related('asset').order_by('date', '-collected_at'):
        # A published company calendar takes precedence for the same asset in the seven-day window.
        if any(asset_id == event.asset_id for asset_id, _ in verified) or (event.asset_id, event.date) in seen:
            continue
        seen.add((event.asset_id, event.date))
        rows.append({'symbol': event.asset.symbol, 'date': event.date, 'title': 'Resultado trimestral · data do provedor',
            'timing': event.horario_rotulo, 'source': '', 'provider': event.source, 'confirmed': False, 'checked': event.collected_at})
    return sorted(rows, key=lambda e: (e['date'], e['symbol']))


@transaction.atomic
def register_cash(form, user):
    data = {key: form.cleaned_data[key] for key in ('asset', 'currency', 'kind', 'date', 'amount', 'reference')}
    entry, created = CashEntry.objects.get_or_create(id=form.cleaned_data['submission_key'], defaults={**data, 'user': user})
    if not created and (entry.user_id != user.pk or entry.cancelled_at or any(getattr(entry, k) != v for k, v in data.items())):
        raise ValueError('Esta confirmação já foi usada em outro lançamento. Atualize a página e confira o extrato.')
    return entry, created


def economic_results(lines):
    currencies = sorted({l.asset.native_currency for l in lines} | set(CashEntry.objects.filter(cancelled_at=None).values_list('currency', flat=True)))
    out = []
    for currency in currencies:
        selected = [l for l in lines if l.asset.native_currency == currency]
        entries = CashEntry.objects.filter(currency=currency, cancelled_at=None)
        sums = {kind: sum((e.amount for e in entries if e.kind == kind), ZERO) for kind, _ in CashEntry.Kind.choices}
        # Fees already in purchase records are shown once; the cash form warns against entering them twice.
        lot_fees = sum((lot.costs or ZERO for lot in Lot.objects.filter(cancelado_em=None, asset__native_currency=currency)), ZERO)
        unrealized = sum((l.avaliacao.resultado for l in selected if l.avaliacao), ZERO) if selected and all(l.avaliacao for l in selected) else None
        income = sums['dividend'] + sums['jcp']
        realized = sums['profit'] - sums['loss']
        expenses = sums['fee'] + sums['tax'] + lot_fees
        out.append({'currency': currency, 'unrealized': unrealized, 'income': income, 'realized': realized,
            'expenses': expenses, 'lot_fees': lot_fees, 'recorded_count': entries.count(),
            'total': unrealized + income + realized - expenses if unrealized is not None else None,
            'first_entry': entries.order_by('date').values_list('date', flat=True).first()})
    return out


def simulate(form, lines, preference):
    data = form.cleaned_data
    asset = data['asset']
    before = portfolio.posicao_do_ativo(asset)
    after = adicionar_compra(before, Lote(data['quantity'], data['price']))
    group = [l for l in lines if l.asset.native_currency == asset.native_currency and l.posicao]
    base_cost = sum((l.posicao.investido for l in group), ZERO)
    investment = data['quantity'] * data['price']
    total_after = base_cost + investment
    sector_after = sum((l.posicao.investido for l in group if sector_name(l.asset) == sector_name(asset)), ZERO) + investment
    return {'symbol': asset.symbol, 'currency': asset.native_currency, 'before': before, 'after': after,
        'investment': investment, 'company_percent': after.investido / total_after * HUNDRED,
        'sector_percent': sector_after / total_after * HUNDRED if asset.sector.strip() else None,
        'over': after.investido / total_after * HUNDRED > preference.asset_limit,
        'sector_over': bool(asset.sector.strip()) and sector_after / total_after * HUNDRED > preference.sector_limit,
        'sector': sector_name(asset), 'asset_id': asset.pk}


def watchlist(lines, quality, events, preference, concentration):
    """In-portal explanations only: no messages to external notification channels."""
    notes = []
    for group in concentration['groups']:
        for asset in group['assets']:
            if asset['over']:
                notes.append({'level': 'Atenção', 'symbol': asset['symbol'], 'title': 'Concentração acima do seu limite',
                    'evidence': f"{asset['percent']:.2f}% em {group['currency']}, pelo {group['basis']}. Limite: {preference.asset_limit}%.",
                    'risk': 'Uma oscilação desta empresa terá maior efeito nessa parte da carteira.',
                    'next': 'Confira o efeito de uma nova compra no simulador antes de decidir.'})
    for q in quality:
        if q['blocks'] or q['jumps']:
            notes.append({'level': 'Revisar dados', 'symbol': q['symbol'], 'title': 'Sinal limitado pela qualidade dos dados',
                'evidence': '; '.join(q['blocks'] + q['warnings']), 'risk': 'Preços incompletos ou saltos na série podem distorcer indicadores.',
                'next': 'Confira o histórico e os eventos societários na corretora. Não há ajuste automático de quantidade.'})
    for event in events:
        notes.append({'level': 'Agenda', 'symbol': event['symbol'], 'title': event['title'],
            'evidence': f"{event['date']:%d/%m/%Y} · {event['timing'] or 'horário não informado'} · " + ('calendário publicado' if event['confirmed'] else 'data de provedor, sujeita a confirmação'),
            'risk': 'Resultados podem provocar alta ou queda brusca, inclusive na abertura seguinte.',
            'next': 'Compare resultados e perspectivas divulgados com as expectativas; a data não é um sinal de compra.',
            'source': event.get('source')})
    return notes


def interval_return(start_value, end_value, flows, income, days):
    """Modified Dietz: explicit end-of-day flow convention, never an exact intraday return."""
    total_flows = sum((amount for _, amount in flows), ZERO)
    denominator = start_value + sum((Decimal(days - offset) / Decimal(days) * amount for offset, amount in flows), ZERO)
    return (end_value + income - start_value - total_flows) / denominator if denominator > 0 else None


def performance(lines, currency, start, end):
    selected = [l for l in lines if l.asset.native_currency == currency and l.posicao]
    benchmark = 'BOVA11' if currency == 'BRL' else 'SPY'
    result = {'currency': currency, 'benchmark': benchmark, 'points': [], 'warnings': [], 'start': None, 'end': None}
    if not selected:
        result['warnings'].append('Sem posições nesta moeda.'); return result
    series, quantities, lots = {}, {}, {}
    earliest = start
    for line in selected:
        a = line.asset
        balance = a.position_balance
        effective = portfolio.lotes_apos_saldo(a, list(a.lots.all()))
        # A latest reconciled balance is the first date with known holdings. Never backfill acquisitions.
        if balance:
            earliest = max(earliest, balance.reference_date)
        elif effective:
            earliest = max(earliest, min(l.trade_date for l in effective))
        if any(l.revision > 1 or l.cancelado_em for l in a.lots.all() if not balance or l.pk not in balance.covered_lot_ids):
            result['warnings'].append(f'{a.symbol}: há revisão/cancelamento de compras após o saldo; comparação exige conciliação.'); return result
        if balance and any(l.trade_date < balance.reference_date for l in effective):
            result['warnings'].append(f'{a.symbol}: compra anterior ao saldo ainda não conciliada.'); return result
        source = fonte_principal(a)
        series[a.pk] = {b.session_date: b.close for b in DailyBar.objects.filter(asset=a, source=source,
            provisional=False, session_date__range=(start, end))}
        quantities[a.pk] = balance.quantity if balance else ZERO
        lots[a.pk] = effective
    refs = {b.date: b.close for b in BenchmarkBar.objects.filter(symbol=benchmark, date__range=(earliest, end))}
    common = set(refs)
    for prices in series.values():
        common &= set(prices)
    dates = sorted(d for d in common if d >= earliest)
    if len(dates) < 2:
        result['warnings'].append('São necessários dois fechamentos comuns de todas as posições e da referência, após o último saldo conciliado. Nenhum período anterior foi inventado.'); return result
    def qty(asset_id, day):
        return quantities[asset_id] + sum((l.quantity for l in lots[asset_id] if l.trade_date <= day), ZERO)
    def value(day):
        return sum((qty(aid, day) * prices[day] for aid, prices in series.items()), ZERO)
    entries = list(CashEntry.objects.filter(currency=currency, cancelled_at=None, date__range=(dates[0], dates[-1])))
    # Realized P/L imports aren't cash flows on this open-positions-only performance series.
    if any(e.kind in ('profit', 'loss') for e in entries):
        result['warnings'].append('Há resultado realizado informado; faltam vendas/quantidades e fluxos completos. A comparação da carteira não pode ser calculada com segurança.'); return result
    level = Decimal(1)
    points = [{'date': dates[0], 'portfolio': ZERO, 'benchmark': ZERO}]
    for left, right in zip(dates, dates[1:]):
        # Large jumps can be corporate actions on an unadjusted source. Withhold rather than guess.
        if any(abs(prices[right] / prices[left] - 1) >= Decimal('.20') for prices in series.values()):
            result['warnings'].append('Salto de pelo menos 20% na série do período: conferir eventos societários antes de comparar.'); return result
        if abs(refs[right] / refs[left] - 1) >= Decimal('.20'):
            result['warnings'].append('Salto na referência: conferir ajustes antes de comparar.'); return result
        days = (right - left).days
        flows = [((l.trade_date - left).days, l.quantity * l.unit_price)
            for asset_lots in lots.values() for l in asset_lots if left < l.trade_date <= right]
        income = -sum((e.amount for e in entries if e.kind in ('fee','tax') and left < e.date <= right), ZERO)
        income -= sum((l.costs or ZERO for asset_lots in lots.values() for l in asset_lots if left < l.trade_date <= right), ZERO)
        ret = interval_return(value(left), value(right), flows, income, days)
        if ret is None:
            result['warnings'].append('Base insuficiente para neutralizar os aportes.'); return result
        level *= 1 + ret
        points.append({'date': right, 'portfolio': (level - 1) * HUNDRED,
            'benchmark': (refs[right] / refs[dates[0]] - 1) * HUNDRED})
    result.update(points=points, start=dates[0], end=dates[-1], portfolio=points[-1]['portfolio'], reference=points[-1]['benchmark'])
    result['difference'] = result['portfolio'] - result['reference']
    result['warnings'].append('Modified Dietz encadeado: compras neutralizadas pelo custo executado e ponderadas por dias; não conhece o horário intradiário. Comparação de preços, sem dividendos/JCP de ambos os lados. Despesas entram somente quando registradas. Referências são ETFs, com custos próprios; resultado completo fica no painel de lançamentos.')
    return result
