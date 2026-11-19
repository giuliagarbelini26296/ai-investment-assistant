"""
Alertas baseados em regras transparentes (escopo v1.3 rev. 2, seção 7; proposta v1.1, seção 9).

- Seis condições em três famílias: desde a compra (queda/alta ≥ X%), dia/24 h (queda/alta ≥ X%),
  preço absoluto (abaixo/acima de um valor com moeda explícita).
- Percentuais são avaliados na moeda nativa do ativo.
- A classificação compra/venda/manutenção é um rótulo escolhido pelo usuário na regra — nunca inferida;
  o padrão é "apenas aviso".
- Alertas disparados geram Notificacao por canal configurado (Telegram, feed do macOS); ver notify.py.
- Cada edição gera uma RuleVersion imutável; alertas apontam para a versão que os gerou.
- Texto obrigatório em todo alerta: TEXTO_NAO_RECOMENDACAO.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

TEXTO_NAO_RECOMENDACAO = (
    "Sinal baseado em regra definida pelo usuário. Não constitui recomendação de investimento."
)


class Family(models.TextChoices):
    SINCE_PURCHASE = "desde_compra", "Desde a compra (retorno %)"
    DAY = "dia_24h", "No dia / 24 h (variação %)"
    ABSOLUTE = "preco_absoluto", "Preço absoluto"
    MARKET = "mercado", "Sinais de mercado (52 semanas, suporte/resistência, resultados, analistas)"


class Condition(models.TextChoices):
    DROP = "queda", "Queda ≥ X%"
    RISE = "alta", "Alta ≥ X%"
    BELOW = "abaixo", "Preço abaixo de"
    ABOVE = "acima", "Preço acima de"
    NEAR_LOW = "min52", "Até X% acima da mínima de 52 semanas"
    SUPPORT = "suporte", "Rompe o suporte (mínima de X sessões)"
    RESISTANCE = "resist", "Rompe a resistência (máxima de X sessões)"
    EARNINGS = "result", "Resultado trimestral em até X dias"
    RATING = "recomend", "Recomendações de compra mudam ≥ X p.p."


class Classification(models.TextChoices):
    NOTICE = "aviso", "Apenas aviso (sem classificação)"
    BUY = "compra", "Possível oportunidade de compra"
    SELL = "venda", "Possível oportunidade de venda"
    HOLD = "manutencao", "Manutenção"


CONDICOES_POR_FAMILIA = {
    Family.SINCE_PURCHASE: (Condition.DROP, Condition.RISE),
    Family.DAY: (Condition.DROP, Condition.RISE),
    Family.ABSOLUTE: (Condition.BELOW, Condition.ABOVE),
    Family.MARKET: (
        Condition.NEAR_LOW,
        Condition.SUPPORT,
        Condition.RESISTANCE,
        Condition.EARNINGS,
        Condition.RATING,
    ),
}
# Limites aceitos por condição de mercado: (mínimo, máximo, inteiro?)
LIMITES_MERCADO = {
    Condition.NEAR_LOW: (Decimal("0.1"), Decimal("50"), False),
    Condition.SUPPORT: (Decimal("2"), Decimal("250"), True),
    Condition.RESISTANCE: (Decimal("2"), Decimal("250"), True),
    Condition.EARNINGS: (Decimal("1"), Decimal("90"), True),
    Condition.RATING: (Decimal("1"), Decimal("100"), False),
}
# Condições que não dependem do preço (avaliadas mesmo com cotação vencida)
CONDICOES_SEM_PRECO = (Condition.EARNINGS, Condition.RATING)


class Rule(models.Model):
    """Cabeçalho da regra; os parâmetros vivem em RuleVersion (imutável)."""

    name = models.CharField("nome", max_length=80)
    asset = models.ForeignKey(
        "portfolio.Asset",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="rules",
        verbose_name="ativo (vazio = regra global)",
    )
    is_active = models.BooleanField("ativa", default=True)
    current_version = models.PositiveIntegerField(default=0, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_active", "name"]

    def __str__(self) -> str:
        alvo = self.asset.symbol if self.asset_id else "global"
        return f"{self.name} ({alvo})"

    @property
    def versao_atual(self) -> RuleVersion | None:
        return self.versions.filter(number=self.current_version).first()


class RuleVersion(models.Model):
    rule = models.ForeignKey(Rule, on_delete=models.CASCADE, related_name="versions")
    number = models.PositiveIntegerField()
    family = models.CharField("família", max_length=16, choices=Family.choices)
    condition = models.CharField("condição", max_length=8, choices=Condition.choices)
    threshold = models.DecimalField("limite (X% ou preço)", max_digits=20, decimal_places=8)
    currency = models.CharField("moeda do preço absoluto", max_length=4, blank=True)
    classification = models.CharField("classificação", max_length=12, choices=Classification.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    reason = models.CharField("motivo da alteração", max_length=200, blank=True)

    class Meta:
        ordering = ["rule", "number"]
        constraints = [models.UniqueConstraint(fields=["rule", "number"], name="uniq_rule_version")]

    def __str__(self) -> str:
        return f"{self.rule_id} v{self.number}: {self.descricao}"

    def clean(self):
        if self.condition not in CONDICOES_POR_FAMILIA.get(self.family, ()):
            raise ValidationError({"condition": "Condição incompatível com a família da regra."})
        if self.threshold is None or self.threshold <= 0:
            raise ValidationError({"threshold": "O limite deve ser positivo."})
        if self.family == Family.ABSOLUTE and self.currency not in ("USD", "BRL"):
            raise ValidationError({"currency": "Informe a moeda do preço absoluto (USD ou BRL)."})
        if self.family == Family.MARKET:
            minimo, maximo, inteiro = LIMITES_MERCADO[self.condition]
            if not (minimo <= self.threshold <= maximo):
                raise ValidationError({"threshold": f"Use um valor entre {minimo} e {maximo}."})
            if inteiro and self.threshold != self.threshold.to_integral_value():
                raise ValidationError({"threshold": "Use um número inteiro (sessões ou dias)."})
        if self.family != Family.ABSOLUTE:
            self.currency = ""

    @property
    def descricao(self) -> str:
        x = f"{self.threshold.normalize():f}"
        if self.family == Family.SINCE_PURCHASE:
            return f"{'queda' if self.condition == Condition.DROP else 'alta'} ≥ {x}% desde a compra"
        if self.family == Family.DAY:
            return f"{'queda' if self.condition == Condition.DROP else 'alta'} ≥ {x}% no dia/24 h"
        if self.family == Family.MARKET:
            return {
                Condition.NEAR_LOW: f"até {x}% acima da mínima de 52 semanas",
                Condition.SUPPORT: f"rompe o suporte de {x} sessões",
                Condition.RESISTANCE: f"rompe a resistência de {x} sessões",
                Condition.EARNINGS: f"resultado trimestral em até {x} dias",
                Condition.RATING: f"recomendações de compra mudam ≥ {x} p.p.",
            }.get(self.condition, self.condition)
        simbolo = {"USD": "US$", "BRL": "R$"}.get(self.currency, self.currency)
        return f"preço {'abaixo' if self.condition == Condition.BELOW else 'acima'} de {simbolo} {x}"


class RuleState(models.Model):
    """Estado da avaliação de uma versão de regra para um ativo (uma linha por versão × ativo)."""

    version = models.ForeignKey(RuleVersion, on_delete=models.CASCADE, related_name="states")
    asset = models.ForeignKey("portfolio.Asset", on_delete=models.CASCADE, related_name="rule_states")
    last_condition = models.BooleanField(null=True)  # None = nunca avaliada com dado válido
    last_evaluated_at = models.DateTimeField(null=True, blank=True)
    suspended = models.BooleanField(default=False)  # atualidade não permite avaliar
    suspended_reason = models.CharField(max_length=200, blank=True)
    active_alert = models.OneToOneField(
        "Alert", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        constraints = [models.UniqueConstraint(fields=["version", "asset"], name="uniq_state_version_asset")]

    def __str__(self) -> str:
        return f"{self.version} × {self.asset_id}: {self.last_condition} susp={self.suspended}"


class Alert(models.Model):
    version = models.ForeignKey(RuleVersion, on_delete=models.PROTECT, related_name="alerts")
    asset = models.ForeignKey("portfolio.Asset", on_delete=models.CASCADE, related_name="alerts")
    quote = models.ForeignKey("marketdata.Quote", null=True, on_delete=models.SET_NULL, related_name="alerts")
    triggered_at = models.DateTimeField()
    resolved_at = models.DateTimeField(null=True, blank=True)
    seen_at = models.DateTimeField(null=True, blank=True)
    silenced_until = models.DateTimeField(null=True, blank=True)  # "silenciar por 24 h": some do destaque
    classification = models.CharField(max_length=12, choices=Classification.choices)
    observed = models.JSONField(
        default=dict
    )  # preço, preço médio, retorno, variação, limite, fonte, horários
    position_revision = models.CharField(max_length=60, blank=True)  # assinatura da posição avaliada

    class Meta:
        ordering = ["-triggered_at"]
        indexes = [models.Index(fields=["asset", "-triggered_at"])]

    def __str__(self) -> str:
        return f"{self.asset_id} {self.version.descricao} em {self.triggered_at:%d/%m %H:%M}"

    @property
    def is_active(self) -> bool:
        return self.resolved_at is None

    @property
    def texto_obrigatorio(self) -> str:
        return TEXTO_NAO_RECOMENDACAO

    def silenciado(self, agora=None) -> bool:
        from django.utils import timezone

        return self.silenced_until is not None and self.silenced_until > (agora or timezone.now())

    @property
    def limite(self) -> Decimal:
        return self.version.threshold


class Channel(models.TextChoices):
    TELEGRAM = "telegram", "Telegram"
    MAC = "mac", "Notificação do macOS"


class NotificationStatus(models.TextChoices):
    PENDING = "pendente", "Pendente"
    SENT = "enviada", "Enviada"
    FAILED = "falha", "Falha"


class NotificationKind(models.TextChoices):
    ALERT = "alerta", "Alerta de regra"
    SUMMARY = "resumo", "Resumo diário"
    OPS = "operacao", "Aviso operacional"


class Notification(models.Model):
    """Caixa de saída por canal.

    - alerta: um registro por alerta × canal (restrição única) — envio único, retentativas.
    - resumo / operação: texto pronto em `title`/`body`, sem alerta associado.
    """

    alert = models.ForeignKey(
        Alert, null=True, blank=True, on_delete=models.CASCADE, related_name="notifications"
    )
    kind = models.CharField(max_length=10, choices=NotificationKind.choices, default=NotificationKind.ALERT)
    title = models.CharField(max_length=120, blank=True)
    body = models.TextField(blank=True)
    channel = models.CharField(max_length=12, choices=Channel.choices)
    status = models.CharField(
        max_length=10, choices=NotificationStatus.choices, default=NotificationStatus.PENDING
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["alert", "channel"],
                condition=models.Q(alert__isnull=False),
                name="uniq_notification_alert_channel",
            )
        ]

    def __str__(self) -> str:
        return f"{self.channel} {self.kind} {self.alert_id or self.title} {self.status}"


class OpsIncident(models.Model):
    """Incidente operacional detectado pelo vigia do coletor (um aviso ao abrir, um ao normalizar)."""

    key = models.CharField(max_length=40)  # ex.: falhas:CRIPTO, parado:NYSE
    message = models.CharField(max_length=300)
    opened_at = models.DateTimeField()
    notified_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-opened_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["key"], condition=models.Q(resolved_at__isnull=True), name="uniq_ops_incident_open"
            )
        ]

    def __str__(self) -> str:
        return f"{self.key} desde {self.opened_at:%d/%m %H:%M}" + (" (resolvido)" if self.resolved_at else "")
