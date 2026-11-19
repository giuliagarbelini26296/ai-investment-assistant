import uuid
from decimal import Decimal

from django import forms
from django.utils import timezone

from apps.portfolio.models import Asset
from .models import CashEntry, RiskPreference


class LocalDecimal(forms.DecimalField):
    widget = forms.TextInput(attrs={'inputmode': 'decimal', 'autocomplete': 'off'})

    def to_python(self, value):
        if isinstance(value, str):
            value = value.strip().replace(' ', '')
            if ',' in value:
                value = value.replace('.', '').replace(',', '.')
        return super().to_python(value)


class RiskForm(forms.ModelForm):
    asset_limit = LocalDecimal(label='Limite por empresa (%)', min_value=Decimal('.01'), max_value=100, decimal_places=2)
    sector_limit = LocalDecimal(label='Limite por setor (%)', min_value=Decimal('.01'), max_value=100, decimal_places=2)
    country_limit = LocalDecimal(label='Limite por país (%)', min_value=Decimal('.01'), max_value=100, decimal_places=2)

    class Meta:
        model = RiskPreference
        fields = ['asset_limit', 'sector_limit', 'country_limit']


class CashForm(forms.ModelForm):
    submission_key = forms.UUIDField(widget=forms.HiddenInput)
    amount = LocalDecimal(label='Valor total (sem %)', min_value=Decimal('.00000001'), max_digits=24, decimal_places=8)
    confirm = forms.BooleanField(label='Conferi o extrato e este valor ainda não foi lançado aqui.')

    class Meta:
        model = CashEntry
        fields = ['asset', 'currency', 'kind', 'date', 'amount', 'reference']
        widgets = {'date': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['asset'].queryset = Asset.objects.filter(is_active=True).exclude(market='CAMBIO').order_by('symbol')
        self.fields['asset'].label = 'Ativo (opcional para despesas gerais)'
        self.initial.setdefault('submission_key', uuid.uuid4())
        self.initial.setdefault('date', timezone.localdate())
        self.fields['amount'].help_text = 'Use o valor efetivo na moeda escolhida; rentabilidade percentual não é provento.'


class SimulationForm(forms.Form):
    asset = forms.ModelChoiceField(label='Ativo', queryset=Asset.objects.none())
    quantity = LocalDecimal(label='Quantidade da nova compra', min_value=Decimal('.0000000001'), max_digits=28, decimal_places=10)
    price = LocalDecimal(label='Preço por ação ou cota', min_value=Decimal('.00000001'), max_digits=20, decimal_places=8)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['asset'].queryset = Asset.objects.filter(is_active=True).exclude(market='CAMBIO').order_by('symbol')

    def clean(self):
        data = super().clean()
        asset, q = data.get('asset'), data.get('quantity')
        if asset and q and asset.requires_integer_quantity and q != q.to_integral_value():
            self.add_error('quantity', 'Na B3, informe uma quantidade inteira.')
        if q and data.get('price') and q * data['price'] >= Decimal('1e24'):
            self.add_error('quantity', 'Valor da operação acima do limite de cálculo.')
        return data
