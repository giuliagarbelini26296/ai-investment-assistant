import re
import uuid
from decimal import Decimal, InvalidOperation
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods
from apps.alerts.forms import interpretar_numero
from apps.portfolio.models import Asset
from .models import Portfolio
from .services import purchase_preview, record_purchase

SALT='managed_portfolios.purchase.v1'


class AssetChoice(forms.ModelChoiceField):
    def label_from_instance(self,asset):
        return f'{asset.symbol} — {asset.name} · {asset.market}'


class PurchaseForm(forms.Form):
    asset_choice=AssetChoice(label='Qual ativo você comprou?',queryset=Asset.objects.none(),empty_label='Escolha o ativo')
    currency=forms.ChoiceField(label='Moeda da compra',choices=[('BRL','Reais (R$)'),('USD','Dólares (US$)')],initial='BRL')
    quantity=forms.CharField(label='Quantidade comprada nesta operação',widget=forms.TextInput(attrs={'inputmode':'decimal'}))
    unit_price=forms.CharField(label='Preço pago por ação, cota ou unidade',widget=forms.TextInput(attrs={'inputmode':'decimal'}))
    trade_date=forms.DateField(label='Data da compra',widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    submission_key=forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self,*args,portfolio,preview=False,**kwargs):
        self.portfolio=portfolio
        super().__init__(*args,**kwargs)
        self.fields['asset_choice'].queryset=Asset.objects.filter(is_active=True).exclude(market='CAMBIO').order_by('symbol','market')
        self.fields['submission_key'].required=not preview

    def clean(self):
        data=super().clean()
        asset=data.get('asset_choice')
        for key in ('quantity','unit_price'):
            if key not in data:
                continue
            raw=data[key].strip()
            try:
                if not re.fullmatch(r'\d[\d.,]*',raw):
                    raise ValueError()
                if key=='quantity' and asset and not asset.requires_integer_quantity and ',' not in raw and raw.count('.')==1:
                    raw=raw.replace('.',',')
                value=interpretar_numero(raw)
                places=10 if key=='quantity' else 8
                if not value.is_finite() or value<=0 or value.as_tuple().exponent < -places or value>=Decimal('1e16'):
                    raise ValueError()
                data[key]=value
            except (InvalidOperation,ValueError,TypeError):
                self.add_error(key,'Informe um número positivo válido, sem letras ou percentuais.')
        if asset and data.get('currency'):
            expected='BRL' if asset.market=='B3' else 'USD' if asset.market in ('NYSE','NASDAQ') else None
            if expected and data['currency']!=expected:
                self.add_error('currency','Use '+expected+' para este mercado.')
        if asset and isinstance(data.get('quantity'),Decimal) and asset.requires_integer_quantity and data['quantity']!=data['quantity'].to_integral_value():
            self.add_error('quantity','A B3 exige quantidade inteira.')
        if data.get('trade_date') and data['trade_date']>timezone.localdate():
            self.add_error('trade_date','A data da compra não pode ser futura.')
        if not self.errors and asset:
            try:
                preview_for(self)
            except ValidationError as exc:
                self.add_error(None,exc)
        return data


def decimal_text(value):
    return format(value.normalize(),'f')


def preview_for(form):
    d=form.cleaned_data
    return purchase_preview(form.portfolio,d['asset_choice'],d['currency'],d['quantity'],d['unit_price'],d['trade_date'])


def identity(form,user):
    d=form.cleaned_data
    return {'portfolio':form.portfolio.slug,'user':user.pk,'asset':d['asset_choice'].pk,'currency':d['currency'],
        'quantity':decimal_text(d['quantity']),'price':decimal_text(d['unit_price']),
        'date':d['trade_date'].isoformat(),'key':str(d['submission_key'])}


@never_cache
@require_GET
def preview(request,slug):
    if not request.user.is_authenticated:
        return JsonResponse({'valid':False,'error':'authentication_required'},status=401)
    portfolio=get_object_or_404(Portfolio,slug=slug,legacy=False)
    form=PurchaseForm(request.GET,portfolio=portfolio,preview=True)
    if not form.is_valid():
        return JsonResponse({'valid':False,'errors':form.errors.get_json_data()},status=400)
    return JsonResponse({'valid':True,**preview_for(form)})


@never_cache
@login_required
@require_http_methods(['GET','POST'])
def purchase(request,slug):
    portfolio=get_object_or_404(Portfolio,slug=slug,legacy=False)
    initial={'trade_date':timezone.localdate(),'submission_key':uuid.uuid4(),'currency':'BRL'}
    if request.GET.get('ativo','').isdigit():
        initial['asset_choice']=request.GET['ativo']
    form=PurchaseForm(request.POST if request.method=='POST' else None,portfolio=portfolio,initial=initial)
    review=signature=None
    if request.method=='POST' and form.is_valid():
        review=preview_for(form)
        action=request.POST.get('action','review')
        if action=='confirm':
            try:
                if request.POST.get('not_included')!='yes':
                    raise ValidationError('Confirme que esta compra ainda não está incluída na carteira '+portfolio.name+'.')
                signed=signing.loads(request.POST.get('review_signature',''),salt=SALT,max_age=1800)
                if {k:v for k,v in signed.items() if k!='before'}!=identity(form,request.user):
                    raise signing.BadSignature()
                d=form.cleaned_data
                receipt,created=record_purchase(portfolio,d['asset_choice'],request.user,
                    quantity=d['quantity'],unit_price=d['unit_price'],currency=d['currency'],trade_date=d['trade_date'],
                    submission_key=d['submission_key'],reviewed_before=signed['before'])
            except (signing.BadSignature,signing.SignatureExpired):
                form.add_error(None,'Conferência expirada ou alterada. Confira novamente antes de confirmar.')
            except (ValidationError,IntegrityError) as exc:
                form.add_error(None,exc if isinstance(exc,ValidationError) else 'Registro já utilizado. Abra uma nova compra.')
                review=preview_for(form)
            else:
                messages.success(request,('Compra registrada na carteira '+portfolio.name+'. Preço médio atualizado.'
                    if created else 'Esta compra já foi registrada. Nenhuma duplicação criada.'))
                return redirect('/?carteira='+portfolio.slug+'#/carteira')
        if action=='edit':
            review=None
        if review:
            signed=identity(form,request.user)
            signed['before']=[decimal_text(review['before'][k]) for k in ('quantity','cost')]
            signature=signing.dumps(signed,salt=SALT,compress=True)
    return render(request,'managed_portfolios/purchase.html',{'form':form,'preview':review,
        'review_signature':signature,'managed_portfolio':portfolio})
