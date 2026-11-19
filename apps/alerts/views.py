import json
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from . import notify, services
from .forms import ATALHOS, TIPOS, AlertaForm
from .models import TEXTO_NAO_RECOMENDACAO, Alert, Notification, Rule, RuleState


def _contexto_painel() -> dict:
    agora = timezone.now()
    ativos = list(services.alertas_ativos())
    for a in ativos:
        a.esta_silenciado = a.silenciado(agora)
    historico = Alert.objects.filter(resolved_at__isnull=False).select_related(
        "asset", "version", "version__rule"
    )[:50]
    regras = Rule.objects.select_related("asset").prefetch_related("versions")
    suspensos = RuleState.objects.filter(suspended=True, version__rule__is_active=True).select_related(
        "asset", "version", "version__rule"
    )
    return {
        "ativos": ativos,
        "nao_vistos": services.nao_vistos(agora),
        "historico": historico,
        "regras": regras,
        "suspensos": suspensos,
        "texto": TEXTO_NAO_RECOMENDACAO,
        "agora": agora,
        "canais": notify.canais_ligados(),
        "notificacoes": Notification.objects.select_related("alert", "alert__asset").order_by("-created_at")[
            :20
        ],
    }


@login_required
def painel(request):
    return render(request, "alerts/painel.html", _contexto_painel())


@login_required
def painel_fragmento(request):
    """Miolo do painel para a atualização ao vivo (portal.js)."""
    return render(request, "alerts/_painel.html", _contexto_painel())


def _resposta_acao(request, mensagem: str, ok: bool = True):
    if request.headers.get("X-Requested-With"):
        return JsonResponse({"ok": ok, "mensagem": mensagem}, status=200 if ok else 400)
    (messages.success if ok else messages.error)(request, mensagem)
    return redirect("alertas")


@login_required
@require_POST
def silenciar(request, pk):
    try:
        horas = int(request.POST.get("horas", 24))
    except ValueError:
        horas = 24
    alerta = services.silenciar(pk, horas)
    if alerta is None:
        return _resposta_acao(request, "Alerta não encontrado ou já resolvido.", ok=False)
    return _resposta_acao(
        request, f"{alerta.asset.symbol} silenciado por {horas} h (continua ativo; a regra não é alterada)."
    )


@login_required
@require_POST
def regra_limite(request, pk):
    from decimal import InvalidOperation

    from .forms import interpretar_numero

    rule = get_object_or_404(Rule, pk=pk, is_active=True)
    try:
        novo = interpretar_numero(request.POST.get("valor", ""))
        if novo <= 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        return _resposta_acao(request, "Informe um limite válido (ex.: 5 ou 95.000).", ok=False)
    v = rule.versao_atual
    if v.family != "preco_absoluto" and v.condition == "queda" and novo > 100:
        return _resposta_acao(request, "Uma queda não pode passar de 100%.", ok=False)
    from django.core.exceptions import ValidationError

    try:
        nova = services.ajustar_limite(rule, request.user, novo, reason="ajuste rápido pelo painel")
    except ValidationError as exc:
        return _resposta_acao(request, " ".join(exc.messages), ok=False)
    return _resposta_acao(
        request, f"'{rule.name}' agora é v{nova.number}: {nova.descricao}. Estado reiniciado."
    )


@login_required
@require_POST
def marcar_vistos(request):
    ids = [int(i) for i in request.POST.getlist("ids") if i.isdigit()]
    n = services.marcar_vistos(ids)
    return _resposta_acao(
        request, f"{n} alerta(s) marcado(s) como visto(s). Marcar como visto não resolve nem rearma a regra."
    )


def _contexto_form(form, titulo, rule=None):
    from apps.portfolio.models import Asset

    from .forms import UNIDADE_TIPO

    ids = {}
    for _, _, _, simbolo in ATALHOS:
        if simbolo and simbolo not in ids:
            a = Asset.objects.filter(symbol=simbolo, is_active=True).order_by("id").first()
            ids[simbolo] = a.pk if a else None
    atalhos = [
        {"rotulo": r, "tipo": t, "valor": v, "ativo": ids.get(s) or ""}
        for r, t, v, s in ATALHOS
        if not s or ids.get(s)
    ]
    return {
        "form": form,
        "titulo": titulo,
        "rule": rule,
        "atalhos": atalhos,
        "unidades": UNIDADE_TIPO,
        "canais": notify.canais_ligados(),
        "texto_nao_recomendacao": TEXTO_NAO_RECOMENDACAO,
    }


@login_required
def regra_nova(request):
    inicial = {}
    if request.GET.get("ativo", "").isdigit():
        inicial["asset"] = request.GET["ativo"]
    # pré-preenchimento vindo do gráfico do ativo ("avisar se ficar abaixo de X")
    if request.GET.get("tipo") in TIPOS:
        inicial["tipo"] = request.GET["tipo"]
    if request.GET.get("valor"):
        inicial["valor"] = request.GET["valor"][:32]
    if request.GET.get("currency") in ("USD", "BRL"):
        inicial["currency"] = request.GET["currency"]
    form = AlertaForm(request.POST or None, initial=inicial)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        rule = services.criar_regra(
            request.user, name=form.nome(), asset=d["asset"], reason=d["reason"], **form.parametros()
        )
        messages.success(request, f"Alerta '{rule.name}' criado. Ele será avaliado a cada coleta.")
        return redirect("alertas")
    return render(request, "alerts/regra_form.html", _contexto_form(form, "Novo alerta"))


@login_required
def regra_editar(request, pk):
    rule = get_object_or_404(Rule, pk=pk)
    v = rule.versao_atual
    form = AlertaForm(request.POST or None, initial=AlertaForm.inicial_de_versao(rule, v))
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        rule.name, rule.asset = form.nome(), d["asset"]
        rule.save(update_fields=["name", "asset", "updated_at"])
        nova = services.nova_versao(rule, request.user, reason=d["reason"], **form.parametros())
        messages.success(
            request, f"Alerta '{rule.name}' atualizado (versão {nova.number}); estado reiniciado."
        )
        return redirect("alertas")
    return render(
        request, "alerts/regra_form.html", _contexto_form(form, f"Editar alerta — {rule.name}", rule)
    )


@login_required
@require_POST
def regra_desativar(request, pk):
    rule = get_object_or_404(Rule, pk=pk)
    services.desativar_regra(rule)
    return _resposta_acao(
        request, f"Regra '{rule.name}' pausada; alertas ativos resolvidos, histórico preservado."
    )


@login_required
@require_POST
def regra_reativar(request, pk):
    rule = get_object_or_404(Rule, pk=pk)
    rule.is_active = True
    rule.save(update_fields=["is_active", "updated_at"])
    return _resposta_acao(request, f"Regra '{rule.name}' reativada.")


# --- Feed para o script de notificação do macOS ------------------------------------------------------


def _token_valido(request) -> bool:
    esperado = settings.NOTIFICACOES.get("FEED_TOKEN") or ""
    recebido = request.headers.get("X-Portal-Token", "")
    if not esperado or len(recebido) != len(esperado):
        return False
    # comparação em tempo constante
    import hmac

    return hmac.compare_digest(recebido, esperado)


@require_GET
def feed_mac(request):
    """Notificações pendentes do canal mac, em texto simples: id<TAB>título<TAB>mensagem por linha."""
    if not _token_valido(request):
        return HttpResponse(status=401)
    linhas = [f"{n['id']}\t{n['titulo']}\t{n['mensagem']}" for n in notify.feed_mac()]
    return HttpResponse(
        "\n".join(linhas) + ("\n" if linhas else ""), content_type="text/plain; charset=utf-8"
    )


@csrf_exempt
@require_POST
def feed_mac_confirmar(request):
    if not _token_valido(request):
        return HttpResponse(status=401)
    try:
        ids = [int(i) for i in json.loads(request.body or b"[]")]
    except (ValueError, TypeError):
        return JsonResponse({"erro": "corpo deve ser uma lista JSON de ids"}, status=400)
    return JsonResponse({"confirmadas": notify.confirmar_mac(ids)})


# --- Prévia ao criar a regra: quais ativos já disparariam e a distância até o limite ------------------


def _detalhe_mercado(v: dict, q) -> str:
    from apps.ui.templatetags.formatos import moeda, pct

    cod = q.currency if q is not None else "USD"
    preco = moeda(q.price, cod) if q is not None else "—"
    if "minima_52s" in v:
        minima = moeda(v["minima_52s"], cod)
        return f"preço {preco}; mínima de 52 semanas {minima} ({pct(v['distancia_pct'])} acima)"
    if "suporte" in v:
        return f"preço {preco}; suporte de {v['sessoes']} sessões {moeda(v['suporte'], cod)}"
    if "resistencia" in v:
        return f"preço {preco}; resistência de {v['sessoes']} sessões {moeda(v['resistencia'], cod)}"
    if "data_resultado" in v:
        d = v["data_resultado"]
        return f"próximo resultado em {d[8:10]}/{d[5:7]}/{d[:4]} ({v['dias']} dia(s); {v['fonte']})"
    if "mudanca_pp" in v:
        return (
            f"compra {v['pct_compra_anterior']}% → {v['pct_compra']}% ({v['mudanca_pp']} p.p., {v['fonte']})"
        )
    return ""


@login_required
def previa_regra(request):
    """GET ?tipo&valor&currency[&ativo] → avaliação da regra proposta contra a última cotação válida."""
    from apps.marketdata import freshness
    from apps.portfolio import services as portfolio_services
    from apps.portfolio.models import Asset
    from apps.valuation import core

    from .forms import TIPOS, interpretar_numero
    from .models import Family, RuleVersion

    tipo = request.GET.get("tipo", "")
    if tipo not in TIPOS:
        return JsonResponse({"erro": "tipo inválido"}, status=400)
    try:
        limite = interpretar_numero(request.GET.get("valor", ""))
        if limite <= 0:
            raise ValueError
    except Exception:  # noqa: BLE001
        return JsonResponse({"itens": [], "aviso": "informe um valor"})
    _, fam, cond = TIPOS[tipo]
    moeda = request.GET.get("currency") if request.GET.get("currency") in ("USD", "BRL") else "USD"
    versao = RuleVersion(
        family=fam, condition=cond, threshold=limite, currency=moeda if fam == Family.ABSOLUTE else ""
    )
    especifico = request.GET.get("ativo", "").isdigit()
    if fam == Family.MARKET and not especifico:
        return JsonResponse({"itens": [], "aviso": "sinais de mercado valem para um ativo específico"})
    ativos = Asset.objects.filter(is_active=True).exclude(market="CAMBIO")
    if especifico:
        ativos = Asset.objects.filter(is_active=True, pk=int(request.GET["ativo"]))
    agora = timezone.now()
    itens = []
    for asset in ativos:
        pos = portfolio_services.posicao_do_ativo(asset)
        if pos is None and not especifico:
            continue  # regra "qualquer ativo da carteira": só posições
        atual = freshness.atualidade(asset, agora)
        q = atual.cotacao
        precisa_posicao = fam == Family.SINCE_PURCHASE
        if (precisa_posicao and pos is None) or (q is None and fam != Family.MARKET):
            itens.append(
                {"simbolo": asset.symbol, "estado": "sem_dado", "detalhe": "sem posição ou sem cotação"}
            )
            continue
        aval = core.avaliar(pos, q.price, q.previous_close) if (pos is not None and q is not None) else None
        if aval is not None and aval.variacao_dia_pct is None and q.change_24h_pct is not None:
            import dataclasses

            aval = dataclasses.replace(aval, variacao_dia_pct=q.change_24h_pct)
        res = services.avaliar_condicao(versao, aval, q, asset, agora)
        if not res.avaliavel:
            itens.append({"simbolo": asset.symbol, "estado": "nao_avaliavel", "detalhe": res.motivo})
            continue
        estado = "dispararia" if res.satisfeita else "ok"
        if fam == Family.MARKET:
            itens.append(
                {"simbolo": asset.symbol, "estado": estado, "detalhe": _detalhe_mercado(res.valores, q)}
            )
            continue
        if fam == Family.SINCE_PURCHASE:
            atual_v, unidade = aval.retorno_pct, "%"
        elif fam == Family.DAY:
            atual_v, unidade = Decimal(res.valores["variacao_pct"]), "%"
        else:
            atual_v, unidade = q.price, q.currency
        alvo = -limite if (fam != Family.ABSOLUTE and cond == "queda") else limite
        distancia = alvo - atual_v
        itens.append(
            {
                "simbolo": asset.symbol,
                "estado": estado,
                "atual": str(atual_v.quantize(Decimal("0.01"))),
                "alvo": str(Decimal(alvo).quantize(Decimal("0.01"))),
                "distancia": str(distancia.quantize(Decimal("0.01"))),
                "unidade": unidade,
                "atualidade": atual.rotulo,
            }
        )
    return JsonResponse({"itens": itens, "texto": TEXTO_NAO_RECOMENDACAO})
