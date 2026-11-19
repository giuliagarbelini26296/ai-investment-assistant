"""Deterministic checks; screenshots never become live quote data."""
from decimal import Decimal as D
from .models import BrokerSnapshot

ZERO=D(0)
def review(account,positions):
    record=BrokerSnapshot.objects.filter(account=account).first()
    if not record:return None
    data=record.data
    rows=data['rows'];total=data['totals'];actual={p['symbol']:p for p in positions}
    detailed=data.get('coverage','positions')=='positions' and bool(rows)
    sums={key:sum((D(r[key]) for r in rows if r.get(key) is not None),ZERO) for key in ('invested','value','gain','daily_gain','dividends')}
    issues=[]
    for key in sums:
        if detailed and all(r.get(key) is not None for r in rows) and abs(sums[key]-D(total[key]))>D('.01'):issues.append('Soma das linhas diverge do total: '+key)
    if D(total['value'])-D(total['invested'])!=D(total['gain']):issues.append('Valor menos investido diverge do resultado informado.')
    comparisons=[]
    for r in rows:
        p=actual.get(r['symbol']);qty=p is not None and D(p['quantity'])==D(r['quantity']);cost=p is not None and abs(D(p['costBasis'])-D(r['invested']))<=D('.01')
        comparisons.append({'symbol':r['symbol'],'quantity_ok':qty,'cost_ok':cost,'portal_quantity':p['quantity'] if p else None,'portal_cost':p['costBasis'] if p else None,'status':'Conferido' if qty and cost else 'Revisar quantidade ou custo'})
        if D(r['quantity'])*D(r['price'])!=D(r['value']):issues.append('Quantidade × preço diverge: '+r['symbol'])
        if D(r['value'])-D(r['invested'])!=D(r['gain']):issues.append('Resultado diverge: '+r['symbol'])
    currency=data['currency'];included={r['symbol'] for r in rows}
    portal_cost=sum((D(p['costBasis']) for p in positions if p['currency']==currency and (not detailed or p['symbol'] in included)),ZERO)
    cost_difference=D(total['invested'])-portal_cost
    return {'detailed':detailed,'portal_cost':portal_cost,'cost_difference':cost_difference,'cost_ok':abs(cost_difference)<=D('.01'),'id':record.pk,'capturedAt':record.captured_at,'sourceKey':record.source_key,'data':data,
        'sums':sums,'issues':issues,'comparisons':comparisons,'matched':sum(c['quantity_ok'] and c['cost_ok'] for c in comparisons),'count':len(rows),
        'computed_return':D(total['gain'])/D(total['invested'])*100 if D(total['invested'])>0 else None,
        'computed_daily_return':D(total['daily_gain'])/(D(total['value'])-D(total['daily_gain']))*100 if D(total['value'])-D(total['daily_gain'])>0 else None}

def insights(positions):
    groups=[]
    for currency in ('BRL','USD'):
        rows=[p for p in positions if p['currency']==currency]
        if not rows:continue
        priced=[p for p in rows if p['value'] is not None and D(p['value'])>=0]
        value=sum((D(p['value']) for p in priced),ZERO)
        complete=len(priced)==len(rows) and value>0
        ordered=sorted(priced,key=lambda p:D(p['value']),reverse=True)
        groups.append({'currency':currency,'quoted':len(priced),'count':len(rows),'value':value if priced else None,
            'top_symbol':ordered[0]['symbol'] if ordered else None,
            'top_weight':D(ordered[0]['value'])/value*100 if complete else None,
            'top_three_weight':sum((D(p['value']) for p in ordered[:3]),ZERO)/value*100 if complete else None,
            'impact_one_percent':value/D(100) if complete else None,
            'rows':[{'symbol':p['symbol'],'weight':D(p['value'])/value*100 if complete and p['value'] is not None else None,
                'cost_price':D(p['costBasis'])/D(p['quantity']) if D(p['quantity'])>0 else None,
                'recovery_percent':max(ZERO,(D(p['costBasis'])/D(p['value'])-1)*100) if p['value'] is not None and D(p['value'])>0 else None,
                'impact_one_percent':D(p['value'])/100 if p['value'] is not None else None} for p in rows]})
    return groups
