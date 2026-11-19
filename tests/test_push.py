import base64
import datetime as dt
import json
from unittest import mock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.utils import timezone

from apps.noticias.models import Article
from apps.portfolio.models import Asset
from apps.react_portal import push
from apps.react_portal.models import PushDelivery, PushSubscription

B64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b'=').decode()  # noqa: E731


def chaves_vapid():
    k = ec.generate_private_key(ec.SECP256R1())
    priv = B64(k.private_numbers().private_value.to_bytes(32, 'big'))
    pub = B64(k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
    return pub, priv


@pytest.fixture
def vapid(settings):
    pub, priv = chaves_vapid()
    settings.NOTIFICACOES = {**settings.NOTIFICACOES, 'PUSH_VAPID_PUBLIC_KEY': pub, 'PUSH_VAPID_PRIVATE_KEY': priv,
                             'PUSH_CONTATO': 'https://portal.example.com'}
    return pub


def assinante_do_navegador():
    """Chaves de um 'navegador' de verdade, para o pywebpush criptografar a mensagem."""
    k = ec.generate_private_key(ec.SECP256R1())
    p256dh = B64(k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
    return p256dh, B64(b'0123456789abcdef')


def inscrever(usuario, accounts=('ana',), criado=None):
    p256dh, auth = assinante_do_navegador()
    sub = PushSubscription.objects.create(user=usuario, endpoint=f'https://fcm.googleapis.com/fcm/send/{timezone.now().timestamp()}',
                                          p256dh=p256dh, auth=auth, accounts=list(accounts))
    if criado:
        PushSubscription.objects.filter(pk=sub.pk).update(created_at=criado)
        sub.refresh_from_db()
    return sub


def noticia(ativo, quando, n=0, status='traduzida'):
    a = Article.objects.create(url_hash=f'{ativo.symbol}{n}{quando.timestamp()}', url='https://exemplo.com/x', headline=f'Manchete {n}',
                               headline_pt=f'Manchete {ativo.symbol} {n}', source='t', publisher='t', published_at=quando,
                               collected_at=quando, translation_status=status)
    a.assets.add(ativo)
    return a


@pytest.fixture
def ativos(db):
    orcl = Asset.objects.create(symbol='ORCL', name='Oracle', market='NYSE', custody='AVENUE')
    nvda = Asset.objects.create(symbol='NVDA', name='NVIDIA', market='NASDAQ', custody='NENHUMA')
    return orcl, nvda


def test_endpoint_so_de_servicos_conhecidos():
    assert push.endpoint_permitido('https://fcm.googleapis.com/fcm/send/abc')
    assert push.endpoint_permitido('https://web.push.apple.com/QAB')
    assert not push.endpoint_permitido('http://fcm.googleapis.com/x')
    assert not push.endpoint_permitido('https://127.0.0.1/x')
    assert not push.endpoint_permitido('https://fcm.googleapis.com.evil.com/x')


def test_silencio_noturno():
    sp = lambda h: timezone.make_aware(dt.datetime(2026, 11, 19, h, 0))  # noqa: E731
    assert push.em_silencio(sp(23)) and push.em_silencio(sp(3)) and not push.em_silencio(sp(7)) and not push.em_silencio(sp(21))


@pytest.mark.django_db
def test_agrupa_por_ativo_respeita_intervalo_e_nao_manda_atrasadas(vapid, usuario, ativos):
    orcl, nvda = ativos
    agora = timezone.make_aware(dt.datetime(2026, 11, 19, 10, 0))
    sub = inscrever(usuario, criado=agora - dt.timedelta(hours=2))
    noticia(orcl, agora - dt.timedelta(hours=3), 9)  # anterior à inscrição: nunca avisada
    for i in range(3):
        noticia(orcl, agora - dt.timedelta(minutes=10 * (i + 1)), i)
    enviados = []
    mapa = {orcl.pk: 'ORCL'}
    with mock.patch.object(push, 'ativos_relacionados', return_value=mapa):
        assert push.notificar_noticias(agora, enviar_fn=lambda s, p: enviados.append(p) or True) == 1
        assert enviados[0]['title'] == 'ORCL · 3 notícias novas' and enviados[0]['body'] == 'Manchete ORCL 0'
        assert enviados[0]['url'] == f'/noticias/?ativo={orcl.pk}&dias=1'
        noticia(orcl, agora + dt.timedelta(minutes=5), 7)
        assert push.notificar_noticias(agora + dt.timedelta(hours=1), enviar_fn=lambda s, p: enviados.append(p) or True) == 0  # < 4 h
        assert push.notificar_noticias(agora + dt.timedelta(hours=5), enviar_fn=lambda s, p: enviados.append(p) or True) == 1
    assert PushDelivery.objects.filter(subscription=sub, notified=True).count() == 2


@pytest.mark.django_db
def test_limite_diario_e_por_passagem(vapid, usuario, ativos):
    agora = timezone.make_aware(dt.datetime(2026, 11, 19, 12, 0))
    inscrever(usuario, criado=agora - dt.timedelta(hours=1))
    simbolos = [Asset.objects.create(symbol=f'AB{i}C3', name='x', market='B3', custody='BTG') for i in range(12)]
    for i, a in enumerate(simbolos):
        noticia(a, agora - dt.timedelta(minutes=i + 1), i)
    mapa = {a.pk: a.symbol for a in simbolos}
    with mock.patch.object(push, 'ativos_relacionados', return_value=mapa):
        assert push.notificar_noticias(agora, enviar_fn=lambda s, p: True) == push.POR_PASSAGEM
        total = push.POR_PASSAGEM
        for k in range(1, 10):
            total += push.notificar_noticias(agora + dt.timedelta(minutes=k), enviar_fn=lambda s, p: True)
    assert total == push.LIMITE_DIARIO


@pytest.mark.django_db
def test_sem_chaves_ou_em_silencio_nao_envia(usuario, ativos, settings):
    orcl, _ = ativos
    agora = timezone.make_aware(dt.datetime(2026, 11, 19, 23, 0))
    inscrever(usuario, criado=agora - dt.timedelta(hours=1))
    noticia(orcl, agora, 1)
    chamada = mock.Mock(return_value=True)
    assert push.notificar_noticias(agora, enviar_fn=chamada) == 0 and not chamada.called


@pytest.mark.django_db
def test_envio_real_criptografa_e_trata_aparelho_expirado(vapid, usuario):
    sub = inscrever(usuario)
    ok = mock.Mock(status_code=201, text='', headers={})
    with mock.patch('requests.post', return_value=ok) as post:
        assert push.enviar(sub, {'title': 'Teste', 'body': 'Olá', 'url': '/'})
    corpo, cab = post.call_args.kwargs['data'], post.call_args.kwargs['headers']
    assert b'Teste' not in corpo and cab['content-encoding'] == 'aes128gcm' and cab['authorization'].startswith('vapid ')
    expirado = mock.Mock(status_code=410, text='gone', headers={})
    with mock.patch('requests.post', return_value=expirado):
        assert not push.enviar(sub, {'title': 'x'})
    assert not PushSubscription.objects.filter(pk=sub.pk).exists()


@pytest.mark.django_db
def test_api_inscricao_valida_e_exige_login(client, cliente_logado, vapid):
    from django.test import Client
    assert Client().get('/api/v1/push/').status_code in (302, 401)
    s = cliente_logado.get('/api/v1/push/').json()
    assert s['enabled'] and s['publicKey'] == vapid and not s['subscribed']
    p256dh, auth = assinante_do_navegador()
    ruim = {'subscription': {'endpoint': 'https://evil.example/x', 'keys': {'p256dh': p256dh, 'auth': auth}}, 'accounts': ['ana']}
    assert cliente_logado.post('/api/v1/push/subscribe/', json.dumps(ruim), content_type='application/json').status_code == 400
    bom = {**ruim, 'subscription': {**ruim['subscription'], 'endpoint': 'https://fcm.googleapis.com/fcm/send/abc'}, 'device': 'Android'}
    with mock.patch.object(push, 'enviar', return_value=True) as boas_vindas:
        r = cliente_logado.post('/api/v1/push/subscribe/', json.dumps(bom), content_type='application/json')
    assert r.status_code == 200 and boas_vindas.called
    assert cliente_logado.get('/api/v1/push/?endpoint=https://fcm.googleapis.com/fcm/send/abc').json()['subscribed']
    assert cliente_logado.post('/api/v1/push/unsubscribe/', json.dumps({'endpoint': 'https://fcm.googleapis.com/fcm/send/abc'}),
                               content_type='application/json').status_code == 200
    assert not PushSubscription.objects.exists()


@pytest.mark.django_db
def test_service_worker_na_raiz(client, tmp_path, settings):
    pasta = tmp_path / 'apps/react_portal/static/react_portal'
    pasta.mkdir(parents=True)
    (pasta / 'sw.js').write_text("self.addEventListener('push',()=>{})")
    settings.BASE_DIR = tmp_path
    r = client.get('/sw.js')
    assert r.status_code == 200 and r['Service-Worker-Allowed'] == '/' and 'javascript' in r['Content-Type']
