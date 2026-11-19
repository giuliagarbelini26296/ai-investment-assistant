from decimal import Decimal as D
from types import SimpleNamespace

from apps.marketdata import freshness
from apps.portfolio.models import Asset
from apps.react_portal.api import native_position, SORT_FIELDS
from apps.valuation import core
from .models import CurrencyQuote, Portfolio
from .services import position

ACCOUNTS = [{'slug':'ana', 'name':'Ana'}, {'slug':'bruno', 'name':'Bruno'}]


def managed_positions(account, query, now):
    portfolio = Portfolio.objects.get(slug=account, legacy=False)
    ids = set(portfolio.balances.values_list('asset_id', flat=True)) | set(portfolio.purchases.values_list('asset_id', flat=True))
    rows = []
    for asset in Asset.objects.filter(pk__in=ids).order_by('symbol'):
        pos, currency, balance = position(portfolio, asset)
        if pos is None:
            continue
        if currency != asset.native_currency:
            quote = CurrencyQuote.objects.filter(asset=asset, currency=currency).first()
            actual = freshness.atualidade(asset, now, quote) if quote else freshness.Atualidade('sem_cotacao', 'Aguardando cotação direta em '+currency, None, False)
        else:
            actual = freshness.atualidade(asset, now)
        quote = actual.cotacao
        # Never value a BRL position using a USD/USDT quote or a BDR as its US underlying.
        if quote and quote.currency != currency:
            actual = freshness.Atualidade('sem_cotacao', 'Sem cotação confirmada na moeda da posição.', None, False)
            quote = None
        val = core.avaliar(pos, quote.price, quote.previous_close) if quote else None
        if val and not actual.permite_alertas:
            import dataclasses
            val = dataclasses.replace(val, variacao_dia_pct=None)
        line = SimpleNamespace(asset=asset, posicao=pos, avaliacao=val,
            atualidade=actual, motivo_sem_preco=actual.descricao)
        row = native_position(line, include_history=query.get('history','1')!='0' and currency==asset.native_currency)
        row.update(currency=currency, broker='Conta '+portfolio.name, brokerCode=portfolio.slug.upper(),
            referenceDate=balance.reference_date if balance else None, assetClass=asset.asset_class,
            positionKind='Cripto' if asset.market=='CRIPTO' else 'BDR' if asset.symbol.endswith('34') and asset.market=='B3' else 'Ação')
        if asset.market=='CRIPTO':
            row['dayBasis']='rolling_24h' if row['dayGain'] is not None else None
            row['quoteState'] += ' · par direto BTC/BRL; variação móvel de 24 h, referência de mercado'
            row['historyUnavailable']='Histórico em BRL ainda não registrado.'
        rows.append(row)
    broker, result = query.get('broker','all'), query.get('result','all')
    sort, direction = query.get('sort','country'), query.get('direction','asc')
    search = query.get('search','').strip()
    if broker not in ('all', portfolio.slug.upper()) or result not in ('all','gain','loss'):
        raise ValueError('Filtro de corretora ou resultado inválido para esta carteira.')
    if sort not in SORT_FIELDS or direction not in ('asc','desc') or len(search)>120:
        raise ValueError('Ordenação ou busca inválida.')
    rows=[r for r in rows if search.casefold() in f"{r['symbol']} {r['name']} {r['broker']}".casefold()
          and (result=='all' or r['totalGain'] is not None and (r['totalGain']>0 if result=='gain' else r['totalGain']<0))]
    available=[r for r in rows if r[sort] is not None]
    available.sort(key=lambda r:(r[sort],r['symbol']), reverse=direction=='desc')
    return available+[r for r in rows if r[sort] is None]


def totals(rows):
    result=[]
    for currency in sorted({r['currency'] for r in rows}):
        subset=[r for r in rows if r['currency']==currency]
        quoted=[r for r in subset if r['value'] is not None]
        daily=[r for r in subset if r['dayGain'] is not None]
        invested=sum((r['costBasis'] for r in subset),D(0))
        cost=sum((r['costBasis'] for r in quoted),D(0))
        value=sum((r['value'] for r in quoted),D(0))
        gain=value-cost
        base=sum((r['dayBase'] for r in daily),D(0))
        day=sum((r['dayGain'] for r in daily),D(0))
        result.append({'currency':currency,'count':len(subset),'quoted':len(quoted),'dayCount':len(daily),
            'invested':invested,'assessedCost':cost,'value':value if quoted else None,
            'gain':gain if quoted else None,'returnPct':gain/cost*100 if cost else None,
            'day':day if daily else None,'dayBase':base if daily else None,'dayPct':day/base*100 if base else None,
            'unquotedSymbols':[r['symbol'] for r in subset if r['value'] is None]})
    return result
