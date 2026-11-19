"""
Formulário de alerta em linguagem simples.

O usuário monta uma frase — "Me avise quando [ativo] [cair 5 % no dia]" — e o formulário traduz para
os parâmetros do motor de regras (família, condição, limite, moeda). Nome e classificação são opcionais:
o nome é gerado a partir da frase e a classificação padrão é "apenas aviso" (o sistema nunca infere
compra/venda/manutenção).
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from django import forms

from apps.portfolio.models import Asset
from apps.ui.templatetags.formatos import normalize_decimal

from .models import Classification, Condition, Family

# tipo → (família, condição). A ordem define a ordem no seletor.
TIPOS = {
    "queda_dia": ("cair X % no dia (24 h)", Family.DAY, Condition.DROP),
    "alta_dia": ("subir X % no dia (24 h)", Family.DAY, Condition.RISE),
    "queda_compra": ("cair X % em relação ao que paguei", Family.SINCE_PURCHASE, Condition.DROP),
    "alta_compra": ("subir X % em relação ao que paguei", Family.SINCE_PURCHASE, Condition.RISE),
    "abaixo": ("ficar abaixo de um preço", Family.ABSOLUTE, Condition.BELOW),
    "acima": ("ficar acima de um preço", Family.ABSOLUTE, Condition.ABOVE),
    "perto_min52": ("ficar a até X % da mínima de 52 semanas", Family.MARKET, Condition.NEAR_LOW),
    "rompe_suporte": ("romper o suporte (mínima de X sessões)", Family.MARKET, Condition.SUPPORT),
    "rompe_resist": ("romper a resistência (máxima de X sessões)", Family.MARKET, Condition.RESISTANCE),
    "resultado": ("divulgar resultado em até X dias", Family.MARKET, Condition.EARNINGS),
    "recomendacao": ("mudar a recomendação dos analistas em X p.p.", Family.MARKET, Condition.RATING),
}
# unidade exibida ao lado do valor, por tipo
UNIDADE_TIPO = {
    "perto_min52": "%",
    "rompe_suporte": "sessões",
    "rompe_resist": "sessões",
    "resultado": "dias",
    "recomendacao": "p.p.",
}
TIPO_POR_PARAMETROS = {(fam, cond): tipo for tipo, (_, fam, cond) in TIPOS.items()}

# Atalhos exibidos como botões; preenchem tipo, valor e (opcionalmente) o ativo pelo símbolo.
ATALHOS = (
    ("Queda de 5% no dia", "queda_dia", "5", ""),
    ("Alta de 5% no dia", "alta_dia", "5", ""),
    ("Queda de 10% desde a compra", "queda_compra", "10", ""),
    ("Alta de 20% desde a compra", "alta_compra", "20", ""),
    ("Bitcoin abaixo de US$ 77.500", "abaixo", "77.500", "BTC"),
    ("Dólar subir 1% no dia", "alta_dia", "1", "USDBRL"),
    ("Dólar cair 1% no dia", "queda_dia", "1", "USDBRL"),
    ("Perto da mínima de 52 semanas", "perto_min52", "5", ""),
    ("Rompe suporte de 20 sessões", "rompe_suporte", "20", ""),
    ("Resultado em 7 dias", "resultado", "7", ""),
)


def interpretar_numero(texto: str) -> Decimal:
    """Aceita '5', '5,5', '5.5', '100.000,00', '100,000.00', 'US$ 100.000' e '0.125'."""
    s = re.sub(r"[^\d,.\-]", "", texto or "")
    if not s:
        raise InvalidOperation
    if "," in s and "." in s:
        # o último separador é o decimal
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".") if s.count(",") == 1 else s.replace(",", "")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    elif "." in s:
        # convenção pt-BR: um ponto seguido de exatamente 3 dígitos é separador de milhar
        # ("100.000" = cem mil), exceto quando a parte inteira é 0 ("0.125").
        inteiro, _, frac = s.partition(".")
        if len(frac) == 3 and inteiro.lstrip("-") not in ("", "0"):
            s = inteiro + frac
    return Decimal(s)


class AlertaForm(forms.Form):
    asset = forms.ModelChoiceField(
        label="Ativo",
        queryset=Asset.objects.filter(is_active=True).order_by("-acompanhamento", "symbol"),
        required=False,
        empty_label="qualquer ativo da minha carteira",
    )
    tipo = forms.ChoiceField(label="Quando", choices=[(k, v[0]) for k, v in TIPOS.items()])
    valor = forms.CharField(
        label="Valor (X % ou preço)",
        max_length=32,
        widget=forms.TextInput(attrs={"inputmode": "decimal", "autocomplete": "off", "size": 10}),
    )
    currency = forms.ChoiceField(
        label="Moeda do preço", choices=[("USD", "US$"), ("BRL", "R$")], required=False, initial="USD"
    )
    name = forms.CharField(label="Nome (opcional)", max_length=80, required=False)
    classification = forms.ChoiceField(
        label="Rótulo (opcional)", choices=Classification.choices, initial=Classification.NOTICE
    )
    reason = forms.CharField(label="Anotação (opcional)", max_length=200, required=False)

    def clean_valor(self):
        try:
            v = interpretar_numero(self.cleaned_data["valor"])
        except (InvalidOperation, ValueError) as exc:
            raise forms.ValidationError("Informe um número, ex.: 5 ou 5,5.") from exc
        if v <= 0:
            raise forms.ValidationError("O valor deve ser positivo.")
        if v.as_tuple().exponent < -8 or len(v.as_tuple().digits) > 20:
            raise forms.ValidationError("Use até 8 casas decimais.")
        return v

    def clean(self):
        d = super().clean()
        tipo = d.get("tipo")
        if tipo in TIPOS:
            fam = TIPOS[tipo][1]
            if fam == Family.ABSOLUTE and d.get("currency") not in ("USD", "BRL"):
                self.add_error("currency", "Informe a moeda do preço (US$ ou R$).")
            if fam == Family.MARKET:
                from .models import LIMITES_MERCADO

                cond = TIPOS[tipo][2]
                if not d.get("asset"):
                    self.add_error(
                        "asset", "Sinais de mercado valem para um ativo específico: escolha o ativo."
                    )
                v = d.get("valor")
                if v is not None:
                    minimo, maximo, inteiro = LIMITES_MERCADO[cond]
                    if not (minimo <= v <= maximo):
                        self.add_error(
                            "valor",
                            f"Use um valor entre {normalize_decimal(minimo)} e {normalize_decimal(maximo)}.",
                        )
                    elif inteiro and v != v.to_integral_value():
                        self.add_error("valor", "Use um número inteiro (sessões ou dias).")
            if (
                fam != Family.ABSOLUTE
                and d.get("valor") is not None
                and d["valor"] > 100
                and tipo.startswith("queda")
            ):
                self.add_error("valor", "Uma queda não pode passar de 100%.")
        return d

    def parametros(self) -> dict:
        d = self.cleaned_data
        _, fam, cond = TIPOS[d["tipo"]]
        return {
            "family": fam,
            "condition": cond,
            "threshold": d["valor"],
            "currency": d.get("currency") or "" if fam == Family.ABSOLUTE else "",
            "classification": d["classification"],
        }

    def nome(self) -> str:
        d = self.cleaned_data
        if d.get("name"):
            return d["name"]
        alvo = d["asset"].symbol if d.get("asset") else "Carteira"
        _, fam, cond = TIPOS[d["tipo"]]
        x = normalize_decimal(d["valor"])
        if fam == Family.ABSOLUTE:
            simbolo = {"USD": "US$", "BRL": "R$"}[d["currency"]]
            return f"{alvo}: {'abaixo' if cond == Condition.BELOW else 'acima'} de {simbolo} {x}"[:80]
        if fam == Family.MARKET:
            from .models import RuleVersion

            return f"{alvo}: {RuleVersion(family=fam, condition=cond, threshold=d['valor']).descricao}"[:80]
        verbo = "queda" if cond == Condition.DROP else "alta"
        onde = "no dia" if fam == Family.DAY else "desde a compra"
        return f"{alvo}: {verbo} de {x}% {onde}"[:80]

    @staticmethod
    def inicial_de_versao(rule, versao) -> dict:
        return {
            "asset": rule.asset,
            "tipo": TIPO_POR_PARAMETROS[(versao.family, versao.condition)],
            "valor": f"{versao.threshold.normalize():f}".replace(".", ","),
            "currency": versao.currency or "USD",
            "name": rule.name,
            "classification": versao.classification,
        }
