from django.conf import settings
from django.db import models


class WorkbookState(models.Model):
    account = models.CharField(max_length=10, unique=True, choices=[('ana','Ana'),('bruno','Bruno')])
    version = models.PositiveIntegerField(default=0)
    data = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    class Meta:
        default_permissions = ()


class WorkbookRevision(models.Model):
    workbook = models.ForeignKey(WorkbookState, on_delete=models.PROTECT, related_name='revisions')
    version = models.PositiveIntegerField()
    data = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    class Meta:
        default_permissions = ()
        constraints = [models.UniqueConstraint(fields=['workbook','version'], name='workbook_revision_unique')]


class BrokerSnapshot(models.Model):
    """Dated evidence, independent of the market quote stream and trade ledger."""
    account = models.CharField(max_length=10, choices=[('ana','Ana'),('bruno','Bruno')])
    source_key = models.CharField(max_length=120)
    captured_at = models.DateTimeField()
    data = models.JSONField()
    recorded_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        default_permissions = ()
        ordering = ['-captured_at','-pk']
        constraints = [models.UniqueConstraint(fields=['account','source_key'], name='broker_snapshot_unique')]
    def save(self,*args,**kwargs):
        if not self._state.adding:
            from django.core.exceptions import ValidationError
            raise ValidationError('Comprovação preservada: registre outra captura para atualizar.')
        super().save(*args,**kwargs)


class PatrimonioDiario(models.Model):
    """Foto diária do patrimônio, por carteira e moeda, sem conversão cambial.

    Gravada pelo coletor ao longo do dia (a última leitura do dia fica valendo). Os dias anteriores
    ao primeiro registro só existem quando reconstruídos a partir do histórico de movimentações do
    BTG (origem 'btg'), com quantidades e valores aplicados confirmados e fechamentos reais.

    flow: dinheiro que entrou (+) ou saiu (−) dos ativos no dia: compras ao custo, vendas pelo valor
    recebido. income: proventos recebidos no dia. Ambos alimentam a rentabilidade (Modified Dietz).
    """
    ORIGENS = [('portal', 'Registro diário do portal'), ('btg', 'Reconstruído (histórico do BTG)')]
    account = models.CharField(max_length=10, choices=[('ana', 'Ana'), ('bruno', 'Bruno')])
    currency = models.CharField(max_length=3)
    date = models.DateField()
    value = models.DecimalField(max_digits=28, decimal_places=8, null=True)
    invested = models.DecimalField(max_digits=28, decimal_places=8)
    gain = models.DecimalField(max_digits=28, decimal_places=8, null=True)
    positions = models.PositiveIntegerField()
    quoted = models.PositiveIntegerField()
    captured_at = models.DateTimeField()
    source = models.CharField(max_length=10, choices=ORIGENS, default='portal', db_default='portal')
    flow = models.DecimalField(max_digits=28, decimal_places=8, null=True, blank=True)
    income = models.DecimalField(max_digits=28, decimal_places=8, default=0, db_default=0)

    class Meta:
        default_permissions = ()
        ordering = ['account', 'currency', 'date']
        constraints = [models.UniqueConstraint(fields=['account', 'currency', 'date'], name='patrimonio_diario_unico')]

    @property
    def complete(self):
        return self.positions > 0 and self.quoted == self.positions and self.value is not None


class AssetLogo(models.Model):
    """Cópia local da logo de um ativo (SVG filtrado). Evita que o navegador revele a carteira a terceiros."""
    symbol = models.CharField(max_length=12, unique=True)
    svg = models.TextField(blank=True)
    found = models.BooleanField(default=False)
    source_symbol = models.CharField(max_length=12, blank=True)
    fetched_at = models.DateTimeField()

    class Meta:
        default_permissions = ()


class PushSubscription(models.Model):
    """Aparelho inscrito para notificações do app (Web Push)."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+')
    endpoint = models.URLField(max_length=800, unique=True)
    p256dh = models.CharField(max_length=200)
    auth = models.CharField(max_length=100)
    accounts = models.JSONField(default=list)  # ['ana', 'bruno']
    device = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    failures = models.PositiveIntegerField(default=0)

    class Meta:
        default_permissions = ()


class PushDelivery(models.Model):
    """Notícia já avisada a um aparelho (evita repetição e controla o limite diário)."""
    subscription = models.ForeignKey(PushSubscription, on_delete=models.CASCADE, related_name='deliveries')
    article = models.ForeignKey('noticias.Article', on_delete=models.CASCADE, related_name='+')
    symbol = models.CharField(max_length=12)
    notified = models.BooleanField(default=True)  # False = incluída num aviso agrupado sem ser a manchete
    sent_at = models.DateTimeField()

    class Meta:
        default_permissions = ()
        constraints = [models.UniqueConstraint(fields=['subscription', 'article'], name='push_delivery_unica')]
        indexes = [models.Index(fields=['subscription', 'sent_at'], name='react_porta_subscri_push_idx')]


class PushApiKey(models.Model):
    """Chave de um serviço externo (ex.: Milo) que só pode enviar notificações do app.

    Guarda apenas o SHA-256 da chave; o valor é mostrado uma única vez, na criação.
    """
    name = models.CharField(max_length=40, unique=True)
    prefix = models.CharField(max_length=12)
    token_hash = models.CharField(max_length=64, unique=True)
    # 'notificacoes' só envia avisos; 'leitura' só consulta pelo MCP. Nunca as duas coisas.
    scope = models.CharField(max_length=15, default='notificacoes', choices=[('notificacoes', 'Enviar notificações'), ('leitura', 'Ler pelo MCP')])
    accounts = models.JSONField(default=list)  # carteiras visíveis no MCP
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        default_permissions = ()


class ExternalNotification(models.Model):
    """Registro de cada notificação recebida de um serviço externo (auditoria e limite por hora)."""
    key = models.ForeignKey(PushApiKey, on_delete=models.CASCADE, related_name='notifications')
    title = models.CharField(max_length=80)
    body = models.CharField(max_length=300)
    devices = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20)
    created_at = models.DateTimeField()

    class Meta:
        default_permissions = ()
        indexes = [models.Index(fields=['key', 'created_at'], name='react_porta_extnotif_idx')]


class McpCall(models.Model):
    """Registro de cada consulta feita pelo MCP (auditoria e limite por hora)."""
    key = models.ForeignKey(PushApiKey, on_delete=models.CASCADE, related_name='mcp_calls')
    method = models.CharField(max_length=40)
    tool = models.CharField(max_length=40, blank=True)
    ok = models.BooleanField(default=True)
    created_at = models.DateTimeField()

    class Meta:
        default_permissions = ()
        indexes = [models.Index(fields=['key', 'created_at'], name='react_porta_mcpcall_idx')]
