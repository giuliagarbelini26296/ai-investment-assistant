from django.db import models


class Article(models.Model):
    class Region(models.TextChoices):
        BRASIL = "brasil", "Brasil"
        WORLD = "internacional", "Internacional"

    class Topic(models.TextChoices):
        EMPRESAS = "empresas", "Empresas"
        ECONOMIA = "economia", "Economia"
        JUROS = "juros", "Juros e inflação"
        CAMBIO = "cambio", "Câmbio"
        CRIPTO = "cripto", "Criptomoedas"

    class Translation(models.TextChoices):
        ORIGINAL = "original", "Original em português"
        PENDING = "pendente", "Tradução em andamento"
        DONE = "traduzida", "Tradução automática"
        FAILED = "falha", "Tradução temporariamente indisponível"

    url_hash = models.CharField(max_length=64, unique=True)
    url = models.URLField(max_length=1200)
    headline = models.CharField(max_length=600)
    summary = models.TextField(blank=True)
    headline_pt = models.CharField(max_length=800, blank=True)
    summary_pt = models.TextField(blank=True)
    language = models.CharField(max_length=10, default="en")
    region = models.CharField(max_length=20, choices=Region.choices, default=Region.WORLD)
    topic = models.CharField(max_length=20, choices=Topic.choices, default=Topic.ECONOMIA)
    source = models.CharField(max_length=40)
    publisher = models.CharField(max_length=120)
    published_at = models.DateTimeField(db_index=True)
    collected_at = models.DateTimeField()
    assets = models.ManyToManyField("portfolio.Asset", blank=True, related_name="central_noticias")
    translation_status = models.CharField(
        max_length=15, choices=Translation.choices, default=Translation.PENDING
    )
    translation_model = models.CharField(max_length=80, blank=True)
    translated_at = models.DateTimeField(null=True, blank=True)
    translation_attempt_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-published_at", "-pk"]
        indexes = [models.Index(fields=["region", "-published_at"])]

    def __str__(self):
        return self.headline[:100]

    @property
    def title(self):
        return self.headline_pt or self.headline

    @property
    def description(self):
        return self.summary_pt or self.summary

    @property
    def translated(self):
        return self.translation_status == self.Translation.DONE

class SourceState(models.Model):
    key = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=120)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    next_check_at = models.DateTimeField(null=True, blank=True)
    etag = models.CharField(max_length=400, blank=True)
    last_modified = models.CharField(max_length=120, blank=True)
    last_error = models.CharField(max_length=200, blank=True)
    article_count = models.PositiveIntegerField(default=0)
    requested = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name
