"""Telas do menu Mercado: visão geral, rankings, oportunidades, dólar e ⭐ Favoritos."""

from __future__ import annotations

import datetime as dt

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.alerts.models import TEXTO_NAO_RECOMENDACAO
from apps.alerts.services import ativos_com_alerta
from apps.portfolio.models import Asset, Market

from . import analise, dolar, universo
from .estado import estado_coleta
from . import resumo as resumo_mod
from .indicadores import PERIODOS_DIAS, ROTULOS_PERIODO
from .models import Favorito, GrupoFavoritos, RefreshState

AVISO_METODOLOGIA = (
    "Score e listas seguem regras fixas e públicas do portal (ver 'Como o score é calculado'). "
    "Projeções de analistas e do Focus são opiniões de terceiros, não certezas. " + TEXTO_NAO_RECOMENDACAO
)


def _periodo(request, padrao="1d") -> str:
    p = request.GET.get("periodo", padrao)
    return p if p in PERIODOS_DIAS else padrao


def _fontes_status() -> list[dict]:
    """Estado real de cada fonte de inteligência (ok, parcial, pago, falha...), para os selos do Mercado."""
    return resumo_mod.estado_fontes()


def _market_scope(request, lista):
    market = request.GET.get('mercado', 'todos')
    if market == 'brasil':
        return [x for x in lista if x.asset.market == Market.B3]
    if market == 'internacional':
        return [x for x in lista if x.asset.market in (Market.NYSE, Market.NASDAQ)]
    return lista


def _base(request, aba: str) -> dict:
    favs = analise.ids_favoritos(request.user)
    return {
        "aba": aba,
        "mercado_filtro": request.GET.get('mercado') if request.GET.get('mercado') in ('brasil', 'internacional') else 'todos',
        "mercados_filtro": [('todos', 'Todos'), ('brasil', 'Brasil'), ('internacional', 'Internacional · EUA')],
        "favoritos_ids": favs,
        "periodos": [(k, ROTULOS_PERIODO[k]) for k in PERIODOS_DIAS],
        "aviso_metodologia": AVISO_METODOLOGIA,
        "fontes_inteligencia": _fontes_status(),
        "agora": timezone.now(),
        "estado_focus": estado_coleta("FOCUS"),
        "estado_macro": estado_coleta("MACRO"),
        "universo_vazio": not Asset.objects.filter(
            acompanhamento=True, market__in=universo.MERCADOS_DO_UNIVERSO
        ).exists(),
    }


def _fragmento(request) -> bool:
    return request.GET.get("fragmento") == "1"


# --- Visão geral --------------------------------------------------------------------------------


@login_required
def visao_geral(request):
    lista = _market_scope(request, analise.leituras())
    ctx = _base(request, "geral")
    ctx.update(
        {
            "resumo": analise.resumo_mercado(lista),
            "favoritos_resumo": [x for x in lista if x.asset.pk in ctx["favoritos_ids"]][:6],
            "alertas_resumo": ativos_com_alerta(),
            "painel": resumo_mod.montar(lista, ctx["favoritos_ids"]),
            "top10": analise.top10([x for x in lista if analise._passa_tipo(x, "acao")]),
            "ranking": analise.ranking(lista, "1d"),
            "painel_dolar": dolar.painel(dias_grafico=30),
            "total": len(lista),
        }
    )
    template = "mercado/_geral.html" if _fragmento(request) else "mercado/geral.html"
    return render(request, template, ctx)


# --- Rankings -----------------------------------------------------------------------------------------


@login_required
def rankings(request):
    periodo = _periodo(request)
    tipo = request.GET.get("tipo", "todos")
    if tipo not in (*analise.TIPOS_FILTRO, "favoritos"):
        tipo = "todos"
    lista = _market_scope(request, analise.leituras())
    ctx = _base(request, "rankings")
    r = analise.ranking(lista, periodo, tipo, ctx["favoritos_ids"])
    ctx.update(
        {
            "periodo": periodo,
            "periodo_rotulo": ROTULOS_PERIODO[periodo],
            "tipo": tipo,
            "tipos": [*analise.TIPOS_FILTRO.items(), ("favoritos", "⭐ Favoritos")],
            "altas": r["altas"][:20],
            "quedas": r["quedas"][:20],
            "sem_dado": r["sem_dado"],
        }
    )
    template = "mercado/_rankings.html" if _fragmento(request) else "mercado/rankings.html"
    return render(request, template, ctx)


# --- Oportunidades --------------------------------------------------------------------------------------


@login_required
def oportunidades(request):
    lista = analise.leituras()
    ctx = _base(request, "oportunidades")
    tipo = request.GET.get("tipo", "acao")
    if tipo not in ("acao", "etf", "cripto"):
        tipo = "acao"
    lista = [x for x in lista if analise._passa_tipo(x, tipo)]
    ctx.update({"tipo": tipo, "tipos_avaliacao": [("acao", "Ações"), ("etf", "ETFs"), ("cripto", "Criptomoedas")]})
    risco = request.GET.get("risco", "")
    perspectiva = request.GET.get("perspectiva", "")
    todos = analise.pontuados(lista)
    if tipo in ("cripto", "etf"):
        todos.sort(key=lambda x: (-x.score.por_grupo()["tecnico"][0], x.asset.symbol))
    if risco in ("baixo", "médio", "alto"):
        todos = [x for x in todos if x.score.risco == risco]
    if perspectiva in ("curto prazo", "médio/longo prazo"):
        todos = [x for x in todos if x.score.perspectiva == perspectiva]
    ctx.update(
        {
            "top10": analise.top10([x for x in lista if analise._passa_tipo(x, "acao")]),
            "todos": todos,
            "risco": risco,
            "perspectiva": perspectiva,
            "fora_top10": sum(1 for x in lista if x.score and not x.score.elegivel_top10),
        }
    )
    template = "mercado/_oportunidades.html" if _fragmento(request) else "mercado/oportunidades.html"
    return render(request, template, ctx)


# --- Dólar ------------------------------------------------------------------------------------------------


@login_required
def painel_dolar(request):
    try:
        dias = max(30, min(int(request.GET.get("dias", 365)), 730))
    except ValueError:
        dias = 365
    p = dolar.painel(dias_grafico=dias)
    asset = dolar.ativo_dolar()
    from apps.alerts.models import Rule

    ctx = _base(request, "dolar")
    ctx.update(
        {
            "p": p,
            "dias": dias,
            "dolar": asset,
            "regras_dolar": Rule.objects.filter(asset=asset, is_active=True),
            "serie_json": [{"data": d.isoformat(), "valor": str(v)} for d, v in p.serie],
            "intradia_json": [{"t": t.isoformat(), "valor": str(v)} for t, v in p.intradia],
            "focus_json": [
                {"data": f.data.isoformat(), "mediana": str(f.mediana)}
                for f in p.focus_historico
                if f.mediana
            ],
        }
    )
    template = "mercado/_dolar.html" if _fragmento(request) else "mercado/dolar.html"
    return render(request, template, ctx)


# --- Favoritos ---------------------------------------------------------------------------------------------


def _favoritos_ctx(request) -> dict:
    from apps.alerts.models import Rule

    periodo = _periodo(request)
    grupo_id = request.GET.get("grupo", "")
    favs = Favorito.objects.filter(user=request.user).select_related("asset").prefetch_related("grupos")
    if grupo_id.isdigit():
        favs = favs.filter(grupos__id=int(grupo_id))
    por_asset = {f.asset_id: f for f in favs}
    lista = [x for x in analise.leituras() if x.asset.pk in por_asset]
    # favoritos recém-adicionados ainda sem leitura no cache
    faltam = [f.asset for f in por_asset.values() if f.asset_id not in {x.asset.pk for x in lista}]
    lista += [analise.leitura_do_ativo(a) for a in faltam if a.market != Market.FX]
    lista.sort(key=lambda x: (por_asset[x.asset.pk].ordem, x.asset.symbol))
    dia = [(x, x.variacao(periodo)) for x in lista if x.variacao(periodo) is not None]
    melhor = max(dia, key=lambda t: t[1]) if dia else None
    pior = min(dia, key=lambda t: t[1]) if dia else None
    elegiveis = [x for x in lista if x.score and x.score.elegivel_top10]
    melhor_op = max(elegiveis, key=lambda x: x.score.total) if elegiveis else None
    ids = list(por_asset)
    regras = Rule.objects.filter(is_active=True, asset_id__in=ids).count()
    return {
        "periodo": periodo,
        "periodo_rotulo": ROTULOS_PERIODO[periodo],
        "lista": lista,
        "fav_por_asset": por_asset,
        "grupos": GrupoFavoritos.objects.filter(user=request.user),
        "grupo_id": grupo_id,
        "melhor": melhor,
        "pior": pior,
        "melhor_op": melhor_op,
        "regras_ativas": regras,
        "analise": analise.analisar_favoritos(lista),
        "ids_comparar": [x.asset.pk for x in lista if x.tem_dados][:6],
    }


@login_required
def favoritos(request):
    ctx = _base(request, "favoritos")
    ctx.update(_favoritos_ctx(request))
    template = "mercado/_favoritos.html" if _fragmento(request) else "mercado/favoritos.html"
    return render(request, template, ctx)


@login_required
@require_POST
def alternar_favorito(request, pk):
    asset = get_object_or_404(Asset, pk=pk)
    fav = Favorito.objects.filter(user=request.user, asset=asset).first()
    if fav:
        fav.delete()
        ativo = False
    else:
        ordem = Favorito.objects.filter(user=request.user).count()
        Favorito.objects.create(user=request.user, asset=asset, ordem=ordem)
        ativo = True
    if request.headers.get("X-Requested-With"):
        return JsonResponse({"favorito": ativo, "simbolo": asset.symbol})
    messages.success(request, f"{asset.symbol} {'adicionado aos' if ativo else 'removido dos'} favoritos.")
    return redirect(request.POST.get("next") or "favoritos")


@login_required
@require_POST
def adicionar_favorito(request):
    simbolo = (request.POST.get("simbolo") or "").strip().upper()
    mercado = request.POST.get("mercado") or Market.NASDAQ
    if (
        mercado not in universo.MERCADOS_DO_UNIVERSO
        or not simbolo.replace(".", "").replace("-", "").isalnum()
    ):
        messages.error(request, "Informe um ticker válido (ex.: AAPL, SPY, ETH) e o mercado.")
        return redirect("favoritos")
    with transaction.atomic():
        asset, criado = universo.garantir_ativo(
            simbolo, mercado, request.POST.get("nome", "").strip()[:120], etf=bool(request.POST.get("etf"))
        )
        ordem = Favorito.objects.filter(user=request.user).count()
        fav, novo = Favorito.objects.get_or_create(user=request.user, asset=asset, defaults={"ordem": ordem})
        grupo = request.POST.get("grupo", "")
        if grupo.isdigit():
            g = GrupoFavoritos.objects.filter(user=request.user, pk=int(grupo)).first()
            if g:
                fav.grupos.add(g)
    analise.invalidar_cache()
    extra = " O histórico de 1 ano é carregado pelo coletor nos próximos minutos." if criado else ""
    messages.success(request, f"{asset.symbol} {'adicionado' if novo else 'já estava'} nos favoritos.{extra}")
    return redirect("favoritos")


@login_required
@require_POST
def grupo_criar(request):
    nome = (request.POST.get("nome") or "").strip()[:40]
    if nome:
        GrupoFavoritos.objects.get_or_create(
            user=request.user,
            nome=nome,
            defaults={"ordem": GrupoFavoritos.objects.filter(user=request.user).count()},
        )
    return redirect("favoritos")


@login_required
@require_POST
def grupo_excluir(request, pk):
    GrupoFavoritos.objects.filter(user=request.user, pk=pk).delete()  # favoritos continuam, só perdem o grupo
    return redirect("favoritos")


@login_required
@require_POST
def favorito_grupo(request, pk):
    """Liga/desliga um favorito em um grupo (chips na linha)."""
    fav = get_object_or_404(Favorito, user=request.user, pk=pk)
    grupo = get_object_or_404(GrupoFavoritos, user=request.user, pk=int(request.POST.get("grupo", 0) or 0))
    if fav.grupos.filter(pk=grupo.pk).exists():
        fav.grupos.remove(grupo)
        no_grupo = False
    else:
        fav.grupos.add(grupo)
        no_grupo = True
    if request.headers.get("X-Requested-With"):
        return JsonResponse({"no_grupo": no_grupo})
    return redirect("favoritos")


@login_required
@require_POST
def favorito_mover(request, pk):
    """Sobe/desce um favorito na lista (organização manual)."""
    favs = list(Favorito.objects.filter(user=request.user).order_by("ordem", "asset__symbol"))
    idx = next((i for i, f in enumerate(favs) if f.pk == pk), None)
    if idx is not None:
        alvo = idx - 1 if request.POST.get("direcao") == "cima" else idx + 1
        if 0 <= alvo < len(favs):
            favs[idx], favs[alvo] = favs[alvo], favs[idx]
        for i, f in enumerate(favs):
            if f.ordem != i:
                f.ordem = i
                f.save(update_fields=["ordem"])
    return redirect(request.POST.get("next") or "favoritos")


# --- Universo -------------------------------------------------------------------------------------------


@login_required
@require_POST
def semear(request):
    r = universo.semear_universo(request.user)
    analise.invalidar_cache()
    messages.success(
        request,
        f"Lista sugerida carregada: {len(r['criados'])} ativo(s) novo(s), "
        f"{len(r['existentes'])} já existiam. "
        "O coletor carrega 1 ano de histórico de cada um nos próximos minutos (rodízio dentro das cotas).",
    )
    return redirect(request.POST.get("next") or "mercado")


@login_required
def universo_lista(request):
    ativos = list(universo.ativos_do_universo())
    estados = {
        (e.asset_id, e.kind): e for e in RefreshState.objects.filter(asset__in=ativos, kind__in=("candles",))
    }
    ctx = _base(request, "universo")
    ctx.update(
        {
            "ativos": [(a, estados.get((a.pk, "candles"))) for a in ativos],
            "mercados": [(m, n) for m, n in Market.choices if m in universo.MERCADOS_DO_UNIVERSO],
        }
    )
    return render(request, "mercado/universo.html", ctx)


@login_required
@require_POST
def universo_remover(request, pk):
    asset = get_object_or_404(Asset, pk=pk, acompanhamento=True)
    if asset.has_position:
        messages.error(request, f"{asset.symbol} tem compras: não pode sair do universo.")
    else:
        asset.is_active = False
        asset.save(update_fields=["is_active", "updated_at"])
        analise.invalidar_cache()
        messages.success(request, f"{asset.symbol} saiu do universo (histórico mantido; reative em Ativos).")
    return redirect("mercado_universo")


@login_required
@require_POST
def atualizar_leituras(request):
    analise.invalidar_cache()
    return JsonResponse({"ok": True, "em": dt.datetime.now(dt.UTC).isoformat()})


@login_required
@require_POST
def universo_adicionar(request):
    simbolo = (request.POST.get("simbolo") or "").strip().upper()
    mercado = request.POST.get("mercado") or Market.NASDAQ
    if (
        mercado not in universo.MERCADOS_DO_UNIVERSO
        or not simbolo.replace(".", "").replace("-", "").isalnum()
    ):
        messages.error(request, "Informe um ticker válido e o mercado.")
        return redirect("mercado_universo")
    asset, criado = universo.garantir_ativo(
        simbolo,
        mercado,
        request.POST.get("nome", "").strip()[:120],
        request.POST.get("setor", "").strip()[:80],
        bool(request.POST.get("etf")),
    )
    analise.invalidar_cache()
    messages.success(request, f"{asset.symbol} {'incluído no' if criado else 'já estava no'} universo.")
    return redirect("mercado_universo")


# --- Ações EUA / Ações Brasil / Comparar mercados (rc8) ------------------------------------------------


def _faltam(iniciais) -> list[str]:
    existentes = set(
        Asset.objects.filter(is_active=True, symbol__in=[i[0] for i in iniciais]).values_list(
            "symbol", flat=True
        )
    )
    return [i[0] for i in iniciais if i[0] not in existentes]


def _contexto_acoes(request, mercados, iniciais, moeda_padrao: str) -> dict:
    from . import mercados as mk

    periodo = _periodo(request)
    moeda = request.GET.get("moeda", moeda_padrao)
    if moeda not in ("USD", "BRL"):
        moeda = moeda_padrao
    lista = mk.leituras_do_mercado(mercados)
    cambio = mk.Cambio.carregar(
        [x.ind.periodos[periodo].base_data for x in lista if x.ind and x.ind.periodos[periodo].base_data]
    )
    linhas = mk.linhas(lista, periodo, moeda, cambio)
    altas, quedas, sem = mk.ranking(linhas)
    grupos = mk.por_setor(lista)
    setor = request.GET.get("setor", "")
    if not setor and grupos:
        setor = next((g.setor for g in grupos if len(g.membros) > 1), grupos[0].setor)
    resultados, proventos = mk.calendario(lista)
    return {
        "periodo": periodo,
        "periodo_rotulo": ROTULOS_PERIODO[periodo],
        "moeda": moeda,
        "cambio": cambio,
        "linhas": sorted(linhas, key=lambda li: li.x.asset.symbol),
        "altas": altas[:10],
        "quedas": quedas[:10],
        "sem_dado": sem,
        "perto_minima": mk.perto_da_minima(lista),
        "perto_minima_parcial": mk.perto_da_minima(lista, completa=False),
        "grupos": grupos,
        "setor": setor,
        "grupo_setor": next((g for g in grupos if g.setor == setor), None),
        "metricas_setor": mk.METRICAS_SETOR,
        "resultados": resultados,
        "proventos": proventos,
        "noticias": mk.noticias(lista),
        "faltam_iniciais": _faltam(iniciais),
        "total": len(lista),
    }


@login_required
def acoes_eua(request):
    from . import mercados as mk

    ctx = _base(request, "eua")
    ctx.update(_contexto_acoes(request, mk.EUA, mk.INICIAIS_EUA, "USD"))
    ctx["mercado_nome"] = "EUA"
    template = "mercado/_acoes.html" if _fragmento(request) else "mercado/acoes.html"
    return render(request, template, ctx)


@login_required
def acoes_brasil(request):
    from apps.marketdata.adapters import chave_da_fonte

    from . import mercados as mk

    ctx = _base(request, "brasil")
    ctx.update(_contexto_acoes(request, mk.BRASIL, mk.INICIAIS_BRASIL, "BRL"))
    ctx["mercado_nome"] = "Brasil"
    ctx["macro"] = mk.macro()
    ctx["sensibilidade"] = {li.x.asset.pk: mk.sensibilidade_dolar(li.x.asset) for li in ctx["linhas"]}
    sem_token = not chave_da_fonte("brapi")
    ctx["brapi_sem_token"] = sem_token
    ctx["precisam_token"] = [
        li.x.asset.symbol for li in ctx["linhas"] if sem_token and li.x.asset.symbol not in mk.SEM_TOKEN_BRAPI
    ]
    ctx["brapi_limitado"] = RefreshState.objects.filter(
        asset__market=Market.B3, kind="candles", last_error__startswith="histórico limitado"
    ).exists()
    template = "mercado/_acoes.html" if _fragmento(request) else "mercado/acoes.html"
    return render(request, template, ctx)


@login_required
@require_POST
def semear_iniciais(request, mercado):
    from . import mercados as mk

    iniciais = mk.INICIAIS_EUA if mercado == "eua" else mk.INICIAIS_BRASIL
    criados = mk.semear(iniciais)
    messages.success(
        request,
        f"{len(criados)} ativo(s) incluído(s): {', '.join(criados) or 'nenhum novo'}. "
        "O coletor carrega cotações e histórico nos próximos minutos.",
    )
    return redirect("mercado_eua" if mercado == "eua" else "mercado_brasil")


PERIODOS_COMPARACAO = ("1m", "3m", "6m", "12m")


@login_required
def comparar_mercados(request):
    from . import mercados as mk

    ctx = _base(request, "comparar")
    moeda = request.GET.get("moeda", "BRL")
    moeda = moeda if moeda in ("USD", "BRL") else "BRL"
    periodo = request.GET.get("periodo", "12m")
    periodo = periodo if periodo in PERIODOS_COMPARACAO else "12m"
    candidatos = list(
        Asset.objects.filter(is_active=True, market__in=(*mk.EUA, *mk.BRASIL, Market.CRYPTO))
        .filter(is_etf=False)
        .order_by("market", "symbol")
    )
    ids = [int(i) for i in request.GET.getlist("ativos") if i.isdigit()][:8]
    if not ids:
        padrao = [s for s, *_ in mk.INICIAIS_EUA[:4]] + [s for s, *_ in mk.INICIAIS_BRASIL[:4]]
        ids = [a.pk for a in candidatos if a.symbol in padrao][:8]
    selecionados = [a for a in candidatos if a.pk in ids]
    linhas = mk.comparar(selecionados, moeda, periodo)
    ctx.update(
        {
            "moeda": moeda,
            "periodo": periodo,
            "periodos_comp": [(p, ROTULOS_PERIODO[p]) for p in PERIODOS_COMPARACAO],
            "candidatos": candidatos,
            "ids": ids,
            "linhas": linhas,
            "series": mk.series_json(linhas),
            "series_nativas": mk.series_json(linhas, nativa=True),
            "cambio": mk.Cambio.carregar(),
        }
    )
    template = "mercado/_comparar.html" if _fragmento(request) else "mercado/comparar.html"
    return render(request, template, ctx)


@login_required
def criptomoedas(request):
    periodo = _periodo(request)
    lista = [x for x in analise.leituras() if x.asset.market == Market.CRYPTO]
    ctx = _base(request, "cripto")
    ranking = analise.ranking(lista, periodo)
    ctx.update({
        "lista": lista, "periodo": periodo, "periodo_rotulo": ROTULOS_PERIODO[periodo],
        "altas": ranking["altas"], "quedas": ranking["quedas"], "sem_dado": ranking["sem_dado"],
    })
    return render(request, "mercado/_cripto.html" if _fragmento(request) else "mercado/cripto.html", ctx)
