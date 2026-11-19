"""Notificações do app (Web Push) com notícias dos ativos da carteira.

Regras: só notícias ligadas aos ativos das carteiras escolhidas (BDRs incluem a empresa
americana correspondente); agrupadas por ativo; no máximo 1 aviso por ativo a cada 4 h e 8
por dia por aparelho; silêncio das 22h às 7h; só manchetes em português; nada anterior à
inscrição do aparelho. O servidor só envia para serviços de push conhecidos.
"""
import datetime as dt
import json
import logging
from urllib.parse import urlparse

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_safe

from .models import PushDelivery, PushSubscription

log = logging.getLogger(__name__)

INTERVALO_POR_ATIVO = dt.timedelta(hours=4)
LIMITE_DIARIO = 8
POR_PASSAGEM = 2
JANELA = dt.timedelta(hours=12)
SILENCIO = (22, 7)  # das 22h às 7h, horário de São Paulo
MAX_FALHAS = 5
SERVICOS = ('fcm.googleapis.com', 'web.push.apple.com', 'updates.push.services.mozilla.com',
            'push.services.mozilla.com', 'notify.windows.com')
BDR_PARA_EUA = {'NVDC34': 'NVDA', 'TSMC34': 'TSM'}  # nomes que não seguem o padrão da B3


def chaves():
    cfg = settings.NOTIFICACOES
    return cfg.get('PUSH_VAPID_PUBLIC_KEY', ''), cfg.get('PUSH_VAPID_PRIVATE_KEY', '')


def endpoint_permitido(endpoint):
    try:
        u = urlparse(endpoint)
    except ValueError:
        return False
    host = (u.hostname or '').lower()
    return u.scheme == 'https' and any(host == s or host.endswith('.' + s) for s in SERVICOS)


def em_silencio(agora):
    h = timezone.localtime(agora).hour
    return h >= SILENCIO[0] or h < SILENCIO[1]


def ativos_relacionados(accounts):
    """{asset_id: símbolo exibido} para as carteiras: posições + empresa americana das BDRs."""
    from apps.portfolio.models import Asset

    from .api import portfolio_payload
    from .logos import candidatos

    exibido = {}
    for account in accounts:
        for p in portfolio_payload({'account': account, 'history': '0'}, 'push')['positions']:
            exibido.setdefault(p['symbol'], p['symbol'])
    eua = {a.symbol: a for a in Asset.objects.filter(market__in=('NASDAQ', 'NYSE'))}
    for bdr in [s for s in exibido if s.endswith('34')]:
        alvo = BDR_PARA_EUA.get(bdr) or next((u for u in eua if bdr in candidatos(u)[1:]), None)
        if alvo:
            exibido.setdefault(alvo, bdr)
    ids = dict(Asset.objects.filter(symbol__in=exibido).values_list('pk', 'symbol'))
    return {pk: exibido[sym] for pk, sym in ids.items()}


def enviar(sub, payload):
    from pywebpush import WebPushException, webpush

    _, privada = chaves()
    try:
        webpush(subscription_info={'endpoint': sub.endpoint, 'keys': {'p256dh': sub.p256dh, 'auth': sub.auth}},
                data=json.dumps(payload, ensure_ascii=False), vapid_private_key=privada,
                vapid_claims={'sub': settings.NOTIFICACOES['PUSH_CONTATO']}, ttl=6 * 3600, timeout=10)
    except WebPushException as exc:
        status = getattr(exc.response, 'status_code', None)
        if status in (404, 410):  # aparelho desinscrito ou expirado
            sub.delete()
        else:
            sub.failures += 1
            sub.save(update_fields=['failures'])
            log.warning('push: falha %s no aparelho %s', status, sub.pk)
        return False
    except Exception:  # rede/timeout: tenta de novo na próxima passagem
        sub.failures += 1
        sub.save(update_fields=['failures'])
        log.warning('push: erro de envio no aparelho %s', sub.pk)
        return False
    sub.failures, sub.last_success_at = 0, timezone.now()
    sub.save(update_fields=['failures', 'last_success_at'])
    return True


def notificar_noticias(agora=None, enviar_fn=None):
    """Chamada pelo coletor a cada passagem. Retorna quantos avisos enviou."""
    from apps.noticias.models import Article

    agora = agora or timezone.now()
    if not all(chaves()) or em_silencio(agora):
        return 0
    enviar_fn = enviar_fn or enviar
    inicio_dia = timezone.localtime(agora).replace(hour=0, minute=0, second=0, microsecond=0)
    total, cache = 0, {}
    for sub in PushSubscription.objects.filter(failures__lt=MAX_FALHAS).order_by('pk'):
        hoje = sub.deliveries.filter(notified=True, sent_at__gte=inicio_dia).count()
        if hoje >= LIMITE_DIARIO:
            continue
        chave = tuple(sorted(sub.accounts))
        if chave not in cache:
            cache[chave] = ativos_relacionados(chave)
        mapa = cache[chave]
        if not mapa:
            continue
        desde = max(sub.created_at, agora - JANELA)
        artigos = (Article.objects.filter(published_at__gte=desde, assets__in=list(mapa),
                                          translation_status__in=('original', 'traduzida'))
                   .exclude(pk__in=sub.deliveries.values('article_id')).distinct()
                   .prefetch_related('assets').order_by('-published_at'))
        grupos = {}
        for art in artigos:
            simbolo = min(mapa[a.pk] for a in art.assets.all() if a.pk in mapa)
            grupos.setdefault(simbolo, []).append(art)
        enviados = 0
        for simbolo, lista in sorted(grupos.items(), key=lambda g: -g[1][0].published_at.timestamp()):
            if enviados >= POR_PASSAGEM or hoje >= LIMITE_DIARIO:
                break
            ultimo = sub.deliveries.filter(symbol=simbolo, notified=True).order_by('-sent_at').first()
            if ultimo and agora - ultimo.sent_at < INTERVALO_POR_ATIVO:
                continue
            manchete = lista[0]
            ativo = next((a.pk for a in manchete.assets.all() if mapa.get(a.pk) == simbolo), None)
            payload = {
                'title': simbolo if len(lista) == 1 else f'{simbolo} · {len(lista)} notícias novas',
                'body': manchete.title[:180],
                'url': f'/noticias/?ativo={ativo}&dias=1' if ativo else '/noticias/',
                'tag': f'noticias-{simbolo}',
            }
            if not enviar_fn(sub, payload):
                break
            PushDelivery.objects.bulk_create(
                [PushDelivery(subscription=sub, article=a, symbol=simbolo, notified=a is manchete, sent_at=agora)
                 for a in lista], ignore_conflicts=True)
            enviados += 1
            hoje += 1
            total += 1
    return total


# ---------- API usada pelo app ----------

def _json(request):
    try:
        return json.loads(request.body or b'{}')
    except ValueError:
        return None


@never_cache
@require_safe
@login_required
def status(request):
    publica, privada = chaves()
    endpoint = request.GET.get('endpoint', '')
    sub = PushSubscription.objects.filter(endpoint=endpoint, user=request.user).first() if endpoint else None
    return JsonResponse({'enabled': bool(publica and privada), 'publicKey': publica,
                         'subscribed': bool(sub), 'accounts': sub.accounts if sub else [],
                         'rules': {'perAsset': '4 h', 'daily': LIMITE_DIARIO, 'quiet': '22h às 7h'}})


@never_cache
@require_http_methods(['POST'])
@login_required
def subscribe(request):
    body = _json(request)
    sub = (body or {}).get('subscription') or {}
    keys = sub.get('keys') or {}
    accounts = [a for a in (body or {}).get('accounts', []) if a in ('ana', 'bruno')]
    endpoint = str(sub.get('endpoint', ''))
    if not endpoint_permitido(endpoint) or len(endpoint) > 800 or not keys.get('p256dh') or not keys.get('auth') or not accounts:
        return JsonResponse({'error': 'Inscrição inválida.'}, status=400)
    if not all(chaves()):
        return JsonResponse({'error': 'Notificações ainda não configuradas no servidor.'}, status=503)
    obj, criado = PushSubscription.objects.update_or_create(endpoint=endpoint, defaults={
        'user': request.user, 'p256dh': str(keys['p256dh'])[:200], 'auth': str(keys['auth'])[:100],
        'accounts': accounts, 'device': str(body.get('device', ''))[:120], 'failures': 0})
    if criado:
        enviar(obj, {'title': 'Notificações ativadas', 'body': 'Você vai receber as notícias dos seus ativos aqui.',
                     'url': '/noticias/', 'tag': 'id3-ativacao'})
    return JsonResponse({'subscribed': True, 'accounts': obj.accounts})


@never_cache
@require_http_methods(['POST'])
@login_required
def unsubscribe(request):
    body = _json(request) or {}
    PushSubscription.objects.filter(endpoint=str(body.get('endpoint', '')), user=request.user).delete()
    return JsonResponse({'subscribed': False})


@never_cache
@require_http_methods(['POST'])
@login_required
def test(request):
    body = _json(request) or {}
    sub = PushSubscription.objects.filter(endpoint=str(body.get('endpoint', '')), user=request.user).first()
    if not sub:
        return JsonResponse({'error': 'Este aparelho não está inscrito.'}, status=404)
    ok = enviar(sub, {'title': 'Portal de Investimentos', 'body': 'Teste de notificação: está tudo certo.',
                      'url': '/', 'tag': 'id3-teste'})
    return JsonResponse({'sent': ok}, status=200 if ok else 502)


@require_safe
def service_worker(request):
    from pathlib import Path

    from django.http import HttpResponse, HttpResponseNotFound

    arquivo = Path(settings.BASE_DIR) / 'apps/react_portal/static/react_portal/sw.js'
    if not arquivo.exists():
        return HttpResponseNotFound()
    resposta = HttpResponse(arquivo.read_text(), content_type='application/javascript; charset=utf-8')
    resposta['Cache-Control'] = 'no-cache'
    resposta['Service-Worker-Allowed'] = '/'
    return resposta
