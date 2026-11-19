"""Record completed sales in either portfolio, with review and an immutable cost snapshot."""
import uuid
from decimal import Decimal as D
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from .models import Asset, Sale
from . import services
from apps.managed_portfolios.models import Portfolio
from apps.managed_portfolios import services as managed

SALT='portfolio.sale.review.v1'

class SaleForm(forms.Form):
    asset = forms.ModelChoiceField(label='Ativo vendido', queryset=Asset.objects.none())
    trade_date = forms.DateField(label='Data da execução', widget=forms.DateInput(attrs={'type':'date'}))
    quantity = forms.DecimalField(label='Quantidade efetivamente vendida', min_value=D('.0000000001'), max_digits=28, decimal_places=10, localize=True)
    unit_price = forms.DecimalField(label='Preço médio executado da venda', min_value=D('.00000001'), max_digits=24, decimal_places=8, localize=True)
    fees = forms.DecimalField(label='Corretagem e taxas da venda (opcional)', min_value=D(0), max_digits=28, decimal_places=8, required=False, localize=True, help_text='Deixe em branco se ainda não possui a nota. Zero significa sem taxas, conforme a nota.')
    evidence = forms.CharField(label='Referência da execução (opcional)', required=False, max_length=2000, widget=forms.Textarea(attrs={'rows':2}))
    submission_key = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['asset'].queryset=Asset.objects.filter(is_active=True).exclude(market='CAMBIO').order_by('symbol')

    def clean(self):
        d=super().clean();a=d.get('asset');q=d.get('quantity');date=d.get('trade_date')
        if a and q and a.requires_integer_quantity and q != q.to_integral_value():
            self.add_error('quantity','A B3 exige quantidade inteira.')
        if date and date>timezone.localdate():self.add_error('trade_date','Informe uma data que já ocorreu.')
        return d


def current(account,asset):
    if account=='ana':return services.posicao_do_ativo(asset),asset.native_currency,asset.position_balance
    return managed.position(Portfolio.objects.get(slug=account),asset)


def preview(account,asset,*,quantity,unit_price,trade_date,fees=None):
    pos,currency,balance=current(account,asset)
    if not pos or quantity>pos.quantidade:raise ValidationError('A quantidade vendida supera os papéis desta carteira.')
    if quantity<=0 or unit_price<=0 or (fees is not None and fees<0):raise ValidationError('Confira quantidade, preço e taxas.')
    if not all(n.is_finite() for n in [quantity,unit_price]+([fees] if fees is not None else [])):raise ValidationError('Informe valores finitos.')
    if asset.requires_integer_quantity and quantity!=quantity.to_integral_value():raise ValidationError('A B3 exige quantidade inteira.')
    if trade_date>timezone.localdate():raise ValidationError('A data da execução não pode ser futura.')
    if balance and trade_date<balance.reference_date:raise ValidationError('A venda é anterior ao saldo conciliado; pode já estar incluída. Confira o histórico antes de registrar.')
    allocated=(pos.investido*quantity/pos.quantidade).quantize(D('.00000001'))
    remaining=pos.quantidade-quantity;cost=pos.investido-allocated
    gross=quantity*unit_price;gain=gross-allocated
    return {'symbol':asset.symbol,'currency':currency,'before_quantity':pos.quantidade,'before_cost':pos.investido.quantize(D('.00000001')),
        'before_average':pos.preco_medio,'allocated_cost':allocated,'quantity':quantity,'unit_price':unit_price,
        'gross_proceeds':gross,'gross_gain':gain,'fees':fees,'net_gain':gain-fees if fees is not None else None,
        'remaining_quantity':remaining,'remaining_cost':cost,'remaining_average':cost/remaining if remaining else None}


def identity(data,user,account):
    return {'account':account,'user':user.pk,'asset':data['asset'].pk,'date':data['trade_date'].isoformat(),
        'quantity':str(data['quantity'].normalize()),'price':str(data['unit_price'].normalize()),
        'fees':str(data['fees'].normalize()) if data.get('fees') is not None else None,
        'evidence':data.get('evidence',''),'key':str(data['submission_key'])}


@transaction.atomic
def record_sale(account,asset,user,*,quantity,unit_price,trade_date,submission_key,reviewed_before,fees=None,evidence=''):
    if account not in ('ana','bruno'):raise ValidationError('Carteira inválida.')
    # Same account/asset locks as purchases and reconciliation; no cross-account positions.
    if account=='bruno':Portfolio.objects.select_for_update().get(slug=account)
    asset=Asset.objects.select_for_update().get(pk=asset.pk)
    existing=Sale.objects.filter(submission_key=submission_key).first()
    if existing:
        if (existing.account,existing.asset_id,existing.quantity,existing.unit_price,existing.fees,existing.trade_date,existing.created_by_id,existing.evidence)!=(account,asset.pk,quantity,unit_price,fees,trade_date,user.pk if user else None,evidence):
            raise ValidationError('Este identificador já foi usado para outra execução.')
        return existing,False
    p=preview(account,asset,quantity=quantity,unit_price=unit_price,trade_date=trade_date,fees=fees)
    if [str(p['before_quantity']),str(p['before_cost'])] != reviewed_before:
        raise ValidationError('A posição mudou desde a conferência. Confira novamente antes de registrar.')
    sale=Sale(account=account,asset=asset,quantity=quantity,unit_price=unit_price,currency=p['currency'],fees=fees,
        trade_date=trade_date,submission_key=submission_key,allocated_cost=p['allocated_cost'],before_quantity=p['before_quantity'],
        before_cost=p['before_cost'],evidence=evidence,created_by=user)
    sale.full_clean();sale.save()
    # Preserve every original buy and balance. A new balance carries only unsold cost.
    values={'reference_date':trade_date,'quantity':p['remaining_quantity'],'invested_cost':p['remaining_cost'],
        'source_key':'sale:'+str(submission_key),'evidence':f'Venda registrada #{sale.pk}: {quantity} {asset.symbol} a {unit_price} {p["currency"]}. '+evidence}
    average=p['remaining_average'] or p['before_average']
    average=average.quantize(D('.00000001'))
    if account=='ana':services.registrar_saldo(asset,average_price=average,**values)
    else:managed.reconcile(Portfolio.objects.get(slug=account),asset,reported_average=average,currency=p['currency'],**values)
    return sale,True


@never_cache
@login_required
@require_http_methods(['GET','POST'])
def sale_page(request):
    account=request.GET.get('carteira','ana')
    if account not in ('ana','bruno'):
        from django.http import HttpResponseBadRequest
        return HttpResponseBadRequest('Carteira inválida.')
    form=SaleForm(request.POST if request.method=='POST' else None,initial={'submission_key':uuid.uuid4(),'trade_date':timezone.localdate(),'asset':request.GET.get('ativo')})
    p=signature=None
    if request.method=='POST' and form.is_valid():
        d=form.cleaned_data
        try:
            if request.POST.get('action')=='confirm':
                signed=signing.loads(request.POST.get('review_signature',''),salt=SALT,max_age=1800)
                if signed['identity']!=identity(d,request.user,account) or request.POST.get('ack')!='yes':raise signing.BadSignature()
                sale,created=record_sale(account,d['asset'],request.user,**{k:d[k] for k in ['quantity','unit_price','fees','trade_date','submission_key','evidence']},reviewed_before=signed['before'])
                messages.success(request,'Venda registrada e posição atualizada.' if created else 'Venda já registrada. Nenhuma duplicação.')
                return redirect('/vendas/registrar/?carteira='+account)
            if request.POST.get('action')!='edit':
                p=preview(account,d['asset'],**{k:d[k] for k in ['quantity','unit_price','fees','trade_date']})
                signature=signing.dumps({'identity':identity(d,request.user,account),'before':[str(p['before_quantity']),str(p['before_cost'])]},salt=SALT)
        except (signing.BadSignature,signing.SignatureExpired):form.add_error(None,'Confira os dados novamente; a confirmação expirou ou foi alterada.')
        except ValidationError as exc:form.add_error(None,exc)
    return render(request,'portfolio/sale.html',{'form':form,'preview':p,'review_signature':signature,'account':account,
        'history':Sale.objects.filter(account=account).select_related('asset')[:50]})
