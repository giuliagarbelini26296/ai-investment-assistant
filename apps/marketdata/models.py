"""
Dados de mercado persistidos: cotações, candles diários, câmbio, orçamento de fontes e execuções.

Princípios (escopo v1.3 / proposta v1.1):
- Toda cotação carrega fonte, tipo (tempo real / atrasado / fechamento / spot / não declarado),
  horário de mercado (quando a fonte informa) e horário de coleta.
- Nada é estimado: um registro só existe se a fonte devolveu o valor.
- Histórico diário é sempre de uma única fonte por registro, nunca mesclado.
"""

from __future__ import annotations

from django.db import models


class QuoteType(models.TextChoices):
    REAL_TIME = "tempo_real", "Tempo real (declarado pela fonte)"
    DELAYED = "atrasado", "Atrasado (declarado pela fonte)"
    CLOSE = "fechamento", "Fechamento"
    SPOT = "spot", "Spot (exchange)"
    UNDECLARED = "nao_declarado", "Atraso não declarado pela fonte"


class QuoteCurrency(models.TextChoices):
    USD = "USD", "USD"
    USDT = "USDT", "USDT"
    BRL = "BRL", "BRL"


class TimeBasis(models.TextChoices):
    LOCAL_SESSION = "sessao_local", "Sessão local da bolsa"
    UTC_DAY = "dia_utc", "Dia UTC (cripto)"


class FxRateType(models.TextChoices):
    PTAX_CLOSE = "ptax_fechamento", "PTAX fechamento (BCB)"
    PTAX_BULLETIN = "ptax_boletim", "PTAX boletim intradiário (BCB)"
    INTRADAY = "intradiario", "Intradiário (provedor)"
    REFERENCE = "referencia", "Referência diária (provedor)"


class RecordStatus(models.TextChoices):
    VALID = "valida", "Válida"
    DISCARDED = "descartada", "Descartada"


class CollectionRun(models.Model):
    """Uma execução do coletor para uma fonte/mercado."""

    class Status(models.TextChoices):
        COMPLETE = "completo", "Completo"
        PARTIAL = "parcial", "Parcial"
        FAILED = "falha", "Falha"
        SKIPPED = "pulado", "Pulado"

    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    source = models.CharField(max_length=30)
    market = models.CharField(max_length=20, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.FAILED)
    attempted = models.PositiveIntegerField(default=0)
    succeeded = models.PositiveIntegerField(default=0)
    failed = models.PositiveIntegerField(default=0)
    attempts = models.PositiveIntegerField(default=1)
    details = models.JSONField(default=dict, blank=True)  # {simbolo: "motivo"} para falhas individuais
    error = models.TextField(blank=True)  # erro sanitizado (sem chaves)

    class Meta:
        ordering = ["-started_at"]
        indexes = [models.Index(fields=["source", "-started_at"])]

    def __str__(self) -> str:
        return f"{self.source} {self.started_at:%Y-%m-%d %H:%M} {self.status}"


class Quote(models.Model):
    asset = models.ForeignKey("portfolio.Asset", on_delete=models.CASCADE, related_name="quotes")
    price = models.DecimalField(max_digits=20, decimal_places=8)
    currency = models.CharField(max_length=4, choices=QuoteCurrency.choices)
    previous_close = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    change_24h_pct = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    market_time = models.DateTimeField(null=True, blank=True)  # nulo quando a fonte não informa (spot)
    collected_at = models.DateTimeField()
    source = models.CharField(max_length=30)
    source_version = models.CharField(max_length=30, blank=True)
    quote_type = models.CharField(max_length=15, choices=QuoteType.choices)
    declared_delay_minutes = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=RecordStatus.choices, default=RecordStatus.VALID)
    discard_reason = models.CharField(max_length=200, blank=True)
    run = models.ForeignKey(
        CollectionRun, null=True, blank=True, on_delete=models.SET_NULL, related_name="quotes"
    )
    raw = models.JSONField(default=dict, blank=True)  # trecho bruto necessário à reprodução, sem chaves

    class Meta:
        ordering = ["-collected_at"]
        indexes = [models.Index(fields=["asset", "-collected_at"])]
        constraints = [
            models.UniqueConstraint(
                fields=["asset", "source", "market_time"],
                condition=models.Q(market_time__isnull=False, status="valida"),
                name="uniq_quote_asset_source_market_time",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.asset.symbol} {self.price} {self.currency} ({self.source}, {self.quote_type})"


class DailyBar(models.Model):
    asset = models.ForeignKey("portfolio.Asset", on_delete=models.CASCADE, related_name="daily_bars")
    session_date = models.DateField()
    time_basis = models.CharField(max_length=15, choices=TimeBasis.choices)
    open = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    high = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    low = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    close = models.DecimalField(max_digits=20, decimal_places=8)
    volume = models.DecimalField(max_digits=28, decimal_places=8, null=True, blank=True)
    source = models.CharField(max_length=30)
    provisional = models.BooleanField(default=False)  # sessão em andamento
    collected_at = models.DateTimeField()

    class Meta:
        ordering = ["asset", "session_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["asset", "session_date", "source"], name="uniq_bar_asset_date_source"
            )
        ]

    def __str__(self) -> str:
        return f"{self.asset_id} {self.session_date} close={self.close} ({self.source})"


class FxRate(models.Model):
    pair = models.CharField(max_length=7, default="USD-BRL")
    rate_type = models.CharField(max_length=20, choices=FxRateType.choices)
    bid = models.DecimalField(max_digits=12, decimal_places=6)
    ask = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    rate_time = models.DateTimeField(null=True, blank=True)
    collected_at = models.DateTimeField()
    source = models.CharField(max_length=30)
    status = models.CharField(max_length=10, choices=RecordStatus.choices, default=RecordStatus.VALID)
    run = models.ForeignKey(
        CollectionRun, null=True, blank=True, on_delete=models.SET_NULL, related_name="fx_rates"
    )
    raw = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-collected_at"]
        indexes = [models.Index(fields=["pair", "rate_type", "-collected_at"])]

    def __str__(self) -> str:
        return f"{self.pair} {self.bid} {self.rate_type} ({self.source})"


class FxDailyRate(models.Model):
    """Câmbio de fechamento por data (PTAX), base da rentabilidade 'desde a compra'."""

    pair = models.CharField(max_length=7, default="USD-BRL")
    date = models.DateField()
    rate_type = models.CharField(max_length=20, choices=FxRateType.choices, default=FxRateType.PTAX_CLOSE)
    bid = models.DecimalField(max_digits=12, decimal_places=6)
    ask = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    source = models.CharField(max_length=30)
    collected_at = models.DateTimeField()

    class Meta:
        ordering = ["pair", "date"]
        constraints = [
            models.UniqueConstraint(fields=["pair", "date", "rate_type", "source"], name="uniq_fx_daily")
        ]

    def __str__(self) -> str:
        return f"{self.pair} {self.date} {self.bid} ({self.source})"


class SourceBudget(models.Model):
    """Consumo de chamadas por fonte e período (dia ou mês)."""

    class Period(models.TextChoices):
        DAY = "dia", "Dia"
        MONTH = "mes", "Mês"
        MINUTE = "minuto", "Minuto"

    source = models.CharField(max_length=30)
    period = models.CharField(max_length=6, choices=Period.choices)
    period_key = models.CharField(max_length=16)  # 2026-10-29 | 2026-10 | 2026-10-29T14:35
    limit = models.PositiveIntegerField()
    consumed = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["source", "period", "period_key"], name="uniq_budget_period")
        ]

    def __str__(self) -> str:
        return f"{self.source} {self.period} {self.period_key}: {self.consumed}/{self.limit}"


class ScheduleState(models.Model):
    """Estado persistido da agenda do coletor, por tarefa (NYSE, B3, CRIPTO, CAMBIO_INTRA, CAMBIO_PTAX)."""

    task = models.CharField(max_length=20, unique=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_close_date = models.DateField(null=True, blank=True)  # última sessão cujo fechamento foi coletado
    last_status = models.CharField(max_length=10, blank=True)
    last_error = models.TextField(blank=True)
    force_requested_at = models.DateTimeField(null=True, blank=True)  # "Atualizar agora"
    last_slot = models.CharField(max_length=20, blank=True)  # tarefas por horário (resumo diário)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.task}: {self.last_status} ({self.last_attempt_at})"
