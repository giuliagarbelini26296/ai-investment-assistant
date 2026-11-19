from django import forms

from .models import Asset, Lot


class AssetForm(forms.ModelForm):
    class Meta:
        model = Asset
        fields = [
            "symbol",
            "name",
            "market",
            "custody",
            "sector",
            "is_etf",
            "source_symbol",
            "is_active",
            "acompanhamento",
            "notes",
        ]
        widgets = {"notes": forms.Textarea(attrs={"rows": 2})}
        help_texts = {
            "symbol": "Ex.: TTWO, BTC, PETR4. Classe e moeda nativa derivam do mercado.",
            "market": "NYSE/Nasdaq → USD · B3 → BRL · Cripto → USD (par USDT/USD da exchange).",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # o dólar comercial é um pseudo-ativo criado pelo portal; não se cadastra à mão
        self.fields["market"].choices = [c for c in self.fields["market"].choices if c[0] != "CAMBIO"]


FAIXA_CAMBIO = (1, 15)  # USD/BRL plausível para checagem de digitação
DIVERGENCIA_MAXIMA = 3  # preço da compra até 3× (ou 1/3) do fechamento do dia, sem confirmação


class LotForm(forms.ModelForm):
    confirmar_valores = forms.BooleanField(
        label="Confirmo preço e câmbio mesmo fora do esperado",
        required=False,
        help_text="Marque só se os valores acima estiverem corretos (ex.: desdobramento ou ativo diferente).",
    )

    class Meta:
        model = Lot
        fields = ["trade_date", "quantity", "unit_price", "fx_rate", "costs", "notes"]
        widgets = {"trade_date": forms.DateInput(attrs={"type": "date"})}
        help_texts = {
            "quantity": "Fracionária para cripto e ações dos EUA; inteira para B3.",
            "unit_price": "Na moeda nativa do ativo (US$ para EUA/cripto, R$ para B3).",
            "fx_rate": (
                "Opcional. Câmbio efetivo da remessa (ex.: Avenue). "
                "Se vazio, o portal usará o PTAX da data (E5)."
            ),
        }

    def __init__(self, *args, asset=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.asset = asset or getattr(self.instance, "asset", None)
        # números em pt-BR: "76.590" = setenta e seis mil; "5,42" = cinco vírgula quarenta e dois
        for nome in ("quantity", "unit_price", "fx_rate", "costs"):
            if nome not in self.fields:
                continue
            original = self.fields[nome]
            self.fields[nome] = forms.CharField(
                label=original.label,
                required=original.required,
                help_text=original.help_text,
                widget=forms.TextInput(attrs={"inputmode": "decimal", "autocomplete": "off"}),
            )
            valor = getattr(self.instance, nome, None) if self.instance.pk else None
            if valor is not None:
                self.initial[nome] = f"{valor.normalize():f}".replace(".", ",")
        if self.asset is not None and self.asset.native_currency == "BRL":
            self.fields.pop("fx_rate", None)

    def _numero(self, nome: str, quantidade: bool = False):
        from decimal import InvalidOperation

        from apps.alerts.forms import interpretar_numero

        bruto = (self.data.get(self.add_prefix(nome)) or "").strip()
        if not bruto:
            return None
        texto = bruto
        if quantidade and "," not in texto and texto.count(".") == 1:
            inteira = self.asset is not None and self.asset.requires_integer_quantity
            if not inteira:  # cripto/EUA: "0.125" e "1.125" são frações, não milhares
                texto = texto.replace(".", ",")
        try:
            return interpretar_numero(texto)
        except (InvalidOperation, ValueError) as exc:
            raise forms.ValidationError("Informe um número, ex.: 76.590,50 ou 5,42.") from exc

    def clean_quantity(self):
        return self._numero("quantity", quantidade=True)

    def clean_unit_price(self):
        return self._numero("unit_price")

    def clean_fx_rate(self):
        return self._numero("fx_rate")

    def clean_costs(self):
        return self._numero("costs")

    def clean(self):
        cleaned = super().clean()
        if self.asset is not None:
            self.instance.asset = self.asset
        if not cleaned.get("confirmar_valores"):
            self._checar_plausibilidade(cleaned)
        return cleaned

    def _checar_plausibilidade(self, cleaned) -> None:
        from .conferencia import conferir

        if self.asset is None:
            return
        for campo, mensagem in conferir(
            self.asset, cleaned.get("unit_price"), cleaned.get("trade_date"), cleaned.get("fx_rate")
        ).items():
            self.add_error(campo, mensagem + " Confirme abaixo apenas se os valores estiverem corretos.")
