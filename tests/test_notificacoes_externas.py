import datetime as dt
import json
from unittest import mock

import pytest
from django.core.management import call_command
from django.test import Client
from django.utils import timezone

from apps.react_portal import inbound, push
from apps.react_portal.models import ExternalNotification, PushApiKey, PushSubscription

URL = '/api/v1/notificacoes/'
MEIO_DIA = timezone.make_aware(dt.datetime(2026, 11, 19, 12, 0))


def postar(token, corpo, cliente=None):
    cliente = cliente or Client(enforce_csrf_checks=True)
    headers = {'HTTP_AUTHORIZATION': f'Bearer {token}'} if token else {}
    return cliente.post(URL, json.dumps(corpo), content_type='application/json', **headers)


@pytest.fixture
def aparelhos(usuario):
    a = PushSubscription.objects.create(user=usuario, endpoint='https://fcm.googleapis.com/fcm/send/a', p256dh='x', auth='y', accounts=['ana'])
    m = PushSubscription.objects.create(user=usuario, endpoint='https://web.push.apple.com/m', p256dh='x', auth='y', accounts=['bruno'])
    return a, m


@pytest.mark.django_db
def test_chave_guardada_so_como_hash():
    token = inbound.gerar_chave('milo')
    k = PushApiKey.objects.get()
    assert token.startswith('id3n_') and token not in (k.token_hash, k.prefix) and len(k.token_hash) == 64


@pytest.mark.django_db
def test_sem_chave_ou_revogada_recusa(aparelhos):
    assert postar(None, {'title': 'a', 'body': 'b'}).status_code == 401
    assert postar('id3n_errada', {'title': 'a', 'body': 'b'}).status_code == 401
    token = inbound.gerar_chave('milo')
    call_command('chave_notificacoes', 'revogar', 'milo')
    assert postar(token, {'title': 'a', 'body': 'b'}).status_code == 401


@pytest.mark.django_db
def test_envia_so_para_carteiras_pedidas_sem_csrf(aparelhos):
    token = inbound.gerar_chave('milo')
    enviados = []
    with mock.patch.object(timezone, 'now', return_value=MEIO_DIA), \
         mock.patch.object(push, 'enviar', side_effect=lambda s, p: enviados.append((s.endpoint, p)) or True):
        r = postar(token, {'title': 'PETR4 +3,2%', 'body': 'Maior alta da carteira desde a abertura.', 'url': '/carteira', 'accounts': ['bruno']})
    assert r.status_code == 200 and r.json() == {'sent': 1, 'status': 'enviada'}
    assert enviados[0][0].startswith('https://web.push.apple.com') and enviados[0][1]['url'] == '/carteira'
    assert ExternalNotification.objects.get().devices == 1


@pytest.mark.django_db
@pytest.mark.parametrize('corpo', [
    {'title': '', 'body': 'x'},
    {'title': 'x', 'body': 'y', 'url': 'https://golpe.com'},
    {'title': 'x', 'body': 'y', 'url': '//golpe.com'},
    {'title': 'x', 'body': 'y', 'accounts': ['joao']},
])
def test_valida_conteudo(corpo):
    token = inbound.gerar_chave('milo')
    assert postar(token, corpo).status_code == 400


@pytest.mark.django_db
def test_corta_textos_longos_e_caracteres_de_controle(aparelhos):
    token = inbound.gerar_chave('milo')
    capturado = {}
    with mock.patch.object(timezone, 'now', return_value=MEIO_DIA), \
         mock.patch.object(push, 'enviar', side_effect=lambda s, p: capturado.update(p) or True):
        postar(token, {'title': 'T' * 200 + '\x07', 'body': 'linha1\nlinha2' + 'b' * 500})
    assert len(capturado['title']) == inbound.TITULO_MAX and len(capturado['body']) == inbound.TEXTO_MAX and '\n' not in capturado['body']


@pytest.mark.django_db
def test_limite_por_hora_e_silencio(aparelhos):
    token = inbound.gerar_chave('milo')
    with mock.patch.object(timezone, 'now', return_value=MEIO_DIA), mock.patch.object(push, 'enviar', return_value=True):
        for _ in range(inbound.LIMITE_HORA):
            assert postar(token, {'title': 'a', 'body': 'b'}).status_code == 200
        assert postar(token, {'title': 'a', 'body': 'b'}).status_code == 429
    noite = timezone.make_aware(dt.datetime(2026, 11, 19, 23, 0))
    with mock.patch.object(timezone, 'now', return_value=noite), mock.patch.object(push, 'enviar') as enviar:
        r = postar(token, {'title': 'a', 'body': 'b'})
    assert r.status_code == 202 and r.json()['status'] == 'silencio' and not enviar.called


@pytest.mark.django_db
def test_chave_nao_le_dados_do_portal():
    token = inbound.gerar_chave('milo')
    c = Client(HTTP_AUTHORIZATION=f'Bearer {token}')
    assert c.get('/api/v1/portfolio/?account=ana').status_code == 401
    assert c.get(URL).status_code == 405


@pytest.mark.django_db
def test_comando_grava_arquivo_protegido(tmp_path):
    arquivo = tmp_path / 'milo.txt'
    call_command('chave_notificacoes', 'criar', 'milo', arquivo=str(arquivo))
    assert arquivo.read_text().startswith('id3n_') and oct(arquivo.stat().st_mode & 0o777) == '0o600'
