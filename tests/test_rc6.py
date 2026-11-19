"""rc6: painel expansível do ativo, prévia da regra, tabela interativa (marcação no HTML)."""

from decimal import Decimal

from django.urls import reverse

from .test_rc3 import AGORA, carteira  # noqa: F401 - fixture

D = Decimal


def test_painel_do_ativo(cliente_logado, carteira, usuario):
    from .test_alerts import regra

    ttwo = carteira["ttwo"]
    regra(usuario, ttwo, family="preco_absoluto", condition="abaixo", threshold=D("90"), currency="USD")
    html = cliente_logado.get(reverse("asset_painel", args=[ttwo.pk])).content.decode()
    assert "<html" not in html and 'class="painel-ativo"' in html
    assert "grafico-painel" in html and "linhas-painel" in html and '"tipo": "abaixo"' in html
    assert "Compras" in html and "01/10/26" in html and "US$ 100,00" in html
    assert "preço abaixo de US$ 90" in html and "página completa" in html
    assert cliente_logado.get(reverse("asset_painel", args=[9999])).status_code == 404


def test_carteira_marcacao_interativa(cliente_logado, carteira, monkeypatch):
    # This markup fixture is dated 29/10; evaluate it at its actual quote time.
    monkeypatch.setattr("django.utils.timezone.now", lambda: AGORA)
    html = cliente_logado.get(reverse("home_classico")).content.decode()
    assert 'id="filtros-rapidos"' in html and 'data-filtro="classe:cripto"' in html
    assert 'id="simulador"' in html and 'id="sim-cambio"' in html
    assert 'class="sel-comparar"' in html and 'id="comparar-selecionados"' in html
    assert 'data-painel="' in html and 'class="expandir"' in html
    assert (
        'data-classe="cripto"' in html
        and 'data-custodia="BINANCE"' in html
        and 'data-situacao="lucro"' in html
    )
    assert html.count("ponto-") >= 2  # indicador de atualidade em cada preço
    assert 'id="spark-patrimonio"' in html and 'class="seta"' in html
    assert 'data-valor="' in html and 'data-investido="' in html  # base do simulador


def test_previa_da_regra(cliente_logado, carteira):
    url = reverse("regra_previa")
    # BTC +10% no dia; TTWO +10% no dia (previous 100 → 110)
    d = cliente_logado.get(url, {"tipo": "alta_dia", "valor": "5", "currency": "USD"}).json()
    por = {i["simbolo"]: i for i in d["itens"]}
    assert por["BTC"]["estado"] == "dispararia" and por["TTWO"]["estado"] == "dispararia"
    assert por["BTC"]["atual"] == "10.00" and por["BTC"]["alvo"] == "5.00"
    d = cliente_logado.get(url, {"tipo": "queda_dia", "valor": "5", "currency": "USD"}).json()
    por = {i["simbolo"]: i for i in d["itens"]}
    assert (
        por["TTWO"]["estado"] == "ok"
        and por["TTWO"]["alvo"] == "-5.00"
        and por["TTWO"]["distancia"] == "-15.00"
    )
    # preço absoluto só do TTWO em USD: 110 < 120 → dispararia
    d = cliente_logado.get(
        url, {"tipo": "abaixo", "valor": "120", "currency": "USD", "ativo": carteira["ttwo"].pk}
    ).json()
    assert [i["simbolo"] for i in d["itens"]] == ["TTWO"] and d["itens"][0]["estado"] == "dispararia"
    assert d["itens"][0]["unidade"] == "USD" and "recomendação" in d["texto"]
    # moeda diferente da cotação → não avaliável
    d = cliente_logado.get(
        url, {"tipo": "abaixo", "valor": "500", "currency": "BRL", "ativo": carteira["ttwo"].pk}
    ).json()
    assert d["itens"][0]["estado"] == "nao_avaliavel"
    assert cliente_logado.get(url, {"tipo": "xpto", "valor": "1"}).status_code == 400
    assert cliente_logado.get(url, {"tipo": "alta_dia", "valor": "abc"}).json()["itens"] == []


def test_tela_de_alerta_tem_previa(cliente_logado, carteira):
    html = cliente_logado.get(reverse("regra_nova")).content.decode()
    assert 'id="previa-regra"' in html and reverse("regra_previa") in html


def test_lista_de_ativos_trunca_nome_e_sobre_recolhe(cliente_logado, carteira):
    assert 'class="truncar"' in cliente_logado.get(reverse("asset_list")).content.decode()
    assert (
        "<details><summary>detalhes</summary>" in cliente_logado.get(reverse("sobre_dados")).content.decode()
    )
