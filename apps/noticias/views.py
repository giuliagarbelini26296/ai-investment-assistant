import datetime as dt

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Max, Q
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.mercado.models import Favorito
from apps.portfolio.models import Asset

from .models import Article, SourceState


def _owned_assets():
    return Asset.objects.filter(
        Q(position_balances__isnull=False) | Q(lots__isnull=False, lots__cancelado_em__isnull=True),
        is_active=True,
    ).distinct()


def _decorate(items, own_ids, favorite_ids):
    for article in items:
        ids = {a.pk for a in article.assets.all()}
        article.in_portfolio = bool(ids & own_ids)
        article.in_favorites = bool(ids & favorite_ids)
        article.symbols = [a.symbol for a in article.assets.all()][:5]
    return items


def highlights(user, limit=3):
    own_ids = set(_owned_assets().values_list("pk", flat=True))
    favorite_ids = set(Favorito.objects.filter(user=user).values_list("asset_id", flat=True))
    qs = Article.objects.filter(assets__in=own_ids | favorite_ids).distinct()
    # No destaque da carteira, só textos já disponíveis em português.
    qs = qs.filter(translation_status__in=[Article.Translation.DONE, Article.Translation.ORIGINAL])
    return _decorate(list(qs.prefetch_related("assets")[:limit]), own_ids, favorite_ids)


@login_required
def index(request):
    region = request.GET.get("regiao", "todas")
    scope = request.GET.get("escopo", "todos")
    topic = request.GET.get("tema", "")
    query = request.GET.get("q", "").strip()[:100]
    publisher = request.GET.get("fonte", "").strip()[:120]
    asset_id = request.GET.get("ativo", "")
    days_text = request.GET.get("dias", "7")
    days = int(days_text) if days_text in {"1", "7", "30"} else 7
    if region not in {"brasil", "internacional"}:
        region = "todas"
    if scope not in {"carteira", "favoritos"}:
        scope = "todos"
    if topic not in Article.Topic.values:
        topic = ""
    own_ids = set(_owned_assets().values_list("pk", flat=True))
    favorite_ids = set(Favorito.objects.filter(user=request.user).values_list("asset_id", flat=True))
    recent = Article.objects.filter(published_at__gte=timezone.now() - dt.timedelta(days=days))
    qs = recent
    if region != "todas":
        qs = qs.filter(region=region)
    if scope != "todos":
        qs = qs.filter(assets__in=own_ids if scope == "carteira" else favorite_ids)
    if topic:
        qs = qs.filter(topic=topic)
    if publisher:
        qs = qs.filter(publisher=publisher)
    if asset_id.isdigit() and len(asset_id) <= 18:
        qs = qs.filter(assets__pk=int(asset_id))
    else:
        asset_id = ""
    if query:
        qs = qs.filter(
            Q(headline__icontains=query)
            | Q(summary__icontains=query)
            | Q(headline_pt__icontains=query)
            | Q(summary_pt__icontains=query)
            | Q(assets__symbol__icontains=query)
        )
    qs = qs.distinct().prefetch_related("assets")
    page = Paginator(qs, 20).get_page(request.GET.get("pagina"))
    _decorate(page.object_list, own_ids, favorite_ids)
    params = request.GET.copy()
    params.pop("pagina", None)
    regions = []
    for value, label in [("todas", "Todas"), ("brasil", "Brasil"), ("internacional", "Internacional")]:
        p = params.copy()
        p["regiao"] = value
        regions.append({"value": value, "label": label, "url": "?" + p.urlencode()})
    states = list(SourceState.objects.all())
    last = max((s.last_success_at for s in states if s.last_success_at), default=None)
    counts = {x["region"]: x["n"] for x in recent.values("region").annotate(n=Count("id"))}
    return render(
        request,
        "noticias/index.html",
        {
            "page": page,
            "regiao": region,
            "escopo": scope,
            "tema": topic,
            "q": query,
            "fonte": publisher,
            "ativo": asset_id,
            "dias": str(days),
            "regions": regions,
            "topics": Article.Topic.choices,
            "publishers": recent.order_by("publisher").values_list("publisher", flat=True).distinct(),
            "assets": Asset.objects.filter(is_active=True).exclude(market="CAMBIO").order_by("symbol"),
            "states": states,
            "last_update": last,
            "counts": counts,
            "pending": recent.filter(translation_status=Article.Translation.PENDING).count(),
            "pagination_query": params.urlencode(),
            "latest_publication": recent.aggregate(value=Max("published_at"))["value"],
        },
    )


@login_required
@require_POST
def refresh(request):
    SourceState.objects.update(requested=True)
    messages.success(request, "Atualização solicitada. Novas notícias aparecerão após a próxima verificação.")
    return redirect("noticias:index")
