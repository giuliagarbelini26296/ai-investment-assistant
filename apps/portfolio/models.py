"""
Carteira: ativos e lotes de compra, com trilha de revisões.

- Quantidades e preços em NUMERIC (Decimal). Quantidade fracionária permitida para cripto e
  ações dos EUA (Avenue oferece frações); ações da B3 exigem quantidade inteira.
- Lotes nunca são apagados: cancelamento lógico (`cancelado_em`), e toda alteração gera
  uma `LotRevision` com o estado anterior.
- O câmbio da compra é opcional. Quando não informado, a origem fica "pendente" até a
  resolução pelo serviço de câmbio (E5): PTAX da data → PTAX do dia útil anterior → sem base.
"""

from __future__ import annotations

from decimal import Decimal
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class PurchaseReceipt(models.Model):
    """Idempotency record for the guided purchase form; the Lot remains the ledger."""
    submission_key = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    lot = models.OneToOneField('Lot', on_delete=models.PROTECT, related_name='purchase_receipt')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()


class AssetClass(models.TextChoices):
    STOCK = "acao", "Ação"
    CRYPTO = "cripto", "Criptomoeda"
    FX = "cambio", "Câmbio"


class Market(models.TextChoices):
    NYSE = "NYSE", "NYSE"
    NASDAQ = "NASDAQ", "Nasdaq"
    B3 = "B3", "B3"
    CRYPTO = "CRIPTO", "Cripto (24/7)"
    FX = "CAMBIO", "Câmbio (USD/BRL)"


class Custody(models.TextChoices):
    AVENUE = "AVENUE", "Avenue"
    BTG = "BTG", "BTG Investimentos"
    BINANCE = "BINANCE", "Binance"
    OTHER = "OUTRA", "Outra"
    NONE = "NENHUMA", "— (só acompanhamento)"


class Currency(models.TextChoices):
    USD = "USD", "US$"
    BRL = "BRL", "R$"


class FxOrigin(models.TextChoices):
    INFORMED = "informado", "Informado pelo usuário"
    PTAX_DATE = "ptax_data", "PTAX da data da compra"
    PTAX_PREV = "ptax_dia_util_anterior", "PTAX do dia útil anterior"
    NO_BASIS = "sem_base", "Sem base cambial"
    PENDING = "pendente", "Pendente de resolução"
    NOT_APPLICABLE = "nao_aplicavel", "Não se aplica (ativo em BRL)"


MARKET_CURRENCY = {
    Market.NYSE: Currency.USD,
    Market.NASDAQ: Currency.USD,
    Market.B3: Currency.BRL,
    Market.CRYPTO: Currency.USD,
    Market.FX: Currency.BRL,
}
MARKET_CLASS = {
    Market.NYSE: AssetClass.STOCK,
    Market.NASDAQ: AssetClass.STOCK,
    Market.B3: AssetClass.STOCK,
    Market.CRYPTO: AssetClass.CRYPTO,
    Market.FX: AssetClass.FX,
}

# Pseudo-ativo do dólar comercial: recebe cotações do câmbio intradiário para alertas, gráficos e favoritos.
SIMBOLO_DOLAR = "USDBRL"


class Asset(models.Model):
    symbol = models.CharField("símbolo", max_length=20)
    name = models.CharField("nome", max_length=120)
    market = models.CharField("mercado", max_length=10, choices=Market.choices)
    asset_class = models.CharField("classe", max_length=10, choices=AssetClass.choices, editable=False)
    native_currency = models.CharField("moeda nativa", max_length=3, choices=Currency.choices, editable=False)
    custody = models.CharField("custódia", max_length=10, choices=Custody.choices)
    sector = models.CharField("setor", max_length=80, blank=True)
    source_symbol = models.CharField(
        "símbolo na fonte",
        max_length=20,
        blank=True,
        help_text="Opcional. Ex.: BTCUSDT na Binance. Vazio = derivado do símbolo.",
    )
    is_active = models.BooleanField("ativo", default=True)
    is_etf = models.BooleanField("é ETF", default=False)
    acompanhamento = models.BooleanField(
        "só acompanhamento",
        default=False,
        help_text=(
            "Ativo do universo do Mercado/Favoritos, sem compra. Ações dos EUA nessa condição são "
            "atualizadas por rodízio (~1 h) para caber no plano gratuito da fonte."
        ),
    )
    notes = models.TextField("observações", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "ativo"
        verbose_name_plural = "ativos"
        ordering = ["market", "symbol"]
        constraints = [
            models.UniqueConstraint(fields=["symbol", "market"], name="uniq_asset_symbol_market"),
        ]

    def __str__(self) -> str:
        return f"{self.symbol} ({self.get_market_display()})"

    def save(self, *args, **kwargs):
        self.symbol = (self.symbol or "").strip().upper()
        self.asset_class = MARKET_CLASS[self.market]
        self.native_currency = MARKET_CURRENCY[self.market]
        super().save(*args, **kwargs)

    def clean(self):
        self.symbol = (self.symbol or "").strip().upper()
        if not self.symbol:
            raise ValidationError({"symbol": "Informe o símbolo."})
        if self.market not in MARKET_CURRENCY:
            raise ValidationError({"market": "Mercado inválido."})
        self.asset_class = MARKET_CLASS[self.market]
        self.native_currency = MARKET_CURRENCY[self.market]

    @property
    def simbolo_na_fonte(self) -> str:
        if self.source_symbol:
            return self.source_symbol.upper()
        if self.market == Market.CRYPTO:
            return f"{self.symbol}USDT"
        return self.symbol

    @property
    def coleta_horaria(self) -> bool:
        """Ação dos EUA só acompanhada: preço vem do rodízio horário de candles, não do ciclo de 15 min."""
        return self.acompanhamento and self.market in (Market.NYSE, Market.NASDAQ)

    @property
    def tipo_rotulo(self) -> str:
        if self.market == Market.FX:
            return "Câmbio"
        if self.market == Market.CRYPTO:
            return "Cripto"
        return "ETF" if self.is_etf else "Ação"

    @property
    def requires_integer_quantity(self) -> bool:
        return self.market == Market.B3

    @property
    def active_lots(self):
        return self.lots.filter(cancelado_em__isnull=True)

    @property
    def position_balance(self):
        """Último saldo de conciliação; não é uma compra nem informa data de aquisição."""
        return next(iter(self.position_balances.all()), None)

    @property
    def has_position(self):
        return self.position_balance is not None or self.active_lots.exists()


class PositionBalance(models.Model):
    """Saldo comprovado, separado do ledger. Lotes abrangidos ficam intactos no histórico.

    Uma nova conciliação cria outro registro; não sobrescreve a comprovação anterior.
    A data é a de referência do saldo, nunca uma data de compra presumida.
    """

    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="position_balances")
    reference_date = models.DateField("data de referência do saldo")
    quantity = models.DecimalField(max_digits=28, decimal_places=10)
    average_price = models.DecimalField(max_digits=20, decimal_places=8)
    invested_cost = models.DecimalField("custo investido comprovado (opcional)", max_digits=28,
        decimal_places=8, null=True, blank=True,
        help_text="Valor do campo Investido do extrato, preservado sem reconstrução pelo preço médio arredondado.")
    source_key = models.CharField(max_length=120)
    evidence = models.TextField()
    covered_lots = models.JSONField(default=list, blank=True)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-recorded_at", "-pk"]
        default_permissions = ()
        constraints = [
            models.UniqueConstraint(fields=["asset", "source_key"], name="uniq_asset_balance_source"),
            models.CheckConstraint(condition=models.Q(quantity__gte=0), name="balance_quantity_positive"),
            models.CheckConstraint(condition=models.Q(average_price__gt=0), name="balance_price_positive"),
            models.CheckConstraint(condition=models.Q(invested_cost__isnull=True) | models.Q(invested_cost__gte=0),
                name="balance_invested_cost_positive"),
        ]

    def __str__(self):
        return f"Saldo {self.asset.symbol}: {self.quantity} em {self.reference_date}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Saldo imutável: registre outra conciliação para manter a trilha.")
        super().save(*args, **kwargs)

    @property
    def covered_lot_ids(self):
        return {item["id"] for item in self.covered_lots}

    def clean(self):
        errors = {}
        if self.reference_date and self.reference_date > timezone.localdate():
            errors["reference_date"] = "A data de referência não pode ser futura."
        if self.quantity is not None and self.quantity < 0:
            errors["quantity"] = "A quantidade não pode ser negativa."
        if self.average_price is not None and self.average_price <= 0:
            errors["average_price"] = "O preço médio deve ser positivo."
        if self.invested_cost is not None:
            if not isinstance(self.invested_cost, Decimal) or not self.invested_cost.is_finite() or self.invested_cost < 0:
                errors["invested_cost"] = "O custo investido deve ser positivo e finito."
            elif self.quantity and self.average_price and self.quantity > 0 and self.average_price > 0:
                # Both displayed unit average and total may have cent rounding in a broker statement.
                tolerance = self.quantity * Decimal("0.005") + Decimal("0.005")
                if abs(self.invested_cost - self.quantity * self.average_price) > tolerance:
                    errors["invested_cost"] = "O custo diverge do preço médio além do arredondamento do extrato. Confira a fonte."
        if self.asset_id and self.quantity is not None and self.asset.requires_integer_quantity:
            if self.quantity != self.quantity.to_integral_value():
                errors["quantity"] = "Ações da B3 exigem quantidade inteira."
        if self.quantity == 0 and self.invested_cost != 0:
            errors["invested_cost"] = "Uma posição encerrada deve ter custo zero."
        if errors:
            raise ValidationError(errors)


class Lot(models.Model):
    """Uma compra (lote) de um ativo."""

    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="lots", verbose_name="ativo")
    trade_date = models.DateField("data da compra")
    quantity = models.DecimalField(
        "quantidade",
        max_digits=28,
        decimal_places=10,
        validators=[MinValueValidator(Decimal("0.0000000001"))],
    )
    unit_price = models.DecimalField(
        "preço unitário (moeda nativa)",
        max_digits=20,
        decimal_places=8,
        validators=[MinValueValidator(Decimal("0.00000001"))],
    )
    fx_rate = models.DecimalField(
        "câmbio USD/BRL na compra", max_digits=12, decimal_places=6, null=True, blank=True
    )
    fx_origin = models.CharField(
        "origem do câmbio", max_length=24, choices=FxOrigin.choices, default=FxOrigin.PENDING
    )
    fx_resolved = models.DecimalField(
        "câmbio USD/BRL resolvido", max_digits=12, decimal_places=6, null=True, blank=True, editable=False
    )
    costs = models.DecimalField(
        "custos (opcional, não entram no cálculo do MVP)",
        max_digits=20,
        decimal_places=8,
        null=True,
        blank=True,
    )
    notes = models.CharField("observação", max_length=200, blank=True)
    revision = models.PositiveIntegerField("revisão", default=1, editable=False)
    cancelado_em = models.DateTimeField("cancelado em", null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "compra"
        verbose_name_plural = "compras"
        ordering = ["trade_date", "id"]
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="lot_quantity_positive"),
            models.CheckConstraint(condition=models.Q(unit_price__gt=0), name="lot_price_positive"),
            models.CheckConstraint(
                condition=models.Q(fx_rate__isnull=True) | models.Q(fx_rate__gt=0), name="lot_fx_positive"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.asset.symbol} {self.quantity} @ {self.unit_price} em {self.trade_date}"

    @property
    def is_cancelled(self) -> bool:
        return self.cancelado_em is not None

    @property
    def invested(self) -> Decimal:
        return self.quantity * self.unit_price

    def clean(self):
        errors = {}
        if self.trade_date and self.trade_date > timezone.localdate():
            errors["trade_date"] = "A data da compra não pode ser futura."
        if self.quantity is not None and self.quantity < 0:
            errors["quantity"] = "A quantidade não pode ser negativa."
        if self.unit_price is not None and self.unit_price <= 0:
            errors["unit_price"] = "O preço deve ser positivo."
        if self.fx_rate is not None and self.fx_rate <= 0:
            errors["fx_rate"] = "O câmbio deve ser positivo."
        asset = getattr(self, "asset", None)
        if asset is not None and self.quantity is not None and asset.requires_integer_quantity:
            if self.quantity != self.quantity.to_integral_value():
                errors["quantity"] = "Ações da B3 exigem quantidade inteira."
        if asset is not None and self.fx_rate is not None and asset.native_currency == Currency.BRL:
            errors["fx_rate"] = "Câmbio de compra só se aplica a ativos cotados em USD."
        if errors:
            raise ValidationError(errors)
        if asset is not None and asset.native_currency == Currency.BRL:
            self.fx_origin, self.fx_resolved = FxOrigin.NOT_APPLICABLE, None
        elif self.fx_rate is not None:
            self.fx_origin, self.fx_resolved = FxOrigin.INFORMED, self.fx_rate
        else:
            self.fx_origin, self.fx_resolved = FxOrigin.PENDING, None  # resolvido pelo serviço de câmbio

    def snapshot(self) -> dict:
        return {
            "trade_date": self.trade_date.isoformat() if self.trade_date else None,
            "quantity": str(self.quantity),
            "unit_price": str(self.unit_price),
            "fx_rate": None if self.fx_rate is None else str(self.fx_rate),
            "fx_origin": self.fx_origin,
            "fx_resolved": None if self.fx_resolved is None else str(self.fx_resolved),
            "costs": None if self.costs is None else str(self.costs),
            "notes": self.notes,
            "cancelado_em": self.cancelado_em.isoformat() if self.cancelado_em else None,
        }


class LotRevision(models.Model):
    """Estado anterior de um lote antes de cada alteração ou cancelamento."""

    lot = models.ForeignKey(Lot, on_delete=models.CASCADE, related_name="revisions")
    number = models.PositiveIntegerField("revisão substituída")
    previous = models.JSONField("estado anterior")
    reason = models.CharField("motivo", max_length=200, blank=True)
    changed_at = models.DateTimeField(auto_now_add=True)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["lot", "number"]
        constraints = [models.UniqueConstraint(fields=["lot", "number"], name="uniq_lot_revision")]

    def __str__(self) -> str:
        return f"rev {self.number} de {self.lot_id}"


class Sale(models.Model):
    """Immutable record of an already executed sale; never a brokerage order."""
    submission_key = models.UUIDField(unique=True, default=uuid.uuid4)
    account = models.CharField(max_length=10, choices=[('ana','Ana'),('bruno','Bruno')])
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name='recorded_sales')
    trade_date = models.DateField()
    quantity = models.DecimalField(max_digits=28, decimal_places=10)
    unit_price = models.DecimalField(max_digits=24, decimal_places=8)
    currency = models.CharField(max_length=3)
    fees = models.DecimalField(max_digits=28, decimal_places=8, null=True, blank=True)
    allocated_cost = models.DecimalField(max_digits=28, decimal_places=8)
    before_quantity = models.DecimalField(max_digits=28, decimal_places=10)
    before_cost = models.DecimalField(max_digits=28, decimal_places=8)
    evidence = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        ordering = ['-created_at','-pk']
        constraints = [models.CheckConstraint(condition=models.Q(quantity__gt=0, unit_price__gt=0, allocated_cost__gte=0), name='sale_values_positive'),
            models.CheckConstraint(condition=models.Q(fees__isnull=True)|models.Q(fees__gte=0), name='sale_fees_nonnegative')]

    def save(self,*args,**kwargs):
        if not self._state.adding:
            raise ValidationError('Venda preservada no histórico. Não sobrescreva a execução.')
        super().save(*args,**kwargs)

    @property
    def gross_proceeds(self):
        return self.quantity*self.unit_price

    @property
    def gross_gain(self):
        return self.gross_proceeds-self.allocated_cost

    @property
    def net_gain(self):
        # Income tax is not inferred from an individual sale.
        return self.gross_gain-self.fees if self.fees is not None else None
