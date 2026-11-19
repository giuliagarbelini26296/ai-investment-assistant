"""Additional portfolios share market assets, never positions or purchase history."""
import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class Portfolio(models.Model):
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=80)
    legacy = models.BooleanField(default=False)

    class Meta:
        default_permissions = ()

    def __str__(self):
        return self.name


class ImmutableRecord(models.Model):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('Registro preservado: faça uma nova conciliação para ajustar o saldo.')
        super().save(*args, **kwargs)


class Balance(ImmutableRecord):
    portfolio = models.ForeignKey(Portfolio, on_delete=models.PROTECT, related_name='balances')
    asset = models.ForeignKey('portfolio.Asset', on_delete=models.PROTECT, related_name='managed_balances')
    currency = models.CharField(max_length=3, choices=[('BRL', 'BRL'), ('USD', 'USD')])
    quantity = models.DecimalField(max_digits=28, decimal_places=10)
    reported_average = models.DecimalField(max_digits=24, decimal_places=8)
    invested_cost = models.DecimalField(max_digits=28, decimal_places=8)
    reference_date = models.DateField()
    source_key = models.CharField(max_length=120)
    evidence = models.TextField()
    covered_purchases = models.JSONField(default=list, blank=True)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        ordering = ['-recorded_at', '-pk']
        constraints = [
            models.UniqueConstraint(fields=['portfolio', 'asset', 'source_key'], name='uniq_managed_balance_source'),
            models.CheckConstraint(condition=models.Q(quantity__gte=0, reported_average__gt=0, invested_cost__gte=0), name='managed_balance_positive'),
        ]

    def clean(self):
        validate_position(self)
        if (self.quantity == 0) != (self.invested_cost == 0):
            raise ValidationError("Posição encerrada exige quantidade e custo zero.")
        if self.reference_date and self.reference_date > timezone.localdate():
            raise ValidationError('A data de referência não pode ser futura.')
        if all(isinstance(v, Decimal) and v.is_finite() and v > 0
               for v in (self.quantity, self.reported_average, self.invested_cost)):
            if abs(self.quantity * self.reported_average - self.invested_cost) > self.quantity * Decimal('.005') + Decimal('.005'):
                raise ValidationError('O custo não corresponde ao preço médio informado. Confira o extrato.')


class Purchase(ImmutableRecord):
    submission_key = models.UUIDField(unique=True, default=uuid.uuid4)
    portfolio = models.ForeignKey(Portfolio, on_delete=models.PROTECT, related_name='purchases')
    asset = models.ForeignKey('portfolio.Asset', on_delete=models.PROTECT, related_name='managed_purchases')
    currency = models.CharField(max_length=3, choices=[('BRL', 'BRL'), ('USD', 'USD')])
    trade_date = models.DateField()
    quantity = models.DecimalField(max_digits=28, decimal_places=10)
    unit_price = models.DecimalField(max_digits=24, decimal_places=8)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        constraints = [models.CheckConstraint(condition=models.Q(quantity__gt=0, unit_price__gt=0), name='managed_purchase_positive')]

    def clean(self):
        validate_position(self)
        if self.trade_date and self.trade_date > timezone.localdate():
            raise ValidationError('A data da compra não pode ser futura.')


def validate_position(record):
    if record.portfolio_id and record.portfolio.legacy:
        raise ValidationError('A carteira Ana usa seus registros originais de posição e compras.')
    if record.asset_id:
        if record.asset.market == 'CAMBIO':
            raise ValidationError('Câmbio é referência de mercado, não uma posição desta carteira.')
        if record.asset.market == 'B3' and record.currency != 'BRL':
            raise ValidationError('Ativos e BDRs da B3 são registrados em reais.')
        if record.asset.market in ('NYSE', 'NASDAQ') and record.currency != 'USD':
            raise ValidationError('Ações dos EUA são registradas em dólares.')
        if record.quantity is not None and record.asset.requires_integer_quantity and record.quantity != record.quantity.to_integral_value():
            raise ValidationError('A B3 exige quantidade inteira.')


class CurrencyQuote(models.Model):
    """Direct BRL crypto prices; kept away from the original USD/USDT quote stream."""
    asset = models.ForeignKey('portfolio.Asset', on_delete=models.PROTECT)
    currency = models.CharField(max_length=3)
    price = models.DecimalField(max_digits=24, decimal_places=8)
    previous_close = models.DecimalField(max_digits=24, decimal_places=8, null=True)
    change_24h_pct = models.DecimalField(max_digits=20, decimal_places=8, null=True)
    market_time = models.DateTimeField(null=True)
    collected_at = models.DateTimeField()
    source = models.CharField(max_length=50)

    class Meta:
        default_permissions = ()
        ordering = ['-collected_at', '-pk']
        constraints = [models.UniqueConstraint(fields=['asset', 'currency', 'market_time'], name='uniq_managed_currency_quote')]

    @property
    def raw(self):
        return {}


class CurrencyBar(models.Model):
    asset = models.ForeignKey('portfolio.Asset',on_delete=models.PROTECT)
    currency = models.CharField(max_length=3)
    date = models.DateField()
    close = models.DecimalField(max_digits=24,decimal_places=8)
    source = models.CharField(max_length=30)
    collected_at = models.DateTimeField()

    class Meta:
        default_permissions=()
        ordering=['date']
        constraints=[models.UniqueConstraint(fields=['asset','currency','date','source'],name='unique_currency_bar'),
            models.CheckConstraint(condition=models.Q(close__gt=0),name='currency_bar_positive')]
