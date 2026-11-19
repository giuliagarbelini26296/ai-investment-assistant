import pathlib
import datetime as dt
from unittest.mock import Mock

import httpx
import pytest
from django.urls import reverse
from django.utils import timezone

from apps.mercado.models import Favorito, NewsItem
from apps.noticias import ingest, translation
from apps.noticias.models import Article, SourceState

from .test_rc3 import carteira  # noqa: F401


def article(**overrides):
    data = dict(
        url="https://example.org/news/one",
        headline="Stocks rose after inflation slowed.",
        summary="The central bank kept interest rates unchanged.",
        published_at=timezone.now(),
        source="test",
        publisher="Fonte de teste",
        language="en",
        region="internacional",
    )
    data.update(overrides)
    return ingest.store(**data)[0]


@pytest.mark.parametrize(
    "url", ["javascript:alert(1)", "data:text/html,x", "https://u:p@example.org/x", "", "//example.org/a"]
)
def test_rejeita_links_inseguros(url):
    assert ingest.canonical_url(url) == ""


def test_url_remove_rastreadores_preserva_parametros_do_artigo():
    assert (
        ingest.canonical_url("https://EXAMPLE.org/a?id=3&utm_source=feed&fbclid=x#top")
        == "https://example.org/a?id=3"
    )


@pytest.mark.django_db
def test_mesma_materia_une_ativos_sem_duplicar_ou_modificar_origem(carteira):
    a = article(url="https://example.org/a?utm_source=x", assets=[carteira["btc"].pk])
    b = article(url="https://example.org/a?utm_source=y", assets=[carteira["ttwo"].pk])
    assert a.pk == b.pk and Article.objects.count() == 1
    assert b.assets.count() == 2


@pytest.mark.django_db
def test_sincronizacao_aproveita_noticias_sem_mudar_registros_anteriores(carteira):
    now = timezone.now()
    for asset in carteira.values():
        NewsItem.objects.create(
            asset=asset,
            headline="Company earnings",
            summary="Original summary",
            url="https://example.org/same",
            source="finnhub",
            publisher="Fonte",
            published_at=now,
            collected_at=now,
        )
    before = list(NewsItem.objects.values())
    assert ingest.sync_existing(now) == 1
    assert Article.objects.get().assets.count() == 2
    assert ingest.sync_existing(now) == 0
    assert list(NewsItem.objects.values()) == before


def test_parser_rss_e_atom_e_limpeza_do_conteudo():
    rss = b"<rss><channel><item><title>Market</title><link>https://example.org/one</link><description>&lt;b&gt;Text&lt;/b&gt;</description><pubDate>Tue, 03 Nov 2026 10:00:00 GMT</pubDate></item></channel></rss>"
    result = ingest.parse_feed(rss)
    assert result[0]["headline"] == "Market"
    assert result[0]["published_at"].tzinfo is not None
    assert ingest.clean_text("<script>bad()</script><b>Text</b> &amp; more") == "Text & more"
    atom = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Market</title><link href="https://example.org/one"/><updated>2026-11-03T10:00:00Z</updated></entry></feed>'
    assert ingest.parse_feed(atom)[0]["url"] == "https://example.org/one"


@pytest.mark.parametrize(
    "payload", [b"<!DOCTYPE x><rss/>", b'<!ENTITY x "a"><rss/>', b"<html>error</html>", b"x" * 2_000_001]
)
def test_parser_rejeita_conteudo_inesperado(payload):
    with pytest.raises((ValueError, ingest.ET.ParseError)):
        ingest.parse_feed(payload)


@pytest.mark.django_db
def test_datas_futuras_e_links_invalidos_nao_entram():
    assert article(published_at=timezone.now() + dt.timedelta(days=2)) is None
    assert article(url="javascript:bad") is None
    assert Article.objects.count() == 0


@pytest.mark.django_db
def test_traducao_preserva_original_e_reaproveita_resultado():
    a = article()
    original = (a.headline, a.summary)
    translator = Mock(side_effect=lambda text: "Traduzido: " + text)
    assert ingest.translate_pending(translator=translator) == 1
    a.refresh_from_db()
    assert (a.headline, a.summary) == original
    assert a.translated and a.headline_pt.startswith("Traduzido:")
    assert ingest.translate_pending(translator=translator) == 0
    assert translator.call_count == 2


@pytest.mark.django_db
def test_falha_de_traducao_mantem_original_e_nao_traduz_portugues():
    a = article()
    native = article(
        url="https://example.org/br", language="pt", region="brasil", headline="Inflação desacelera"
    )
    translator = Mock(side_effect=RuntimeError("modelo indisponível"))
    assert ingest.translate_pending(translator=translator) == 0
    a.refresh_from_db()
    native.refresh_from_db()
    assert a.headline_pt == "" and a.headline == "Stocks rose after inflation slowed."
    assert a.translation_status == Article.Translation.FAILED
    assert native.translation_status == Article.Translation.ORIGINAL
    assert ingest.translate_pending(translator=translator) == 0


@pytest.mark.skipif(not (pathlib.Path(__file__).resolve().parents[1] / 'translation-model' / 'model' / 'model.bin').exists(),
                    reason='pesos do modelo de tradução não acompanham o repositório (ver translation-model/README.md)')
def test_tradutor_local_real_funciona_sem_rede_e_sem_marcadores():
    result = translation.translate("The central bank kept interest rates unchanged.")
    assert "banco central" in result.lower() and "juro" in result.lower()
    assert "▁" not in result
    assert translation.translate("") == ""
    assert (
        translation.financial_terms("This growth stock", "Esse estoque de crescimento")
        == "Essa ação de crescimento"
    )


@pytest.mark.django_db
def test_central_exige_login_e_atualizacao_exige_post(client, cliente_logado):
    from django.test import Client

    assert Client().get(reverse("noticias:index")).status_code == 302
    assert cliente_logado.get(reverse("noticias:refresh")).status_code == 405
    state = SourceState.objects.create(key="test", name="Teste")
    assert cliente_logado.post(reverse("noticias:refresh")).status_code == 302
    state.refresh_from_db()
    assert state.requested


@pytest.mark.django_db
def test_filtros_favoritos_isolados_e_carteira(cliente_logado, usuario, carteira, django_user_model):
    a = article(assets=[carteira["btc"].pk])
    b = article(
        url="https://example.org/br",
        language="pt",
        region="brasil",
        headline="Economia brasileira",
        assets=[carteira["ttwo"].pk],
    )
    other = django_user_model.objects.create_user(username="outro")
    Favorito.objects.create(user=other, asset=carteira["ttwo"])
    Favorito.objects.create(user=usuario, asset=carteira["btc"])
    url = reverse("noticias:index")
    response = cliente_logado.get(url, {"escopo": "favoritos"})
    assert list(response.context["page"].object_list) == [a]
    assert list(cliente_logado.get(url, {"regiao": "brasil"}).context["page"].object_list) == [b]
    assert list(cliente_logado.get(url, {"q": "brasileira"}).context["page"].object_list) == [b]
    assert cliente_logado.get(url, {"escopo": "carteira"}).context["page"].paginator.count == 2
    assert cliente_logado.get(url, {"ativo": str(carteira["btc"].pk)}).context["page"].paginator.count == 1


@pytest.mark.django_db
def test_pagina_e_destaques_nao_chamam_fontes_ou_tradutor(cliente_logado, carteira, monkeypatch):
    a = article(assets=[carteira["btc"].pk])
    a.headline_pt = "Notícia em português"
    a.translation_status = Article.Translation.DONE
    a.save()
    fail = Mock(side_effect=AssertionError("Não deve executar na página"))
    monkeypatch.setattr(ingest, "translate", fail)
    monkeypatch.setattr(httpx, "Client", fail)
    assert cliente_logado.get(reverse("noticias:index")).status_code == 200
    response = cliente_logado.get("/classico/")
    assert response.status_code == 200 and "Notícia em português" in response.content.decode()
    assert not fail.called


@pytest.mark.django_db
def test_fonte_falha_sem_apagar_noticias_e_cooldown_e_respeitado(monkeypatch):
    a = article()
    now = timezone.now()
    monkeypatch.setattr(ingest, "fetch_finnhub", lambda _: 0)
    fake = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503)))
    ingest.run_once(now=now, client=fake, translate_limit=0)
    assert Article.objects.filter(pk=a.pk).exists()
    states = SourceState.objects.filter(key__in=["fed", "agencia_brasil"])
    assert all(s.last_error and s.next_check_at > now for s in states)
    assert ingest.run_once(now=now + dt.timedelta(seconds=30), client=fake, translate_limit=0)["fontes"] == []
    assert not fake.is_closed


@pytest.mark.django_db
def test_data_repetida_e_varios_ativos_nao_repetem_card(cliente_logado, carteira):
    a = article(assets=[x.pk for x in carteira.values()])
    response = cliente_logado.get(reverse("noticias:index"), {"escopo": "carteira"})
    assert response.context["page"].paginator.count == 1
    assert response.content.decode().count(a.headline) == 1


@pytest.mark.django_db
def test_paginacao_e_html_escapado(cliente_logado):
    for i in range(23):
        article(url=f"https://example.org/{i}", headline=f"Notícia {i}")
    a = Article.objects.first()
    a.headline = '<img src=x onerror="bad()">'
    a.save()
    response = cliente_logado.get(reverse("noticias:index"))
    assert len(response.context["page"].object_list) == 20
    assert response.context["page"].paginator.num_pages == 2
    assert '<img src=x onerror="bad()">' not in response.content.decode()
    assert (
        len(cliente_logado.get(reverse("noticias:index"), {"pagina": "2"}).context["page"].object_list) == 3
    )
