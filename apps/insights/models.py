"""Planning records never change purchases, balances or existing permissions."""
import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


class RiskPreference(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    asset_limit = models.DecimalField('Limite por empresa (%)', max_digits=5, decimal_places=2,
        default=35, validators=[MinValueValidator(Decimal('0.01')), MaxValueValidator(100)])
    sector_limit = models.DecimalField('Limite por setor (%)', max_digits=5, decimal_places=2,
        default=60, validators=[MinValueValidator(Decimal('0.01')), MaxValueValidator(100)])
    country_limit = models.DecimalField('Limite por país (%)', max_digits=5, decimal_places=2,
        default=80, validators=[MinValueValidator(Decimal('0.01')), MaxValueValidator(100)])

    class Meta:
        default_permissions = ()


class DecisionPreference(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    account = models.CharField(max_length=8, choices=[('ana','Ana'),('bruno','Bruno')])
    profile = models.CharField(max_length=14, choices=[('conservador','Conservador'),('equilibrado','Equilibrado'),('arrojado','Arrojado')])
    horizon = models.CharField(max_length=12, choices=[('tatico','Tático · semanas a meses'),('longo','Longo prazo · 3 anos ou mais')])
    budget_brl = models.DecimalField(max_digits=24, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    budget_usd = models.DecimalField(max_digits=24, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    asset_limit = models.DecimalField(max_digits=5, decimal_places=2, default=25, validators=[MinValueValidator(1),MaxValueValidator(100)])
    plan_risk = models.DecimalField(max_digits=5, decimal_places=2, default=2, validators=[MinValueValidator(Decimal('.01')),MaxValueValidator(10)])
    stop_distance = models.DecimalField(max_digits=5, decimal_places=2, default=15, validators=[MinValueValidator(1),MaxValueValidator(80)])
    entry_tolerance = models.DecimalField(max_digits=5, decimal_places=2, default=2, validators=[MinValueValidator(0),MaxValueValidator(20)])
    safety_margin = models.DecimalField(max_digits=5, decimal_places=2, default=20, validators=[MinValueValidator(0),MaxValueValidator(80)])
    monitor_enabled = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()
        constraints=[models.UniqueConstraint(fields=['user','account'],name='decision_preference_user_account')]


class ValueReference(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    account = models.CharField(max_length=8, choices=[('ana','Ana'),('bruno','Bruno')])
    asset = models.ForeignKey('portfolio.Asset', on_delete=models.PROTECT)
    currency = models.CharField(max_length=3,choices=[('BRL','Reais'),('USD','Dólares')])
    price = models.DecimalField(max_digits=24,decimal_places=8,validators=[MinValueValidator(Decimal('.00000001'))])
    reference_date = models.DateField()
    source = models.CharField(max_length=300)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()
        constraints=[models.UniqueConstraint(fields=['user','account','asset'],name='decision_reference_user_asset')]

    def clean(self):
        if self.reference_date and self.reference_date>timezone.localdate():
            raise ValidationError({'reference_date':'A referência não pode ter data futura.'})
        if self.asset_id:
            expected='BRL' if self.asset.market in ('B3','CRIPTO') else 'USD'
            if self.currency!=expected:
                raise ValidationError({'currency':'Use a moeda da análise: '+expected+'.'})


class DecisionNotice(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE)
    account = models.CharField(max_length=8)
    asset = models.ForeignKey('portfolio.Asset',on_delete=models.PROTECT)
    signal = models.CharField(max_length=20)
    session_date = models.DateField()
    title = models.CharField(max_length=100)
    detail = models.TextField()
    price = models.DecimalField(max_digits=24,decimal_places=8)
    currency = models.CharField(max_length=3)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions=()
        ordering=['-created_at']
        constraints=[models.UniqueConstraint(fields=['user','account','asset','signal','session_date'],name='decision_notice_daily_unique')]


class CashEntry(models.Model):
    class Kind(models.TextChoices):
        DIVIDEND = 'dividend', 'Dividendos recebidos'
        JCP = 'jcp', 'JCP recebido'
        FEE = 'fee', 'Taxas pagas'
        TAX = 'tax', 'Impostos pagos'
        PROFIT = 'profit', 'Lucro realizado informado'
        LOSS = 'loss', 'Prejuízo realizado informado'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset = models.ForeignKey('portfolio.Asset', null=True, blank=True, on_delete=models.PROTECT)
    currency = models.CharField('Moeda', max_length=3, choices=[('BRL', 'BRL · reais'), ('USD', 'USD · dólares')])
    kind = models.CharField('Tipo', max_length=12, choices=Kind.choices)
    date = models.DateField('Data efetiva')
    amount = models.DecimalField('Valor total', max_digits=24, decimal_places=8,
        validators=[MinValueValidator(Decimal('0.00000001'))])
    reference = models.CharField('Referência do extrato / observação', max_length=200)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+')

    class Meta:
        ordering = ['-date', '-created_at']
        default_permissions = ()
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0), name='insight_cash_positive')]

    def clean(self):
        if self.date and self.date > timezone.localdate():
            raise ValidationError({'date': 'Registre apenas valores efetivamente recebidos ou pagos.'})
        if self.asset_id and self.currency != self.asset.native_currency:
            raise ValidationError({'currency': 'Use a moeda original do ativo.'})
        if not (self.reference or '').strip():
            raise ValidationError({'reference': 'Identifique o lançamento no seu extrato.'})

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('Lançamento imutável: cancele e registre a correção para preservar o histórico.')
        super().save(*args, **kwargs)


class BenchmarkBar(models.Model):
    symbol = models.CharField(max_length=12, choices=[('BOVA11', 'BOVA11 · referência Ibovespa'), ('SPY', 'SPY · referência S&P 500')])
    date = models.DateField()
    close = models.DecimalField(max_digits=24, decimal_places=8)
    source = models.CharField(max_length=30)
    collected_at = models.DateTimeField()

    class Meta:
        default_permissions = ()
        ordering = ['date']
        constraints = [models.UniqueConstraint(fields=['symbol', 'date'], name='insight_benchmark_day'),
            models.CheckConstraint(condition=models.Q(close__gt=0), name='insight_benchmark_positive')]


class BenchmarkState(models.Model):
    symbol = models.CharField(max_length=12, primary_key=True)
    last_attempt = models.DateTimeField(null=True)
    last_success = models.DateTimeField(null=True)
    next_attempt = models.DateTimeField(null=True)
    message = models.CharField(max_length=200, blank=True)

    class Meta:
        default_permissions = ()


class CalendarReference(models.Model):
    """Explicit published calendars supplement the provider's estimated earnings dates."""
    asset = models.ForeignKey('portfolio.Asset', on_delete=models.PROTECT)
    date = models.DateField()
    title = models.CharField(max_length=120)
    timing = models.CharField(max_length=60, blank=True)
    source_url = models.URLField(max_length=500)
    verified_at = models.DateTimeField()

    class Meta:
        default_permissions = ()
        ordering = ['date', 'asset__symbol']
        constraints = [models.UniqueConstraint(fields=['asset', 'date', 'title'], name='insight_calendar_unique')]
class AssistantUsage(models.Model):
    """Atomic per-user daily turn reservation; no prompts or credentials stored here."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE)
    date = models.DateField()
    turns = models.PositiveIntegerField(default=0)

    class Meta:
        default_permissions=()
        constraints=[models.UniqueConstraint(fields=['user','date'],name='unique_assistant_daily_usage')]


class LiveFeedState(models.Model):
    source = models.CharField(max_length=30,unique=True)
    last_success = models.DateTimeField(null=True)
    last_attempt = models.DateTimeField(null=True)
    error = models.CharField(max_length=180,blank=True)
    symbols = models.JSONField(default=list)

    class Meta:
        default_permissions=()


class TechnicalWatch(models.Model):
    """Opt-in shared market signal for explicitly selected owners; no cash allocation."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE)
    asset = models.ForeignKey('portfolio.Asset',on_delete=models.PROTECT)
    ana = models.BooleanField(default=True)
    bruno = models.BooleanField(default=True)
    active = models.BooleanField(default=True)
    last_eligible = models.BooleanField(null=True)
    last_status = models.CharField(max_length=24,blank=True)
    last_checked = models.DateTimeField(null=True)
    last_notice_at = models.DateTimeField(null=True)
    detail = models.TextField(blank=True)

    class Meta:
        default_permissions=()
        constraints=[models.UniqueConstraint(fields=['user','asset'],name='unique_technical_watch')]
