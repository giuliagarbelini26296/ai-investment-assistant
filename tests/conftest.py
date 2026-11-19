import pytest
from django.contrib.auth import get_user_model


@pytest.fixture
def usuario(db):
    return get_user_model().objects.create_user(username="ana", password="senha-de-teste-123456")


@pytest.fixture
def cliente_logado(client, usuario):
    client.force_login(usuario)
    return client
