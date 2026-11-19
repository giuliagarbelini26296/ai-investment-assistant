"""Testes de fumaça da E1: configuração, autenticação, páginas básicas e coletor-esqueleto."""

from io import StringIO

import pytest
from django.core.management import call_command
from django.urls import reverse


def test_saude_responde_sem_login(client, db):
    resposta = client.get(reverse("health"))
    assert resposta.status_code == 200
    assert resposta.json()["status"] == "ok"
    assert resposta.json()["banco"] == "ok"


def test_home_exige_login(client, db):
    resposta = client.get(reverse("home_classico"))
    assert resposta.status_code == 302
    assert reverse("login") in resposta["Location"]


def test_login_renderiza_em_portugues(client, db):
    resposta = client.get(reverse("login"))
    assert resposta.status_code == 200
    assert "Entrar" in resposta.content.decode()
    assert 'lang="pt-BR"' in resposta.content.decode()


def test_login_com_senha_errada_nao_autentica(client, usuario):
    resposta = client.post(reverse("login"), {"username": "ana", "password": "errada"})
    assert resposta.status_code == 200
    assert "inválidos" in resposta.content.decode()


def test_home_logado_mostra_faixa_demo(cliente_logado, settings):
    settings.PORTAL = {**settings.PORTAL, "MODO_DEMO": True}
    resposta = cliente_logado.get(reverse("home_classico"))
    html = resposta.content.decode()
    assert resposta.status_code == 200
    assert "DEMONSTRAÇÃO" in html
    assert "não constituem recomendação" in html


def test_home_sem_modo_demo_nao_mostra_faixa(cliente_logado, settings):
    settings.PORTAL = {**settings.PORTAL, "MODO_DEMO": False}
    resposta = cliente_logado.get(reverse("home_classico"))
    assert "DEMONSTRAÇÃO" not in resposta.content.decode()


def test_logout_exige_post(cliente_logado):
    assert cliente_logado.get(reverse("logout")).status_code == 405
    assert cliente_logado.post(reverse("logout")).status_code == 302


def test_cookies_de_sessao_sao_httponly(settings):
    assert settings.SESSION_COOKIE_HTTPONLY is True
    assert settings.CSRF_COOKIE_HTTPONLY is True


def test_coletor_esqueleto_executa_um_ciclo(db):
    saida = StringIO()
    call_command("coletar", "--uma-vez", stdout=saida)


@pytest.mark.parametrize(
    "url, esperado",
    [
        ("postgres://u:p@host:5432/banco", ("django.db.backends.postgresql", "banco", "u", "host", "5432")),
        ("sqlite:////tmp/x.sqlite3", ("django.db.backends.sqlite3", "/tmp/x.sqlite3")),
    ],
)
def test_database_from_url(url, esperado):
    from config.settings import database_from_url

    cfg = database_from_url(url)
    assert cfg["ENGINE"] == esperado[0]
    assert cfg["NAME"] == esperado[1]
    if len(esperado) > 2:
        assert (cfg["USER"], cfg["HOST"], cfg["PORT"]) == esperado[2:]


def test_database_from_url_rejeita_esquema_desconhecido():
    from config.settings import database_from_url

    with pytest.raises(RuntimeError):
        database_from_url("mysql://x")
