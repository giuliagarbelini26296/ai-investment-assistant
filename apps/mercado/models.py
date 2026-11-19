"""
Inteligência de mercado: favoritos, dados de analistas, notícias, resultados, fundamentos e Focus.

Princípios (mesmos do restante do portal):
- Todo registro carrega fonte e horário de coleta; nada é estimado para preencher lacunas.
- Consenso de analistas e preço-alvo são opinião de terceiros (projeção), nunca fato; a interface rotula.
- Nenhum dado aqui gera recomendação: o score do portal é uma régua de critérios fixos e visíveis.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models


class GrupoFavoritos(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="grupos_favoritos"
    )
    nome = models.CharField("nome", max_length=40)
    ordem = models.PositiveIntegerField(default=0)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["ordem", "nome"]
        constraints = [models.UniqueConstraint(fields=["user", "nome"], name="uniq_grupo_favoritos")]

    def __str__(self) -> str:
        return self.nome


class Favorito(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="favoritos")
    asset = models.ForeignKey("portfolio.Asset", on_delete=models.CASCADE, related_name="favoritos")
    grupos = models.ManyToManyField(GrupoFavoritos, blank=True, related_name="favoritos")
    ordem = models.PositiveIntegerField(default=0)
    nota = models.CharField("anotação", max_length=200, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["ordem", "asset__symbol"]
        constraints = [models.UniqueConstraint(fields=["user", "asset"], name="uniq_favorito")]

    def __str__(self) -> str:
        return f"☆ {self.asset.symbol}"


class AnalystSnapshot(models.Model):
    """Contagem de recomendações de analistas num período (opinião de terceiros)."""

    asset = models.ForeignKey("portfolio.Asset", on_delete=models.CASCADE, related_name="recomendacoes")
    source = models.CharField(max_length=30)
    period = models.DateField()  # mês de referência informado pela fonte (ou data da coleta)
    strong_buy = models.PositiveIntegerField(default=0)
    buy = models.PositiveIntegerField(default=0)
    hold = models.PositiveIntegerField(default=0)
    sell = models.PositiveIntegerField(default=0)
    strong_sell = models.PositiveIntegerField(default=0)
    collected_at = models.DateTimeField()

    class Meta:
        ordering = ["asset", "-period"]
        constraints = [
            models.UniqueConstraint(fields=["asset", "source", "period"], name="uniq_analyst_snapshot")
        ]

    def __str__(self) -> str:
        return f"{self.asset_id} {self.period} {self.source} ({self.total} analistas)"

    @property
    def total(self) -> int:
        return self.strong_buy + self.buy + self.hold + self.sell + self.strong_sell

    @property
    def pct_compra(self):
        from decimal import Decimal

        return None if not self.total else Decimal(100) * (self.strong_buy + self.buy) / self.total

    @property
    def pct_venda(self):
        from decimal import Decimal

        return None if not self.total else Decimal(100) * (self.sell + self.strong_sell) / self.total

    @property
    def consenso(self) -> str:
        """Maioria simples entre comprar / manter / vender (rótulo da distribuição, não do portal)."""
        if not self.total:
            return "sem dados"
        compra, manter, venda = self.strong_buy + self.buy, self.hold, self.sell + self.strong_sell
        maior = max(compra, manter, venda)
        if [compra, manter, venda].count(maior) > 1:
            return "dividido"
        return {compra: "comprar", manter: "manter", venda: "vender"}[maior]


class PriceTarget(models.Model):
    """Preço-alvo de consenso informado pela fonte (projeção de terceiros, horizonte típico de 12 meses)."""

    asset = models.ForeignKey("portfolio.Asset", on_delete=models.CASCADE, related_name="precos_alvo")
    source = models.CharField(max_length=30)
    mean = models.DecimalField(max_digits=20, decimal_places=6, null=True, blank=True)
    median = models.DecimalField(max_digits=20, decimal_places=6, null=True, blank=True)
    high = models.DecimalField(max_digits=20, decimal_places=6, null=True, blank=True)
    low = models.DecimalField(max_digits=20, decimal_places=6, null=True, blank=True)
    reference_date = models.DateField(null=True, blank=True)  # quando a fonte informa
    collected_at = models.DateTimeField()

    class Meta:
        ordering = ["asset", "-collected_at"]

    def __str__(self) -> str:
        return f"{self.asset_id} alvo {self.mean} ({self.source})"


class Fundamentos(models.Model):
    """Ficha da empresa (fundamentos básicos) por coleta."""

    asset = models.ForeignKey("portfolio.Asset", on_delete=models.CASCADE, related_name="fundamentos")
    source = models.CharField(max_length=30)
    sector = models.CharField(max_length=80, blank=True)
    industry = models.CharField(max_length=120, blank=True)
    pe = models.DecimalField("P/L", max_digits=14, decimal_places=4, null=True, blank=True)
    forward_pe = models.DecimalField("P/L projetado", max_digits=14, decimal_places=4, null=True, blank=True)
    profit_margin = models.DecimalField(
        "margem líquida", max_digits=12, decimal_places=6, null=True, blank=True
    )
    roe = models.DecimalField("ROE", max_digits=12, decimal_places=6, null=True, blank=True)
    market_cap = models.DecimalField(max_digits=24, decimal_places=2, null=True, blank=True)
    dividend_yield = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    week52_high = models.DecimalField(max_digits=20, decimal_places=6, null=True, blank=True)
    week52_low = models.DecimalField(max_digits=20, decimal_places=6, null=True, blank=True)
    latest_quarter = models.DateField(null=True, blank=True)
    # valuation e endividamento (rc8)
    price_to_book = models.DecimalField("P/VP", max_digits=14, decimal_places=4, null=True, blank=True)
    ev_ebitda = models.DecimalField("EV/EBITDA", max_digits=14, decimal_places=4, null=True, blank=True)
    revenue_growth = models.DecimalField(
        "crescimento da receita", max_digits=12, decimal_places=6, null=True, blank=True
    )  # fração; período informado pela fonte (trimestre vs. ano anterior, em geral)
    net_debt_ebitda = models.DecimalField(
        "dívida líquida/EBITDA", max_digits=14, decimal_places=4, null=True, blank=True
    )
    net_debt_calculado = models.BooleanField(default=False)  # calculado pelo portal (dívida − caixa) / EBITDA
    recommendation_key = models.CharField(max_length=20, blank=True)  # ex.: buy, hold (fonte)
    recommendation_mean = models.DecimalField(max_digits=6, decimal_places=3, null=True, blank=True)
    analyst_opinions = models.PositiveIntegerField(null=True, blank=True)
    collected_at = models.DateTimeField()

    class Meta:
        ordering = ["asset", "-collected_at"]

    def __str__(self) -> str:
        return f"{self.asset_id} ficha {self.source} {self.collected_at:%Y-%m-%d}"


class Provento(models.Model):
    """Dividendo, JCP ou outro provento em dinheiro por ação (valor bruto informado pela fonte)."""

    asset = models.ForeignKey("portfolio.Asset", on_delete=models.CASCADE, related_name="proventos")
    source = models.CharField(max_length=30)
    tipo = models.CharField(max_length=30)  # DIVIDENDO, JCP, RENDIMENTO… (rótulo da fonte)
    data_com = models.DateField(null=True, blank=True)  # último dia com direito / data ex
    data_pagamento = models.DateField(null=True, blank=True)
    data_aprovacao = models.DateField(null=True, blank=True)
    valor = models.DecimalField(max_digits=20, decimal_places=10)
    moeda = models.CharField(max_length=3)
    collected_at = models.DateTimeField()

    class Meta:
        ordering = ["asset", "-data_com"]
        constraints = [
            models.UniqueConstraint(
                fields=["asset", "source", "tipo", "data_com", "data_pagamento", "valor"],
                name="uniq_provento",
            )
        ]

    def __str__(self) -> str:
        return f"{self.asset_id} {self.tipo} {self.valor} com {self.data_com}"

    @property
    def data_referencia(self):
        return self.data_com or self.data_pagamento


class AnalystAction(models.Model):
    """Mudança de recomendação feita por uma instituição identificada (opinião de terceiros)."""

    asset = models.ForeignKey("portfolio.Asset", on_delete=models.CASCADE, related_name="acoes_analistas")
    source = models.CharField(max_length=30)
    date = models.DateField()
    firm = models.CharField(max_length=80)
    from_grade = models.CharField(max_length=40, blank=True)
    to_grade = models.CharField(max_length=40, blank=True)
    action = models.CharField(max_length=10, blank=True)
    collected_at = models.DateTimeField()

    class Meta:
        ordering = ["-date", "firm"]
        constraints = [
            models.UniqueConstraint(fields=["asset", "source", "date", "firm"], name="uniq_analyst_action")
        ]

    def __str__(self) -> str:
        return f"{self.asset_id} {self.date} {self.firm} {self.from_grade}→{self.to_grade}"

    @property
    def acao_rotulo(self) -> str:
        from apps.marketdata.adapters.inteligencia import ACOES_ANALISTA

        return ACOES_ANALISTA.get(self.action, self.action or "—")


class MacroSerie(models.Model):
    """Indicador macro (SGS/BCB): Selic meta, IPCA mensal, IPCA 12 meses."""

    codigo = models.PositiveIntegerField()
    nome = models.CharField(max_length=60)
    data = models.DateField()
    valor = models.DecimalField(max_digits=14, decimal_places=6)
    source = models.CharField(max_length=30, default="sgs_bcb")
    collected_at = models.DateTimeField()

    class Meta:
        ordering = ["codigo", "-data"]
        constraints = [models.UniqueConstraint(fields=["codigo", "data"], name="uniq_macro_serie")]

    def __str__(self) -> str:
        return f"{self.nome} {self.data}: {self.valor}"


class NewsItem(models.Model):
    class Categoria(models.TextChoices):
        EMPRESA = "empresa", "Empresa"
        CAMBIO = "cambio", "Câmbio"
        MERCADO = "mercado", "Mercado"

    asset = models.ForeignKey(
        "portfolio.Asset", null=True, blank=True, on_delete=models.CASCADE, related_name="noticias"
    )
    categoria = models.CharField(max_length=10, choices=Categoria.choices, default=Categoria.EMPRESA)
    source = models.CharField(max_length=30)  # fonte de dados (finnhub, alphavantage)
    publisher = models.CharField(max_length=120, blank=True)  # veículo informado pela fonte
    headline = models.CharField(max_length=400)
    summary = models.TextField(blank=True)
    url = models.URLField(max_length=600)
    published_at = models.DateTimeField()
    sentiment = models.DecimalField(max_digits=6, decimal_places=4, null=True, blank=True)
    sentiment_label = models.CharField(max_length=30, blank=True)
    collected_at = models.DateTimeField()

    class Meta:
        ordering = ["-published_at"]
        indexes = [models.Index(fields=["asset", "-published_at"])]
        constraints = [models.UniqueConstraint(fields=["source", "url", "asset"], name="uniq_news")]

    def __str__(self) -> str:
        return self.headline[:80]


class EarningsEvent(models.Model):
    """Divulgação de resultado (data informada pela fonte; estimativas são projeções de mercado)."""

    asset = models.ForeignKey("portfolio.Asset", on_delete=models.CASCADE, related_name="resultados")
    source = models.CharField(max_length=30)
    date = models.DateField()
    hour = models.CharField(max_length=10, blank=True)  # bmo / amc / dmh, quando informado
    eps_estimate = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    eps_actual = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    revenue_estimate = models.DecimalField(max_digits=24, decimal_places=2, null=True, blank=True)
    revenue_actual = models.DecimalField(max_digits=24, decimal_places=2, null=True, blank=True)
    quarter = models.PositiveSmallIntegerField(null=True, blank=True)
    year = models.PositiveSmallIntegerField(null=True, blank=True)
    collected_at = models.DateTimeField()

    class Meta:
        ordering = ["date"]
        constraints = [models.UniqueConstraint(fields=["asset", "source", "date"], name="uniq_earnings")]

    def __str__(self) -> str:
        return f"{self.asset_id} resultado {self.date}"

    @property
    def horario_rotulo(self) -> str:
        return {"bmo": "antes da abertura", "amc": "após o fechamento", "dmh": "durante o pregão"}.get(
            self.hour, ""
        )


class FocusExpectativa(models.Model):
    """Boletim Focus (BCB): mediana das projeções de mercado para um indicador e ano."""

    indicador = models.CharField(max_length=40)
    data = models.DateField()  # data da pesquisa
    data_referencia = models.CharField(max_length=10)  # ano projetado
    base_calculo = models.PositiveSmallIntegerField(default=0)  # 0 = 30 dias; 1 = 5 dias úteis
    media = models.DecimalField(max_digits=14, decimal_places=6, null=True, blank=True)
    mediana = models.DecimalField(max_digits=14, decimal_places=6, null=True, blank=True)
    minimo = models.DecimalField(max_digits=14, decimal_places=6, null=True, blank=True)
    maximo = models.DecimalField(max_digits=14, decimal_places=6, null=True, blank=True)
    desvio = models.DecimalField(max_digits=14, decimal_places=6, null=True, blank=True)
    respondentes = models.PositiveIntegerField(null=True, blank=True)
    collected_at = models.DateTimeField()

    class Meta:
        ordering = ["-data", "data_referencia"]
        constraints = [
            models.UniqueConstraint(
                fields=["indicador", "data", "data_referencia", "base_calculo"], name="uniq_focus"
            )
        ]

    def __str__(self) -> str:
        return f"Focus {self.indicador} {self.data_referencia} em {self.data}: {self.mediana}"


class RefreshState(models.Model):
    """Rodízio de atualização por ativo e tipo de dado (candles, recomendações, notícias, ficha)."""

    asset = models.ForeignKey("portfolio.Asset", on_delete=models.CASCADE, related_name="atualizacoes")
    kind = models.CharField(max_length=16)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=300, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["asset", "kind"], name="uniq_refresh_state")]

    def __str__(self) -> str:
        return f"{self.asset_id} {self.kind} {self.last_success_at}"
