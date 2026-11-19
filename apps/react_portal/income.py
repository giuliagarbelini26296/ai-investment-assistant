"""Confirmed workbook receipts; market gains and cash balances are different measures."""
import datetime as dt
from decimal import Decimal as D
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from .models import WorkbookState

KINDS={'dividend':'Dividendos','jcp':'JCP','interest':'Juros','income':'Rendimentos','amortization':'Amortizações'}
ZERO=D(0)

def summarize(data,year,month,today=None):
    today=today or timezone.localdate()
    expenses=data.get('settings',{}).get('monthly_expenses')
    expenses=D(expenses) if expenses not in (None,'') else None
    if expenses is not None and expenses<=0:expenses=None
    result={}
    for currency in ('BRL','USD'):
        annual=[];period=[];pending=[];announced=[]
        monthly=[{'month':m,'income_net':None,'confirmed_count':0} for m in range(1,13)]
        kinds={}
        for row in data.get('dividends',[]):
            if row['currency']!=currency:continue
            date=dt.date.fromisoformat(row['date'])
            if date.year!=year:continue
            gross=D(row['gross']);tax=D(row['tax']) if row.get('tax') is not None else None
            net=gross-tax if tax is not None else None
            item={**row,'gross':gross,'tax':tax,'net':net}
            in_month=date.month==month
            if row['status']=='announced':
                if in_month:announced.append(item)
                continue
            if row['status']!='received' or date>today:continue
            if net is None:
                if in_month:pending.append(item)
                continue
            annual.append(item)
            if in_month:period.append(item)
            if row['kind']!='amortization':
                bucket=monthly[date.month-1];bucket['confirmed_count']+=1
                bucket['income_net']=(bucket['income_net'] or ZERO)+net
        income_rows=[r for r in period if r['kind']!='amortization']
        income_net=sum((r['net'] for r in income_rows),ZERO) if income_rows else None
        amort=[r for r in period if r['kind']=='amortization']
        for kind,label in KINDS.items():
            items=[r for r in period if r['kind']==kind]
            if items:kinds[kind]={'label':label,'gross':sum((r['gross'] for r in items),ZERO),'tax':sum((r['tax'] for r in items),ZERO),'net':sum((r['net'] for r in items),ZERO),'count':len(items),'is_income':kind!='amortization'}
        annual_income=[r for r in annual if r['kind']!='amortization']
        coverage=income_net/expenses*100 if currency=='BRL' and income_net is not None and expenses is not None else None
        result[currency]={'income_net':income_net,'annual_income_net':sum((r['net'] for r in annual_income),ZERO) if annual_income else None,
            'receipts_net':sum((r['net'] for r in period),ZERO) if period else None,
            'amortization_net':sum((r['net'] for r in amort),ZERO) if amort else None,
            'confirmed_income_count':len(income_rows),'confirmed_count':len(period),'annual_confirmed_count':len(annual),
            'pending_count':len(pending),'announced_count':len(announced),'announced':announced,
            'categories':kinds,'monthly':monthly,'expenses':expenses if currency=='BRL' else None,'expense_coverage_percent':coverage,
            'expense_gap':max(ZERO,expenses-income_net) if currency=='BRL' and income_net is not None and expenses is not None else None}
    years={today.year,year}
    years.update(dt.date.fromisoformat(r['date']).year for r in data.get('dividends',[]))
    return {'year':year,'month':month,'years':sorted(years,reverse=True),'currencies':result,'source':'Registros salvos na planilha integrada','coverage':'Somente registros recebidos com descontos informados; anúncios e amortizações não são renda recebida. Não representa saldo disponível ou lucro de vendas.'}

@never_cache
@require_GET
def income(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error':{'code':'authentication_required','message':'Entre no portal.'}},status=401)
    account=request.GET.get('account','ana')
    if account not in ('ana','bruno'):return JsonResponse({'error':'Carteira inválida.'},status=400)
    today=timezone.localdate()
    try:
        year=int(request.GET.get('year',today.year));month=int(request.GET.get('month',today.month))
        if not 1970<=year<=2100 or not 1<=month<=12:raise ValueError()
    except (ValueError,TypeError):return JsonResponse({'error':'Período inválido.'},status=400)
    state=WorkbookState.objects.filter(account=account).first()
    data=state.data if state and state.data else {'settings':{},'dividends':[]}
    return JsonResponse({'account':account,'owner':'Ana' if account=='ana' else 'Bruno','generatedAt':timezone.now(),'readOnly':True,'version':state.version if state else 0,**summarize(data,year,month,today)})
