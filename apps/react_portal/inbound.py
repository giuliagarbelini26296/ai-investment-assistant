"""Envio de notificações por serviços externos (ex.: Milo), com chave exclusiva.

POST /api/v1/notificacoes/   Authorization: Bearer <chave>
{"title": "...", "body": "...", "url": "/opcional", "accounts": ["ana","bruno"]}

A chave só envia notificações (não lê nada). Limites: 30 por hora por chave, título até 60
e texto até 240 caracteres, link só para páginas do portal, silêncio das 22h às 7h.
Entrega aos aparelhos inscritos no sino cujas carteiras coincidem com "accounts".
"""
import datetime as dt
import hashlib
import json
import re
import secrets

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from . import push
from .models import ExternalNotification, PushApiKey, PushSubscription

LIMITE_HORA = 30
TITULO_MAX, TEXTO_MAX = 60, 240
CONTROLE = re.compile(r'[\x00-\x1f\x7f]')
PREFIXO = 'id3n_'


def gerar_chave(nome, escopo='notificacoes', contas=None):
    token = PREFIXO + secrets.token_urlsafe(32)
    PushApiKey.objects.create(name=nome, prefix=token[:12], token_hash=hashlib.sha256(token.encode()).hexdigest(),
                              scope=escopo, accounts=list(contas or ['ana', 'bruno']))
    return token


def chave_valida(request, escopo='notificacoes'):
    auth = request.headers.get('Authorization', '')
    if not auth.startswith('Bearer '):
        return None
    token = auth[7:].strip()
    if not token.startswith(PREFIXO) or len(token) > 100:
        return None
    digest = hashlib.sha256(token.encode()).hexdigest()
    key = PushApiKey.objects.filter(token_hash=digest, revoked_at__isnull=True, scope=escopo).first()
    return key if key and secrets.compare_digest(key.token_hash, digest) else None


def _texto(valor, limite):
    return CONTROLE.sub(' ', str(valor or '')).strip()[:limite]


def _erro(msg, status):
    return JsonResponse({'error': msg}, status=status)


@csrf_exempt  # autenticação por chave no cabeçalho, sem cookies de sessão
@never_cache
@require_http_methods(['POST'])
def receber(request):
    key = chave_valida(request)
    if not key:
        return _erro('Chave ausente, inválida ou revogada.', 401)
    agora = timezone.now()
    if key.notifications.filter(created_at__gte=agora - dt.timedelta(hours=1)).count() >= LIMITE_HORA:
        return _erro(f'Limite de {LIMITE_HORA} notificações por hora atingido.', 429)
    try:
        corpo = json.loads(request.body or b'{}')
        assert isinstance(corpo, dict)
    except (ValueError, AssertionError):
        return _erro('Envie um JSON com "title" e "body".', 400)
    titulo, texto = _texto(corpo.get('title'), TITULO_MAX), _texto(corpo.get('body'), TEXTO_MAX)
    url = str(corpo.get('url') or '/')
    if not titulo or not texto:
        return _erro('"title" e "body" são obrigatórios.', 400)
    if not url.startswith('/') or url.startswith('//') or len(url) > 200 or CONTROLE.search(url):
        return _erro('"url" deve ser um caminho do portal, como "/" ou "/noticias/".', 400)
    contas = corpo.get('accounts') or ['ana', 'bruno']
    if not isinstance(contas, list) or not set(contas) <= {'ana', 'bruno'} or not contas:
        return _erro('"accounts" aceita "ana" e/ou "bruno".', 400)
    key.last_used_at = agora
    key.save(update_fields=['last_used_at'])
    if push.em_silencio(agora):
        ExternalNotification.objects.create(key=key, title=titulo, body=texto, status='silencio', created_at=agora)
        return JsonResponse({'sent': 0, 'status': 'silencio', 'detail': 'Silêncio das 22h às 7h (São Paulo).'}, status=202)
    payload = {'title': titulo, 'body': texto, 'url': url, 'tag': f'{key.name}-{int(agora.timestamp())}'}
    entregues = 0
    for sub in PushSubscription.objects.filter(failures__lt=push.MAX_FALHAS):
        if set(sub.accounts) & set(contas) and push.enviar(sub, payload):
            entregues += 1
    ExternalNotification.objects.create(key=key, title=titulo, body=texto, devices=entregues,
                                        status='enviada' if entregues else 'sem_aparelhos', created_at=agora)
    return JsonResponse({'sent': entregues, 'status': 'enviada' if entregues else 'sem_aparelhos'})
