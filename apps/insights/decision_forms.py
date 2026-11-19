from django import forms
from decimal import Decimal
import re
from django.utils import timezone
from apps.portfolio.models import Asset
from .forms import LocalDecimal
from .models import DecisionPreference, ValueReference


class MoneyDecimal(LocalDecimal):
    def to_python(self,value):
        if isinstance(value,str):
            value=value.strip()
            if ',' not in value and re.fullmatch(r'-?\d{1,3}(?:\.\d{3})+',value):
                value=value.replace('.','')
        return super().to_python(value)


class DecisionForm(forms.ModelForm):
    budget_brl=MoneyDecimal(label='Dinheiro novo disponível · R$',required=False,min_value=0,max_digits=24,decimal_places=2)
    budget_usd=MoneyDecimal(label='Dinheiro novo disponível · US$',required=False,min_value=0,max_digits=24,decimal_places=2)
    asset_limit=LocalDecimal(label='Máximo por ativo nesta moeda (%)',min_value=1,max_value=100,decimal_places=2)
    plan_risk=LocalDecimal(label='Perda máxima simulada do plano (%)',min_value=Decimal('.01'),max_value=10,decimal_places=2)
    stop_distance=LocalDecimal(label='Queda do cenário de perda (%)',min_value=1,max_value=80,decimal_places=2)
    entry_tolerance=LocalDecimal(label='Distância máxima acima da média de 20 sessões (%)',min_value=0,max_value=20,decimal_places=2)
    safety_margin=LocalDecimal(label='Margem sobre o preço de referência (%)',min_value=0,max_value=80,decimal_places=2)

    class Meta:
        model=DecisionPreference
        fields=['profile','horizon','budget_brl','budget_usd','asset_limit','plan_risk','stop_distance','entry_tolerance','safety_margin','monitor_enabled']
        labels={'profile':'Perfil do cenário','horizon':'Horizonte da análise','monitor_enabled':'Monitorar sinais automaticamente no painel do portal'}

    def clean(self):
        data=super().clean()
        if data.get('monitor_enabled') and data.get('budget_brl') is None and data.get('budget_usd') is None:
            self.add_error(None,'Informe pelo menos um orçamento para ativar o monitoramento do cenário.')
        return data


class ReferenceForm(forms.ModelForm):
    price=MoneyDecimal(label='Preço de referência da sua tese',min_value=Decimal('.00000001'),max_digits=24,decimal_places=8)

    class Meta:
        model=ValueReference
        fields=['asset','currency','price','reference_date','source']
        labels={'asset':'Ativo','currency':'Moeda','reference_date':'Data da referência','source':'Fonte, estudo ou premissa utilizada'}
        widgets={'reference_date':forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}),'source':forms.TextInput(attrs={'placeholder':'Ex.: estudo de resultados, relatório e data'})}

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['asset'].queryset=Asset.objects.filter(is_active=True).exclude(market='CAMBIO').order_by('symbol','market')
        self.initial.setdefault('reference_date',timezone.localdate())
