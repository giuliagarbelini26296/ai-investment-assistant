"""A private tracking workbook, independent of brokerage trades and CashEntry."""
import datetime as dt
import json
import uuid
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.http import JsonResponse, HttpResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from apps.portfolio.models import Asset
from apps.insights.models import TechnicalWatch
from apps.insights.technical_watch import PARAMETERS
from apps.insights.decisions import decision_row
from apps.mercado.models import Provento
from .api import portfolio_payload
from .models import WorkbookState, WorkbookRevision
from .workbook_review import review,insights
from .research_radar import RESEARCH_RADAR

ZERO=Decimal(0)

def empty_data():
    return {'settings':{'monthly_expenses':None,'yield_percent':None,'cash_brl':None,'cash_usd':None},'dividends':[],'scenarios':[],'budgets':{},'notes':''}

def decimal(value,label,*,optional=False,maximum='100000000',places=8):
    if value in (None,''):
        if optional:return None
        raise ValueError(f'Preencha {label}.')
    if isinstance(value,(bool,list,dict)):raise ValueError(f'Valor inválido em {label}.')
    text=str(value).strip()
    if ',' in text:text=text.replace('.','').replace(',','.')
    try:n=Decimal(text)
    except InvalidOperation:raise ValueError(f'Valor inválido em {label}.') from None
    if not n.is_finite() or n<0 or n>Decimal(maximum) or n.as_tuple().exponent < -places:
        raise ValueError(f'Confira {label}: valor positivo, até {maximum}, com no máximo {places} casas.')
    return n

def normalize(payload):
    if not isinstance(payload,dict) or set(payload)-set(empty_data()):raise ValueError('Estrutura de planilha inválida.')
    result=empty_data();settings=payload.get('settings',{})
    if not isinstance(settings,dict) or set(settings)-set(result['settings']):raise ValueError('Configurações inválidas.')
    for k in result['settings']:
        n=decimal(settings.get(k),k,optional=True,maximum='100' if k=='yield_percent' else '100000000',places=4 if k=='yield_percent' else 2)
        result['settings'][k]=str(n) if n is not None else None
    notes=payload.get('notes','')
    if not isinstance(notes,str) or len(notes)>10000:raise ValueError('As anotações devem ter até 10.000 caracteres.')
    result['notes']=notes
    assets={a.symbol:a for a in Asset.objects.filter(is_active=True)}
    seen=set()
    for key,limit in [('dividends',300),('scenarios',100)]:
        rows=payload.get(key,[])
        if not isinstance(rows,list) or len(rows)>limit:raise ValueError(f'Limite de {limit} linhas em {key}.')
        for row in rows:
            if not isinstance(row,dict):raise ValueError('Linha inválida.')
            identifier=str(row.get('id') or uuid.uuid4())
            try:uuid.UUID(identifier)
            except ValueError:raise ValueError('Identificador de linha inválido.') from None
            if identifier in seen:raise ValueError('Linha duplicada.');
            seen.add(identifier)
            symbol=row.get('symbol','').strip().upper();asset=assets.get(symbol)
            if not asset:raise ValueError(f'Ativo não cadastrado: {symbol or "em branco"}.')
            currency='BRL' if asset.market in ('B3','CRIPTO') else 'USD'
            entry={'id':identifier,'symbol':symbol,'currency':currency}
            if key=='dividends':
                try:date=dt.date.fromisoformat(row.get('date',''))
                except (ValueError,TypeError):raise ValueError('Informe uma data válida para o provento.') from None
                status=row.get('status');kind=row.get('kind')
                if status not in ('received','announced') or kind not in ('dividend','jcp','interest','income','amortization'):raise ValueError('Tipo ou situação do provento inválidos.')
                if status=='received' and date>timezone.localdate():raise ValueError('Um recebimento não pode ter data futura.')
                gross=decimal(row.get('gross'),'valor bruto',places=2)
                if gross<=0 or asset.market=='CRIPTO':raise ValueError('Use proventos de ações/BDRs com valor bruto maior que zero.')
                tax=decimal(row.get('tax'),'descontos',optional=True,places=2)
                if tax is not None and tax>gross:raise ValueError('Descontos não podem superar o bruto.')
                reference=row.get('reference','')
                if not isinstance(reference,str) or len(reference)>500:raise ValueError('Referência deve ter até 500 caracteres.')
                entry.update(date=str(date),status=status,kind=kind,gross=str(gross),tax=str(tax) if tax is not None else None,reference=reference)
            else:
                qty=decimal(row.get('quantity'),'quantidade',maximum='1000000000',places=10);price=decimal(row.get('price'),'preço unitário')
                if qty<=0 or price<=0:raise ValueError('Quantidade e preço devem ser maiores que zero.')
                if asset.market=='B3' and qty!=qty.to_integral_value():raise ValueError('Na B3, use quantidade inteira; BDR não é fração da ação americana.')
                fee=decimal(row.get('fees'),'taxas',optional=True,places=2)
                entry.update(quantity=str(qty),price=str(price),fees=str(fee) if fee is not None else None)
            result[key].append(entry)
    budgets=payload.get('budgets',{})
    if not isinstance(budgets,dict) or len(budgets)>30:raise ValueError('Orçamentos inválidos.')
    for symbol,value in budgets.items():
        if symbol not in assets:raise ValueError('Ativo do orçamento não cadastrado.')
        n=decimal(value,'orçamento por ativo',optional=True,places=2)
        if n is not None:result['budgets'][symbol]=str(n)
    return result

def calculate(data,positions,watch):
    received={'BRL':ZERO,'USD':ZERO};pending=0
    months={(currency,m):ZERO for currency in received for m in range(1,13)}
    year=timezone.localdate().year
    dividends=[]
    for row in data['dividends']:
        gross=Decimal(row['gross']);tax=Decimal(row['tax']) if row['tax'] is not None else None
        net=gross-tax if tax is not None else None
        complete=row['status']=='received' and net is not None
        if row['status']=='received' and tax is None:pending+=1
        if complete and row['kind']!='amortization':
            received[row['currency']]+=net
            date=dt.date.fromisoformat(row['date'])
            if date.year==year:months[(row['currency'],date.month)]+=net
        dividends.append({**row,'net':net,'check':'Confirmado no acompanhamento' if complete else 'Confirmar descontos' if row['status']=='received' else 'Anúncio; fora da renda recebida'})
    # Simulations start with real positions but do not modify any purchase ledger.
    held={p['symbol']:{'quantity':Decimal(p['quantity']),'cost':Decimal(p['costBasis'])} for p in positions}
    scenarios=[]
    for row in data['scenarios']:
        old=held.get(row['symbol'],{'quantity':ZERO,'cost':ZERO});qty=Decimal(row['quantity']);price=Decimal(row['price']);fee=Decimal(row['fees']) if row['fees'] is not None else ZERO
        cost=qty*price+fee;new_qty=old['quantity']+qty;new_cost=old['cost']+cost;held[row['symbol']]={'quantity':new_qty,'cost':new_cost}
        scenarios.append({**row,'previous_quantity':old['quantity'],'previous_cost':old['cost'],'purchase_cost':cost,'new_quantity':new_qty,'new_cost':new_cost,'new_average':new_cost/new_qty,'check':'Simulação; sem taxas informadas' if row['fees'] is None else 'Simulação; não é compra registrada'})
    cash={c:Decimal(data['settings']['cash_'+c.lower()]) if data['settings']['cash_'+c.lower()] is not None else None for c in ('BRL','USD')}
    allocated={c:sum((Decimal(data['budgets'].get(w['symbol'],'0')) for w in watch if w['currency']==c),ZERO) for c in cash}
    radar=[]
    for w in watch:
        amount=data['budgets'].get(w['symbol']);budget=Decimal(amount) if amount is not None else None;c=w['currency'];q=None
        if budget is None:check='Defina orçamento'
        elif cash[c] is None:check='Defina caixa livre'
        elif allocated[c]>cash[c]:check='Distribuição excede caixa'
        elif w.get('blocks') or not w['price']:check='Dados insuficientes'
        else:
            asset=Asset.objects.get(symbol=w['symbol']);step=Decimal(1) if asset.market=='B3' else Decimal('.00000001') if asset.market=='CRIPTO' else Decimal('.00001')
            q=(budget/Decimal(w['price'])/step).to_integral_value(rounding=ROUND_FLOOR)*step;check='Limite aritmético; não é indicação de compra'
        radar.append({**w,'budget':budget,'quantity':q,'check':check})
    expense=Decimal(data['settings']['monthly_expenses']) if data['settings']['monthly_expenses'] is not None else None
    rate=Decimal(data['settings']['yield_percent'])/100 if data['settings']['yield_percent'] is not None else None
    return {'received':received,'pending_dividends':pending,'dividends':dividends,'scenarios':scenarios,'radar':radar,'year':year,'monthly':[{'month':m,'BRL':months[('BRL',m)],'USD':months[('USD',m)]} for m in range(1,13)],'required_capital':expense*12/rate if expense is not None and rate and rate>0 else None,'allocated':allocated}

def context(request,account,data=None):
    state=WorkbookState.objects.filter(account=account).first()
    data=data if data is not None else state.data if state and state.data else empty_data()
    p=portfolio_payload({'account':account,'history':'0'},request.user.get_username())
    watch=[]
    for w in TechnicalWatch.objects.filter(user=request.user,active=True,**{account:True}).select_related('asset'):
        r=decision_row(w.asset,None,PARAMETERS,None,timezone.now());watch.append({k:r.get(k) for k in ['symbol','currency','price','source','quote_time','collected_at','quote_status','signal','trend','sma20','sma50','rsi','blocks','reasons','warnings']})
    ids={x['id'] for x in p['positions']}
    announced=list(Provento.objects.filter(asset_id__in=ids,data_pagamento__gte=timezone.localdate()).select_related('asset').order_by('data_pagamento').values('asset__symbol','tipo','valor','moeda','data_com','data_pagamento','source'))
    return {'researchRadar':RESEARCH_RADAR,'account':account,'owner':p['meta']['ownerLabel'],'version':state.version if state else 0,'updatedAt':state.updated_at if state else None,'generatedAt':p['meta']['generatedAt'],'data':data,'positions':p['positions'],'totals':p['totals'],'announced':announced,'brokerSnapshot':review(account,p['positions']),'insights':insights(p['positions']),'calculated':calculate(data,p['positions'],watch),'assets':list(Asset.objects.filter(is_active=True).values('symbol','name','market','native_currency'))}

@never_cache
@require_http_methods(['GET','POST'])
def workbook(request):
    if not request.user.is_authenticated:return JsonResponse({'error':'Entre no portal para abrir a planilha.'},status=401)
    account=request.GET.get('account','ana')
    if account not in ('ana','bruno'):return JsonResponse({'error':'Carteira inválida.'},status=400)
    if request.method=='POST':
        if len(request.body)>180000:return JsonResponse({'error':'Planilha muito grande.'},status=413)
        try:
            payload=json.loads(request.body);data=normalize(payload.get('data'));version=payload.get('version');action=payload.get('action','save')
            if action not in ('save','preview') or type(version) is not int or version<0:raise ValueError('Versão ou ação inválida.')
        except (ValueError,TypeError,AttributeError,ValidationError) as e:return JsonResponse({'error':str(e)},status=400)
        if action=='save':
            with transaction.atomic():
                row,_=WorkbookState.objects.get_or_create(account=account,defaults={'data':empty_data()})
                row=WorkbookState.objects.select_for_update().get(pk=row.pk)
                if row.version!=version:return JsonResponse({'error':'A planilha foi alterada em outra janela. Seus campos continuam na tela; recarregue antes de salvar.','version':row.version},status=409)
                row.version+=1;row.data=data;row.updated_by=request.user;row.save()
                WorkbookRevision.objects.create(workbook=row,version=row.version,data=data,user=request.user)
        return JsonResponse(context(request,account,data),encoder=DjangoJSONEncoder)
    payload=context(request,account)
    if request.GET.get('download')=='1':
        from .workbook_export import export
        response=HttpResponse(export(payload),content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition']=f'attachment; filename="ID3_{account}_planilha.xlsx"';response['X-Content-Type-Options']='nosniff';return response
    return JsonResponse(payload,encoder=DjangoJSONEncoder)

@never_cache
@require_http_methods(['GET'])
def assistant_status(request):
    if not request.user.is_authenticated:return JsonResponse({'error':'Entre no portal.'},status=401)
    from django.conf import settings
    from apps.insights.assistant import availability,active_model
    if getattr(settings,'PORTAL_AI_PROVIDER','openai')=='gemini':
        from apps.insights.gemini_assistant import status
        return JsonResponse(status())
    if getattr(settings,'PORTAL_AI_PROVIDER','openai')=='ollama':
        from apps.insights.ollama_assistant import status
        return JsonResponse({**status(),'provider':'ollama','providerLabel':'Ollama no Mac','model':settings.PORTAL_OLLAMA_MODEL,'dailyLimit':settings.PORTAL_OLLAMA_DAILY_TURNS,'billing':'Sem cobrança de API; usa os recursos do Mac.','paidEnabled':False,'keyConfigured':False,'docsUrl':'https://docs.ollama.com/'})
    return JsonResponse({'available':availability(),'keyConfigured':bool(settings.PORTAL_AI_KEY),'enabled':bool(settings.PORTAL_AI_ENABLED),'model':active_model(),'dailyLimit':settings.PORTAL_AI_DAILY_TURNS,'provider':'openai','providerLabel':'OpenAI','reason':None if availability() else 'Falta configurar a chave de API e habilitar o acesso no servidor.','pricingUrl':'https://developers.openai.com/api/docs/pricing','keysUrl':'https://platform.openai.com/api-keys'})
