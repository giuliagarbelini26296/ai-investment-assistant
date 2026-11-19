"""Guided purchases: exact native calculation, explicit review and safe retry."""
import uuid
import re
from decimal import Decimal

from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Case, Exists, IntegerField, OuterRef, Value, When
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from apps.valuation import core
from . import services
from .forms import LotForm
from .models import Asset, Lot, PositionBalance, PurchaseReceipt

SALT = 'portfolio.guided_purchase.v1'


class AssetChoice(forms.ModelChoiceField):
    def label_from_instance(self, asset):
        return f'{asset.symbol} — {asset.name} · {asset.market} · {asset.native_currency} · {asset.get_custody_display()}'


class PurchaseForm(LotForm):
    asset_choice = AssetChoice(label='Qual ativo você comprou?', queryset=Asset.objects.none(), empty_label='Escolha o ativo')
    submission_key = forms.UUIDField(widget=forms.HiddenInput)

    class Meta(LotForm.Meta):
        fields = ['trade_date', 'quantity', 'unit_price']
        widgets = {'trade_date': forms.DateInput(format='%Y-%m-%d', attrs={'type':'date'})}

    def __init__(self, data=None, *, initial=None, preview=False):
        initial = initial or {}
        value = data.get('asset_choice') if data is not None else initial.get('asset_choice')
        if isinstance(value, Asset):
            value = value.pk
        held = Exists(PositionBalance.objects.filter(asset_id=OuterRef('pk'))) | Exists(
            Lot.objects.filter(asset_id=OuterRef('pk'), cancelado_em__isnull=True))
        assets = Asset.objects.filter(is_active=True).exclude(market='CAMBIO').annotate(
            purchase_priority=Case(When(held, then=Value(0)), default=Value(1), output_field=IntegerField())
        ).order_by('purchase_priority', 'symbol', 'market')
        selected = assets.filter(pk=value).first() if str(value or '').isdigit() else None
        super().__init__(data=data, asset=selected, initial=initial)
        self.fields['asset_choice'].queryset = assets
        self.fields['submission_key'].required = not preview
        self.fields['trade_date'].label = 'Data da compra'
        self.fields['quantity'].label = 'Quantidade comprada nesta operação'
        self.fields['quantity'].help_text = 'Informe só o que comprou agora, não o total que já possui.'
        self.fields['unit_price'].label = 'Preço pago por ação ou cota'
        self.fields['unit_price'].help_text = 'Use o preço executado da nova compra. O preço médio será calculado.'
        self.fields['confirmar_valores'].label = 'Conferi os valores, mesmo diferentes da referência de mercado'
        self.order_fields(['asset_choice', 'trade_date', 'quantity', 'unit_price', 'confirmar_valores', 'submission_key'])

    def _numero(self, nome, quantidade=False):
        value = (self.data.get(self.add_prefix(nome)) or '').strip().replace(' ', '')
        if value and not re.fullmatch(r'-?\d[\d.,]*', value):
            raise forms.ValidationError('Informe apenas o número, como 100 ou 16,30, sem percentuais ou letras.')
        if value.count(',') > 1 and '.' not in value and not re.fullmatch(r'-?\d{1,3}(,\d{3})+',value):
            raise forms.ValidationError('Confira os separadores. Exemplo: 1.000,50.')
        return super()._numero(nome, quantidade=quantidade)

    def clean(self):
        cleaned = super().clean()
        date = cleaned.get('trade_date')
        balance = self.asset.position_balance if self.asset else None
        if date and balance and date < balance.reference_date:
            self.add_error('trade_date', 'Esta compra é anterior ao saldo conciliado de '
                           f'{balance.reference_date:%d/%m/%Y}. Não a some novamente à posição atual.')
        return cleaned


def decimal_text(value):
    return format(value.normalize(), 'f')


def position_data(asset):
    pos = services.posicao_do_ativo(asset)
    return {'quantity': pos.quantidade if pos else Decimal(0),
            'averagePrice': pos.preco_medio if pos else None,
            'cost': pos.investido if pos else Decimal(0)}


def preview_data(form):
    asset = form.cleaned_data['asset_choice']
    pos = services.posicao_do_ativo(asset)
    before = {'quantity':pos.quantidade if pos else Decimal(0),
              'averagePrice':pos.preco_medio if pos else None, 'cost':pos.investido if pos else Decimal(0)}
    quantity, price = form.cleaned_data['quantity'], form.cleaned_data['unit_price']
    # Same pure calculation used to value the portfolio, including its reconciled cost.
    after = core.adicionar_compra(pos, core.Lote(quantity, price))
    return {'symbol': asset.symbol, 'currency': asset.native_currency, 'broker':asset.get_custody_display(),
            'before': before, 'purchase': {'quantity':quantity, 'unitPrice':price,
                'total': quantity * price, 'date':form.cleaned_data['trade_date']},
            'after': {'quantity':after.quantidade, 'averagePrice':after.preco_medio, 'cost':after.investido}}


def purchase_identity(form, user):
    data = form.cleaned_data
    return {'user': user.pk, 'asset':data['asset_choice'].pk,
            'quantity':decimal_text(data['quantity']), 'price':decimal_text(data['unit_price']),
            'date':data['trade_date'].isoformat(), 'key':str(data['submission_key'])}


def signed_review(form, user, preview):
    payload = purchase_identity(form, user)
    payload['before'] = [decimal_text(preview['before']['quantity']), decimal_text(preview['before']['cost'])]
    return signing.dumps(payload, salt=SALT, compress=True)


class PositionChanged(ValidationError):
    pass


@transaction.atomic
def record_purchase(form, user, reviewed):
    data = form.cleaned_data
    # Serializes retries even if a token is mistakenly reused for another asset.
    get_user_model().objects.select_for_update().get(pk=user.pk)
    identity = purchase_identity(form, user)
    receipt = PurchaseReceipt.objects.select_related('lot').filter(pk=data['submission_key']).first()
    if receipt:
        lot = receipt.lot
        same = (receipt.user_id == user.pk and lot.asset_id == identity['asset'] and
                lot.trade_date.isoformat() == identity['date'] and
                lot.quantity == data['quantity'] and lot.unit_price == data['unit_price'])
        if not same:
            raise ValidationError('Este registro já foi usado com outros dados. Abra uma nova compra.')
        return lot, False
    asset = Asset.objects.select_for_update().get(pk=data['asset_choice'].pk, is_active=True)
    current = position_data(asset)
    if reviewed['before'] != [decimal_text(current['quantity']), decimal_text(current['cost'])]:
        raise PositionChanged('A posição mudou desde a conferência. Confira o novo total antes de registrar.')
    lot = services.criar_lote(asset, user, trade_date=data['trade_date'],
                              quantity=data['quantity'], unit_price=data['unit_price'])
    PurchaseReceipt.objects.create(submission_key=data['submission_key'], lot=lot, user=user)
    return lot, True


@never_cache
@require_GET
def purchase_preview(request):
    if not request.user.is_authenticated:
        return JsonResponse({'valid':False, 'error':'authentication_required'}, status=401)
    form = PurchaseForm(request.GET, preview=True)
    if not form.is_valid():
        return JsonResponse({'valid':False, 'errors':form.errors.get_json_data()}, status=400)
    return JsonResponse({'valid':True, **preview_data(form)})


@never_cache
@login_required
@require_http_methods(['GET', 'POST'])
def purchase_page(request):
    initial = {'trade_date':timezone.localdate(), 'submission_key':uuid.uuid4()}
    if request.GET.get('ativo', '').isdigit():
        initial['asset_choice'] = request.GET['ativo']
    form = PurchaseForm(request.POST if request.method == 'POST' else None, initial=initial)
    preview = signature = None
    if request.method == 'POST' and form.is_valid():
        preview = preview_data(form)
        action = request.POST.get('action', 'review')
        if action == 'confirm':
            try:
                if request.POST.get('not_included') != 'yes':
                    raise ValidationError('Confirme que esta compra ainda não está incluída na posição atual.')
                reviewed = signing.loads(request.POST.get('review_signature',''), salt=SALT, max_age=1800)
                expected = {k:v for k,v in reviewed.items() if k != 'before'}
                if expected != purchase_identity(form, request.user):
                    raise signing.BadSignature('Valores alterados')
                lot, created = record_purchase(form, request.user, reviewed)
            except (signing.BadSignature, signing.SignatureExpired):
                form.add_error(None, 'A conferência expirou ou os valores mudaram. Confira novamente abaixo.')
            except PositionChanged as exc:
                form.add_error(None, exc)
                preview = preview_data(form)
            except (ValidationError, IntegrityError) as exc:
                form.add_error(None, exc if isinstance(exc, ValidationError) else 'Registro já utilizado. Abra uma nova compra.')
                preview = None
            else:
                text = (f'Compra de {lot.asset.symbol} registrada. Preço médio recalculado automaticamente.'
                        if created else 'Essa compra já estava registrada. Nenhuma duplicação foi criada.')
                if not created and lot.is_cancelled:
                    text = 'Esse registro já foi cancelado e não foi incluído novamente.'
                messages.success(request, text)
                return redirect('home')
        elif action == 'edit':
            preview = None
        if preview is not None:
            signature = signed_review(form, request.user, preview)
    return render(request, 'portfolio/quick_purchase.html',
                  {'form':form, 'preview':preview, 'review_signature':signature})
