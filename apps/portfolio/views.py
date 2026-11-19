from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.marketdata import history
from apps.valuation import core

from . import services
from .conferencia import compras_para_conferir
from .forms import AssetForm, LotForm
from .models import Asset, AssetClass, Custody, Lot, Market

FILTROS_SITUACAO = {
    "lucro": "Em lucro",
    "prejuizo": "Em prejuízo",
    "sem_preco": "Sem cotação",
    "sem_posicao": "Sem compras",
}


def _aplicar_filtros(linhas, params):
    q = (params.get("q") or "").strip().lower()
    classe = params.get("classe") or ""
    mercado = params.get("mercado") or ""
    custodia = params.get("custodia") or ""
    setor = (params.get("setor") or "").strip().lower()
    situacao = params.get("situacao") or ""
    ver = params.get("ver") or ""
    saida = []
    for linha in linhas:
        a = linha.asset
        # ativos só acompanhados (universo do Mercado/Favoritos) ficam fora da lista padrão
        if ver != "todos" and a.acompanhamento and linha.posicao is None and not params.get("q"):
            continue
        if q and q not in a.symbol.lower() and q not in a.name.lower():
            continue
        if classe and a.asset_class != classe:
            continue
        if mercado and a.market != mercado:
            continue
        if custodia and a.custody != custodia:
            continue
        if setor and setor not in (a.sector or "").lower():
            continue
        if situacao == "lucro" and not (linha.avaliacao and linha.avaliacao.resultado > 0):
            continue
        if situacao == "prejuizo" and not (linha.avaliacao and linha.avaliacao.resultado < 0):
            continue
        if situacao == "sem_preco" and not (linha.posicao and linha.avaliacao is None):
            continue
        if situacao == "sem_posicao" and linha.posicao is not None:
            continue
        saida.append(linha)
    return saida


@login_required
def asset_list(request):
    from apps.ui.views import moeda_da_sessao

    moeda = moeda_da_sessao(request)
    visao = services.visao_da_carteira(moeda)
    todas = services.linhas_da_carteira(moeda_visao=moeda, cambio=visao.cambio)
    linhas = _aplicar_filtros(todas, request.GET)
    setores = sorted({a.sector for a in Asset.objects.filter(is_active=True) if a.sector})
    ctx = {
        "linhas": linhas,
        "total": len(todas),
        "moeda": moeda,
        "filtros": request.GET,
        "setores": setores,
        "situacoes": FILTROS_SITUACAO,
        "classes": AssetClass.choices,
        "mercados": Market.choices,
        "custodias": Custody.choices,
        "comparar_ids": [str(linha.asset.pk) for linha in linhas if linha.posicao][:6],
        "acompanhados": sum(1 for linha in todas if linha.asset.acompanhamento and linha.posicao is None),
    }
    return render(request, "portfolio/asset_list.html", ctx)


@login_required
def comparar(request):
    from apps.ui.views import moeda_da_sessao

    moeda = moeda_da_sessao(request)
    ids = [i for i in request.GET.getlist("ativos") if i.isdigit()]
    periodo = request.GET.get("periodo", "6m")
    if periodo not in history.PERIODOS:
        periodo = "6m"
    visao = services.visao_da_carteira(moeda)
    linhas = [
        linha
        for linha in services.linhas_da_carteira(moeda_visao=moeda, cambio=visao.cambio)
        if str(linha.asset.pk) in ids
    ]
    todos = Asset.objects.filter(is_active=True).exclude(market="CAMBIO").order_by("symbol")
    ctx = {
        "linhas": linhas,
        "moeda": moeda,
        "periodo": periodo,
        "periodos": list(history.PERIODOS),
        "todos": todos,
        "ids": ids,
    }
    return render(request, "portfolio/comparar.html", ctx)


@login_required
def historico_json(request, pk):
    from apps.ui.views import moeda_da_sessao

    asset = get_object_or_404(Asset, pk=pk)
    periodo = request.GET.get("periodo", "1m")
    if periodo not in history.PERIODOS:
        return JsonResponse({"erro": "período inválido"}, status=400)
    serie = history.serie_historica(asset, periodo, moeda_da_sessao(request))
    return JsonResponse(history.como_json(serie))


@login_required
def asset_create(request):
    form = AssetForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        asset = form.save()
        messages.success(request, f"Ativo {asset.symbol} cadastrado.")
        if request.GET.get('compra') == '1':
            from django.urls import reverse
            return redirect(f'{reverse("quick_purchase")}?ativo={asset.pk}')
        return redirect("asset_detail", pk=asset.pk)
    return render(request, "portfolio/asset_form.html", {"form": form, "titulo": "Novo ativo"})


@login_required
def asset_update(request, pk):
    asset = get_object_or_404(Asset, pk=pk)
    form = AssetForm(request.POST or None, instance=asset)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Ativo {asset.symbol} atualizado.")
        return redirect("asset_detail", pk=asset.pk)
    return render(
        request,
        "portfolio/asset_form.html",
        {"form": form, "titulo": f"Editar {asset.symbol}", "asset": asset},
    )


@login_required
def asset_detail(request, pk):
    from django.utils import timezone

    from apps.marketdata import freshness, fxservice
    from apps.ui.views import moeda_da_sessao
    from apps.valuation import fx

    asset = get_object_or_404(Asset, pk=pk)
    agora = timezone.now()
    lotes = list(asset.lots.select_related("created_by").prefetch_related("revisions"))
    for lot in lotes:
        if not lot.is_cancelled and asset.native_currency == "USD":
            fxservice.resolver_cambio_lote(lot)  # grava origem/câmbio resolvido para exibição
    pos = services.posicao_do_ativo(asset)
    balance = asset.position_balance
    if balance:
        for lot in lotes:
            if lot.pk in balance.covered_lot_ids and not lot.is_cancelled:
                lot.notes += " [Abrangida pelo saldo conciliado; não somar novamente.]"
    atual = freshness.atualidade(asset, agora)
    aval = (
        core.avaliar(pos, atual.cotacao.price, atual.cotacao.previous_close)
        if pos and atual.cotacao
        else None
    )
    moeda = moeda_da_sessao(request)
    cambio = fxservice.cambio_atual(agora)
    conversao = None
    sem_base = False
    if pos and moeda != asset.native_currency:
        lotes_fx = services.lotes_com_cambio(asset, lotes)
        sem_base = any(item.cambio is None for item in lotes_fx)
        if aval and cambio and not sem_base:
            conversao = fx.converter(lotes_fx, pos, aval, asset.native_currency, moeda, cambio.valor)
    ctx = {
        "asset": asset,
        "lotes": lotes,
        "compras_conferir": compras_para_conferir(lotes),
        "posicao": pos,
        "atualidade": atual,
        "avaliacao": aval,
        "moeda": moeda,
        "cambio": cambio,
        "conversao": conversao,
        "sem_base": sem_base,
        "preco_medio": core.arredondar_moeda(pos.preco_medio) if pos else None,
        "investido": core.arredondar_moeda(pos.investido) if pos else None,
        "periodos": list(history.PERIODOS),
        "tem_historico": history.fonte_principal(asset) is not None,
        "linhas_grafico": _linhas_do_grafico(asset, pos),
    }
    if asset.market != "CAMBIO":
        from apps.mercado import analise

        leitura = analise.leitura_do_ativo(asset, agora)
        ctx.update(
            {
                "x": leitura,
                "leitura_exec": analise.leitura_executiva(leitura),
                "favoritos_ids": analise.ids_favoritos(request.user),
            }
        )
    return render(request, "portfolio/asset_detail.html", ctx)


def _dados_lote(form) -> dict:
    """Campos do modelo (sem a confirmação de plausibilidade, que só existe no formulário)."""
    return {k: v for k, v in form.cleaned_data.items() if k != "confirmar_valores"}


def _linhas_do_grafico(asset, pos) -> list[dict]:
    """Níveis desenhados no gráfico (moeda nativa): preço médio e limites de regras de preço absoluto."""
    from apps.alerts import services as alert_services

    linhas = []
    if pos:
        linhas.append(
            {"rotulo": "preço médio", "valor": str(core.arredondar_moeda(pos.preco_medio)), "tipo": "medio"}
        )
    for versao in alert_services.versoes_aplicaveis(asset):
        if versao.family == "preco_absoluto" and versao.currency == asset.native_currency:
            linhas.append(
                {
                    "rotulo": f"alerta: {versao.descricao}",
                    "valor": str(versao.threshold),
                    "tipo": "abaixo" if versao.condition == "abaixo" else "acima",
                    "regra": versao.rule_id,
                }
            )
    return linhas


@login_required
def lot_create(request, pk):
    asset = get_object_or_404(Asset, pk=pk)
    form = LotForm(request.POST or None, asset=asset)
    if request.method == "POST" and form.is_valid():
        services.criar_lote(asset, request.user, **_dados_lote(form))
        messages.success(request, "Compra registrada.")
        return redirect("asset_detail", pk=asset.pk)
    return render(request, "portfolio/lot_form.html", {"form": form, "asset": asset, "titulo": "Nova compra"})


@login_required
def lot_update(request, pk):
    lot = get_object_or_404(Lot.objects.select_related("asset"), pk=pk)
    if lot.is_cancelled:
        messages.error(request, "Compra cancelada não pode ser alterada.")
        return redirect("asset_detail", pk=lot.asset_id)
    form = LotForm(request.POST or None, instance=lot, asset=lot.asset)
    if request.method == "POST" and form.is_valid():
        services.alterar_lote(lot, request.user, reason=request.POST.get("motivo", ""), **_dados_lote(form))
        messages.success(request, f"Compra alterada (revisão {lot.revision}).")
        return redirect("asset_detail", pk=lot.asset_id)
    return render(
        request,
        "portfolio/lot_form.html",
        {"form": form, "asset": lot.asset, "lot": lot, "titulo": "Editar compra"},
    )


@login_required
@require_POST
def lot_cancel(request, pk):
    lot = get_object_or_404(Lot.objects.select_related("asset"), pk=pk)
    services.cancelar_lote(lot, request.user, reason=request.POST.get("motivo", ""))
    messages.success(request, "Compra cancelada (mantida no histórico).")
    return redirect("asset_detail", pk=lot.asset_id)


# --- Importação de compras por CSV ---------------------------------------------------------------


@login_required
def importar_compras(request):
    """Etapa 1: envia o CSV e vê a pré-visualização; etapa 2: confirma (dados ficam na sessão)."""
    from django.core.exceptions import ValidationError

    from . import importacao

    chave = "importacao_pendente"
    if request.method == "POST" and request.POST.get("acao") == "confirmar":
        dados = request.session.pop(chave, None)
        if not dados:
            messages.error(request, "Nada a confirmar: envie o arquivo novamente.")
            return redirect("importar_compras")
        try:
            compras, ativos = importacao.confirmar(dados, request.user)
        except ValidationError as exc:
            messages.error(request, f"Importação cancelada: {exc.messages[0] if exc.messages else exc}")
            return redirect("importar_compras")
        messages.success(
            request,
            f"{compras} compra(s) importada(s)" + (f" e {ativos} ativo(s) criado(s)." if ativos else "."),
        )
        return redirect("home")
    if request.method == "POST" and request.POST.get("acao") == "cancelar":
        request.session.pop(chave, None)
        return redirect("importar_compras")
    previa = None
    if request.method == "POST" and request.FILES.get("arquivo"):
        arquivo = request.FILES["arquivo"]
        if arquivo.size > 2 * 1024 * 1024:
            messages.error(request, "Arquivo acima de 2 MB.")
            return redirect("importar_compras")
        previa = importacao.pre_visualizar(arquivo.read(), request.POST.get("custodia") or Custody.OTHER)
        if previa.validas:
            request.session[chave] = importacao.serializar(previa)
        else:
            request.session.pop(chave, None)
    return render(
        request,
        "portfolio/importar.html",
        {"previa": previa, "custodias": Custody.choices, "pendente": chave in request.session},
    )


@login_required
def asset_painel(request, pk):
    """Fragmento do painel expansível da carteira: posição, compras ativas, alertas e atalhos."""
    from django.utils import timezone

    from apps.alerts import services as alert_services
    from apps.alerts.models import Alert
    from apps.marketdata import freshness

    asset = get_object_or_404(Asset, pk=pk)
    agora = timezone.now()
    lotes = [lot for lot in asset.lots.all() if not lot.is_cancelled]
    pos = services.posicao_do_ativo(asset)
    balance = asset.position_balance
    if balance:
        for lot in lotes:
            if lot.pk in balance.covered_lot_ids and not lot.is_cancelled:
                lot.notes += " [Abrangida pelo saldo conciliado; não somar novamente.]"
    atual = freshness.atualidade(asset, agora)
    aval = (
        core.avaliar(pos, atual.cotacao.price, atual.cotacao.previous_close)
        if pos and atual.cotacao
        else None
    )
    ctx = {
        "asset": asset,
        "lotes": lotes,
        "compras_conferir": compras_para_conferir(lotes),
        "posicao": pos,
        "atualidade": atual,
        "avaliacao": aval,
        "regras": alert_services.versoes_aplicaveis(asset),
        "alertas": Alert.objects.filter(asset=asset, resolved_at__isnull=True).select_related("version")[:5],
        "tem_historico": history.fonte_principal(asset) is not None,
        "linhas_grafico": _linhas_do_grafico(asset, pos),
    }
    return render(request, "portfolio/_painel.html", ctx)
