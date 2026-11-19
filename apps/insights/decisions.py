"""Explainable scenarios. No prediction probabilities, brokerage orders or ledger writes."""
import datetime as dt
from decimal import Decimal as D, ROUND_FLOOR, ROUND_CEILING
from django.utils import timezone
from apps.marketdata import freshness
from apps.marketdata.history import fonte_principal
from apps.marketdata.models import DailyBar
from apps.managed_portfolios.models import CurrencyQuote, CurrencyBar
from apps.mercado.indicadores import rsi, volatilidade_anual
from apps.mercado.models import Fundamentos, PriceTarget, AnalystSnapshot
from apps.portfolio.models import Asset
from apps.react_portal.api import portfolio_payload
from .models import ValueReference

FEATURED=('AMZN','GOOGL','SANB11','AVGO','SOL','BTC')
ZERO=D(0)
HUNDRED=D(100)
VALID_QUOTES={'atual','atrasado','nao_declarado','mercado_fechado','spot'}


def rounded_quantity(value,market,up=False):
    step=D(1) if market=='B3' else D('.00000001') if market=='CRIPTO' else D('.00001')
    return max(ZERO,(value/step).to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR)*step)


def decision_row(asset,held,preference,reference,now):
    currency='BRL' if asset.market in ('B3','CRIPTO') else 'USD'
    warnings=[];blocks=[];reasons=[]
    if asset.market=='CRIPTO':
        q=CurrencyQuote.objects.filter(asset=asset,currency='BRL').first()
        actual=freshness.atualidade(asset,now,q) if q else None
        bars=list(CurrencyBar.objects.filter(asset=asset,currency='BRL',date__lt=timezone.localdate(now))
            .order_by('-date')[:100])
        series=[(b.date,b.close) for b in reversed(bars)]
    else:
        actual=freshness.atualidade(asset,now);q=actual.cotacao
        source=fonte_principal(asset)
        bars=list(DailyBar.objects.filter(asset=asset,source=source,provisional=False,
            session_date__lt=timezone.localdate(now)).order_by('-session_date')[:100]) if source else []
        series=[(b.session_date,b.close) for b in reversed(bars)]
    price=q.price if q and q.currency==currency and q.price>0 else None
    if not price:
        blocks.append('Sem cotação na moeda desta análise.')
    elif actual.estado not in VALID_QUOTES:
        blocks.append('Cotação vencida ou sessão não confirmada. Aguarde atualização da fonte.')
    if q and (q.collected_at>now+dt.timedelta(minutes=5) or q.market_time and q.market_time>now+dt.timedelta(minutes=5)):
        blocks.append('Horário da cotação no futuro; confira fonte e relógio.')
    if q and q.source.startswith('demo'):
        blocks.append('Cotação de demonstração: cenário real indisponível.')
    if actual and actual.estado in ('atrasado','nao_declarado','mercado_fechado'):
        warnings.append(actual.descricao)
    if not asset.sector and asset.market!='CRIPTO':
        warnings.append('Setor não informado: a exposição conjunta a empresas semelhantes exige conferência.')
    if len(series)<50:
        blocks.append('São necessárias 50 sessões completas na mesma moeda e fonte para a leitura técnica.')
    if series and (timezone.localdate(now)-series[-1][0]).days>(3 if asset.market=='CRIPTO' else 7):
        blocks.append('Histórico sem fechamentos recentes.')
    jumps=[date for (date,close),(_,previous) in zip(series[1:],series[:-1]) if previous>0 and abs(close/previous-1)>=D('.20')]
    if jumps:
        warnings.append('O histórico contém salto diário de pelo menos 20%; conferir ajustes societários e dados.')
        if asset.market!='CRIPTO':
            blocks.append('Série exige revisão antes de gerar um sinal de entrada.')
    closes=[close for _,close in series]
    sma20=sum(closes[-20:],ZERO)/20 if len(closes)>=20 else None
    sma50=sum(closes[-50:],ZERO)/50 if len(closes)>=50 else None
    indicator_rsi=rsi([float(v) for v in closes]) if len(closes)>=15 else None
    vol=volatilidade_anual([float(v) for v in closes],20,365 if asset.market=='CRIPTO' else 252)
    technical_limit=sma20*(1+preference.entry_tolerance/HUNDRED) if sma20 else None
    trend='Sem histórico suficiente'
    if price and sma20 and sma50:
        trend='Favorável' if sma20>=sma50 and price>=sma50 else 'Desfavorável' if sma20<sma50 and price<sma50 else 'Mista'
    fund=Fundamentos.objects.filter(asset=asset,collected_at__lte=now).order_by('-collected_at').first() if asset.market!='CRIPTO' else None
    fund_fresh=bool(fund and now-fund.collected_at<=dt.timedelta(days=45))
    if asset.market!='CRIPTO' and not fund_fresh:
        warnings.append('Fundamentos recentes indisponíveis; não é possível concluir que a empresa está barata.')
    fundamental_risk=bool(fund_fresh and (fund.profit_margin is not None and fund.profit_margin<0 or fund.roe is not None and fund.roe<0))
    if fundamental_risk:
        warnings.append('Margem líquida ou ROE negativo nos dados disponíveis: revisar a tese de negócio.')
    reference_price=reference.price if reference and reference.currency==currency else None
    reference_source=reference.source if reference_price else ''
    reference_date=reference.reference_date if reference_price else None
    reference_kind='Premissa informada por você' if reference_price else ''
    if reference_price and reference.reference_date>timezone.localdate(now):
        blocks.append('Referência de valor com data futura.')
        reference_price=None
    if reference_price and (timezone.localdate(now)-reference.reference_date).days>180:
        warnings.append('Sua referência tem mais de 180 dias; revise a tese antes de usar.')
        reference_price=None
    # Targets are projections in the US stock's own currency, never converted to a BDR or crypto.
    if reference_price is None and asset.market in ('NYSE','NASDAQ'):
        target=PriceTarget.objects.filter(asset=asset,mean__gt=0,collected_at__range=(now-dt.timedelta(days=30),now)).order_by('-collected_at').first()
        analysts=AnalystSnapshot.objects.filter(asset=asset,period__range=(timezone.localdate(now)-dt.timedelta(days=90),timezone.localdate(now)),collected_at__lte=now).order_by('-period').first()
        if target and analysts and analysts.total>=5 and (not target.reference_date or dt.timedelta(0)<=timezone.localdate(now)-target.reference_date<=dt.timedelta(days=90)):
            reference_price=target.mean;reference_source=target.source
            reference_date=target.reference_date or timezone.localdate(target.collected_at)
            reference_kind='Preço-alvo de analistas · projeção, não valor garantido'
    value_limit=reference_price*(1-preference.safety_margin/HUNDRED) if reference_price else None
    discount=(reference_price-price)/reference_price*HUNDRED if reference_price and price else None
    signal='Definir horizonte';code='setup';eligible=False
    entry_limit=value_limit if preference.horizon=='longo' else technical_limit
    if blocks:
        signal='Dados insuficientes';code='data';reasons.extend(blocks)
    elif not preference.profile or not preference.horizon:
        reasons.append('Escolha o horizonte e complete os parâmetros do cenário.')
    elif preference.horizon=='longo':
        if not reference_price:
            signal='Estudar valor e tese';code='study'
            reasons.append('Sem referência de valor verificável. Preço baixo ou queda isolada não provam oportunidade.')
        elif fundamental_risk:
            signal='Reavaliar fundamentos';code='study'
            reasons.append('Os dados de rentabilidade exigem revisão antes de aumentar a posição.')
        elif price<=value_limit:
            signal='Compra condicional';code='candidate';eligible=True
            reasons.append('Preço dentro do teto da sua referência e margem. A conclusão depende da premissa usada.')
        else:
            signal='Aguardar preço';code='wait'
            reasons.append('Preço acima do teto calculado pela referência e margem de segurança escolhidas.')
    else:
        if trend=='Favorável' and indicator_rsi is not None and 35<=indicator_rsi<=65 and price<=technical_limit and not fundamental_risk:
            signal='Entrada técnica em estudo';code='candidate';eligible=True
            reasons.append('Média de 20 sessões acima da de 50, preço acima da média de 50, RSI entre 35 e 65 e preço dentro do teto técnico.')
        else:
            signal='Aguardar sinal';code='wait'
            reasons.append('Nem todos os critérios técnicos de entrada estão atendidos. Isso não é uma previsão de queda.')
    quantity=held['quantity'] if held else ZERO
    held_value=held['value'] if held else ZERO
    return {'asset':asset,'symbol':asset.symbol,'currency':currency,'kind':'Cripto' if asset.market=='CRIPTO' else 'BDR' if asset.market=='B3' and asset.symbol.endswith('34') else 'Ação',
        'price':price,'source':q.source if q else '', 'quote_time':q.market_time if q else None,'collected_at':q.collected_at if q else None,
        'quote_status':actual.estado if actual else 'sem_cotacao','history_end':series[-1][0] if series else None,'history_count':len(series),
        'sma20':sma20,'sma50':sma50,'rsi':indicator_rsi,'volatility':vol,'trend':trend,
        'signal':signal,'code':code,'eligible':eligible,'blocks':blocks,'warnings':warnings,'reasons':reasons,
        'reference_price':reference_price,'reference_source':reference_source,'reference_date':reference_date,'reference_kind':reference_kind,
        'entry_limit':entry_limit,'discount':discount,'fund':fund if fund_fresh else None,
        'held_quantity':quantity,'held_value':held_value,'held_cost':held['costBasis'] if held else ZERO,
        'weight':None,'sale_quantity':None,'plan_quantity':None,'plan_cost':None,'plan_risk':None,
        'featured':asset.symbol in FEATURED,'plan_reason':'Informe orçamento e horizonte para calcular a quantidade.'}


def allocate(rows,holdings,preference):
    summaries=[]
    for currency in sorted({r['currency'] for r in rows}):
        positions=[p for p in holdings if p['currency']==currency]
        complete=all(p['value'] is not None and p['quoteStatus'] in VALID_QUOTES for p in positions)
        value=sum((p['value'] or ZERO for p in positions),ZERO)
        budget=preference.budget_brl if currency=='BRL' else preference.budget_usd
        base=value+(budget or ZERO)
        remaining=budget or ZERO
        risk_remaining=base*preference.plan_risk/HUNDRED
        selected=[r for r in rows if r['currency']==currency]
        for row in selected:
            if complete and value and row['held_value'] is not None:
                row['weight']=row['held_value']/value*HUNDRED
                if preference.profile and preference.horizon and row['held_quantity']>0 and row['held_value']>base*preference.asset_limit/HUNDRED and row['price'] and row['quote_status'] in VALID_QUOTES:
                    excess=row['held_value']-base*preference.asset_limit/HUNDRED
                    row['sale_quantity']=min(row['held_quantity'],rounded_quantity(excess/row['price'],row['asset'].market,up=True))
                    row['signal']='Rever concentração';row['code']='concentration';row['eligible']=False
                    row['reasons'].insert(0,'Posição acima do limite do cenário. A quantidade de redução simula rebalanceamento, não uma ordem de venda.')
            if not complete:
                row['eligible']=False;row['plan_reason']='Avaliação incompleta das posições nesta moeda; dimensionamento bloqueado.'
        candidates=sorted(selected,key=lambda r:(not r['eligible'],-(r['discount'] or ZERO),r['symbol']))
        for row in candidates:
            if not preference.profile or not preference.horizon or budget is None:
                continue
            row['plan_quantity']=ZERO;row['plan_cost']=ZERO;row['plan_risk']=ZERO
            if not complete:
                continue
            if not row['eligible']:
                row['plan_reason']='Critérios de entrada não atendidos; sem alocação neste cenário.';continue
            price=row['price']
            room=max(ZERO,base*preference.asset_limit/HUNDRED-(row['held_value'] or ZERO))
            unit_loss=price*preference.stop_distance/HUNDRED
            max_q=min(remaining/price,room/price,risk_remaining/unit_loss)
            q=rounded_quantity(max_q,row['asset'].market)
            row.update(plan_quantity=q,plan_cost=q*price,plan_risk=q*unit_loss)
            remaining-=row['plan_cost'];risk_remaining-=row['plan_risk']
            row['plan_reason']='Dentro do orçamento, limite por ativo e perda simulada do plano.' if q>0 else 'Saldo ou limites do cenário não comportam a quantidade mínima simulada.'
        spent=sum((r['plan_cost'] or ZERO for r in selected),ZERO)
        risk=sum((r['plan_risk'] or ZERO for r in selected),ZERO)
        assert spent<=(budget or ZERO) and risk<=base*preference.plan_risk/HUNDRED
        summaries.append({'currency':currency,'budget':budget,'portfolio_value':value if complete else None,
            'planned_cost':spent,'remaining':remaining if budget is not None else None,'scenario_loss':risk,
            'complete':complete,'capital':base,'risk_limit':base*preference.plan_risk/HUNDRED})
    return summaries


def evaluate(user,account,preference,query=None,now=None):
    now=now or timezone.now();query=query or {}
    payload=portfolio_payload({'account':account,'history':'0'},user.get_username(),now)
    holdings=payload['positions'];held={p['id']:p for p in holdings}
    references={r.asset_id:r for r in ValueReference.objects.filter(user=user,account=account)}
    assets=list(Asset.objects.filter(is_active=True).exclude(market='CAMBIO').order_by('symbol'))
    rows=[decision_row(asset,held.get(asset.pk),preference,references.get(asset.pk),now) for asset in assets]
    # Scope is selected before the plan: the same budget is never reused across rows.
    scope=query.get('scope','focus');market=query.get('market','all');search=query.get('search','').strip()[:100]
    if scope not in ('focus','held','new','all') or market not in ('all','BRL','USD'):
        raise ValueError('Filtro inválido.')
    rows=[r for r in rows if (scope=='all' or scope=='held' and r['held_quantity']>0 or scope=='new' and r['held_quantity']==0 or scope=='focus' and (r['featured'] or r['held_quantity']>0))
        and (market=='all' or r['currency']==market) and (not search or search.casefold() in (r['symbol']+' '+r['asset'].name).casefold())]
    if query.get('_symbols'):
        rows=[r for r in rows if r['symbol'] in query['_symbols']]
    plans=allocate(rows,holdings,preference)
    rank={'candidate':0,'concentration':1,'wait':2,'study':3,'setup':4,'data':5}
    rows.sort(key=lambda r:(rank[r['code']],not r['featured'],r['symbol']))
    return {'rows':rows,'plans':plans,'as_of':now,'account':account,'owner':'Ana' if account=='ana' else 'Bruno',
        'profile':preference,'scope':scope,'market':market,'search':search,'featured_symbols':FEATURED,
        'missing_budget':preference.budget_brl is None and preference.budget_usd is None,
        'missing_horizon':not preference.horizon,'has_candidate':any(r['eligible'] for r in rows),
        'counts':{key:sum(r['code']==key for r in rows) for key in rank}}
