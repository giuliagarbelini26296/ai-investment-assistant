from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.portfolio import services
from apps.portfolio.conferencia import compras_para_conferir

MOEDA_PADRAO = "BRL"


def moeda_da_sessao(request) -> str:
    return request.session.get("moeda_visao", MOEDA_PADRAO)


def _contexto_carteira(request) -> dict:
    """Contexto compartilhado pela página inteira e pelo fragmento recarregado ao vivo."""
    from django.utils import timezone

    from apps.alerts import services as alert_services

    from . import resumo

    moeda = moeda_da_sessao(request)
    agora = timezone.now()
    visao = services.visao_da_carteira(moeda, agora)
    outra = "USD" if moeda == "BRL" else "BRL"
    taxa_outra = None
    if visao.cambio:
        from apps.valuation import fx

        taxa_outra = fx.taxa_para_visao(moeda, outra, visao.cambio.valor)
    ultima = None
    for linha in visao.linhas:
        linha.sparkline = resumo.sparkline_svg(resumo.fechamentos_recentes(linha.asset))
        linha.dia_valor = resumo.resultado_do_dia(linha)
        q = linha.atualidade.cotacao if linha.atualidade else None
        if q is not None and (ultima is None or q.collected_at > ultima):
            ultima = q.collected_at
    from apps.marketdata.adapters import fonte_sem_chave

    sem_chave: dict[str, list[str]] = {}
    for linha in visao.linhas:
        variavel = fonte_sem_chave(linha.asset.market)
        if variavel:
            sem_chave.setdefault(variavel, []).append(linha.asset.symbol)
    from apps.mercado.analise import ids_favoritos

    return {
        "favoritos_ids": ids_favoritos(request.user),
        "compras_conferir": compras_para_conferir(),
        "fontes_sem_chave": sem_chave,
        "visao": visao,
        "moeda": moeda,
        "outra_moeda": outra,
        "taxa_outra": taxa_outra,
        "dia": resumo.resumo_do_dia(visao),
        "alocacao": resumo.alocacao(visao),
        "ultima_coleta": ultima,
        "agora": agora,
        "alertas_ativos": len(alert_services.ativos_com_alerta()),
        "alertas_nao_vistos": alert_services.nao_vistos(),
    }


@login_required
def home(request):
    from apps.noticias.views import highlights

    context = _contexto_carteira(request)
    context["noticias_destaques"] = highlights(request.user)
    return render(request, "ui/home.html", context)


@login_required
def carteira_fragmento(request):
    """Miolo da carteira (cards, gráficos e tabela) para a atualização ao vivo."""
    return render(request, "ui/_carteira.html", _contexto_carteira(request))


@login_required
def evolucao_json(request):
    from . import resumo

    try:
        dias = max(7, min(int(request.GET.get("dias", 90)), 730))
    except ValueError:
        dias = 90
    ev = resumo.evolucao_patrimonio(moeda_da_sessao(request), dias)
    return JsonResponse(resumo.evolucao_como_json(ev))


@login_required
@require_POST
def coletar_agora(request):
    """Pede ao coletor que rode as tarefas de cotação na próxima passagem (≤ 30 s)."""
    from apps.marketdata import collector

    n = collector.solicitar_coleta()
    if request.headers.get("Accept", "").startswith("application/json") or request.headers.get(
        "X-Requested-With"
    ):
        return JsonResponse({"solicitadas": n})
    from django.contrib import messages

    messages.success(request, "Coleta solicitada; o coletor roda em até 30 segundos.")
    return redirect("home")


@login_required
@require_POST
def trocar_moeda(request):
    moeda = request.POST.get("moeda", MOEDA_PADRAO)
    if moeda in services.MOEDAS_VISAO:
        request.session["moeda_visao"] = moeda
    return redirect(request.POST.get("next") or "home")


@login_required
def sobre_dados(request):
    from django.utils import timezone

    from apps.marketdata import budget
    from apps.marketdata.adapters import (
        FONTE_CAMBIO_FECHAMENTO,
        FONTE_CAMBIO_INTRADIARIO,
        FONTE_POR_MERCADO,
        todas_as_fontes,
    )
    from apps.marketdata.calendars import calendario
    from apps.marketdata.models import CollectionRun, ScheduleState

    agora = timezone.now()
    fontes = []
    from apps.marketdata.adapters import FONTES_INTELIGENCIA, chave_ausente

    em_uso = set(FONTE_POR_MERCADO.values()) | {FONTE_CAMBIO_FECHAMENTO, FONTE_CAMBIO_INTRADIARIO}
    em_uso |= {c for c in FONTES_INTELIGENCIA if not chave_ausente(c)}
    papeis_inteligencia = {
        "finnhub": "Mercado: analistas, preço-alvo, notícias, resultados, métricas",
        "alphavantage": "Mercado: ficha da empresa e preço-alvo (1x/semana por ação)",
        "focus_bcb": "Dólar e Brasil: expectativas Focus (câmbio, Selic, IPCA)",
        "sgs_bcb": "Ações Brasil: Selic meta e IPCA",
    }
    for info in todas_as_fontes():
        if info.codigo.startswith("demo") and not settings.PORTAL["MODO_DEMO"]:
            continue
        papel = [m for m, f in FONTE_POR_MERCADO.items() if f == info.codigo]
        if info.codigo == FONTE_CAMBIO_FECHAMENTO:
            papel.append("câmbio fechamento (PTAX)")
        if info.codigo == FONTE_CAMBIO_INTRADIARIO:
            papel.append("câmbio intradiário")
        if info.codigo in papeis_inteligencia:
            falta = chave_ausente(info.codigo)
            papel.append(papeis_inteligencia[info.codigo] + (f" — sem chave ({falta})" if falta else ""))
        if info.codigo.startswith("demo"):
            papel = ["todos os mercados (modo demonstração)"]
        from apps.marketdata.models import QuoteType

        fontes.append(
            {
                "info": info,
                "tipo": dict(QuoteType.choices).get(info.tipo_dado, info.tipo_dado),
                "papel": ", ".join(papel) or "alternativa",
                "em_uso": info.codigo in em_uso or info.codigo.startswith("demo"),
                "saldo": budget.saldo(info, agora),
            }
        )
    calendarios = []
    for mercado in ("NYSE", "B3", "CRIPTO"):
        cal = calendario(mercado)
        sit = cal.situacao(agora)
        calendarios.append(
            {
                "mercado": mercado if mercado != "NYSE" else "NYSE / Nasdaq",
                "estado": sit.estado,
                "sessao": sit.sessao,
                "anos": sorted(getattr(cal, "anos_cobertos", [])) or ["24/7"],
                "fuso": str(getattr(cal, "tz", "UTC")),
            }
        )
    tarefas = ScheduleState.objects.order_by("task")
    execucoes = CollectionRun.objects.order_by("-started_at")[:15]
    return render(
        request,
        "ui/sobre_dados.html",
        {
            "fontes": fontes,
            "calendarios": calendarios,
            "tarefas": tarefas,
            "execucoes": execucoes,
            "agora": agora,
        },
    )


def health(request):
    """Verificação simples usada pelo Compose e por monitoramento local."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()
    return JsonResponse({"status": "ok", "versao": settings.PORTAL["VERSAO"], "banco": "ok"})
