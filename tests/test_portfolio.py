"""Carteira: modelos, revisões de lote, validações por classe e telas de cadastro."""

import datetime as dt
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.portfolio import services
from apps.portfolio.models import Asset, FxOrigin, Lot, LotRevision

D = Decimal
HOJE = dt.date(2026, 10, 29)


@pytest.fixture
def ttwo(db):
    return Asset.objects.create(symbol="ttwo", name="Take-Two Interactive", market="NASDAQ", custody="AVENUE")


@pytest.fixture
def btc(db):
    return Asset.objects.create(symbol="BTC", name="Bitcoin", market="CRIPTO", custody="BINANCE")


@pytest.fixture
def petr4(db):
    return Asset.objects.create(symbol="PETR4", name="Petrobras PN", market="B3", custody="OUTRA")


# --- Ativo -------------------------------------------------------------------------


def test_ativo_deriva_classe_e_moeda_do_mercado(ttwo, btc, petr4):
    assert (ttwo.symbol, ttwo.asset_class, ttwo.native_currency) == ("TTWO", "acao", "USD")
    assert (btc.asset_class, btc.native_currency) == ("cripto", "USD")
    assert (petr4.asset_class, petr4.native_currency) == ("acao", "BRL")


def test_simbolo_unico_por_mercado(ttwo):
    from django.db import IntegrityError

    with pytest.raises(IntegrityError):
        Asset.objects.create(symbol="TTWO", name="dup", market="NASDAQ", custody="AVENUE")


# --- Lotes -------------------------------------------------------------------------


def test_criar_lote_com_cambio_informado(ttwo, usuario):
    lot = services.criar_lote(
        ttwo, usuario, trade_date=HOJE, quantity=D("10"), unit_price=D("150.25"), fx_rate=D("5.4321")
    )
    assert lot.fx_origin == FxOrigin.INFORMED
    assert lot.revision == 1
    assert lot.invested == D("1502.50")
    assert lot.created_by == usuario


def test_criar_lote_sem_cambio_fica_pendente(ttwo, usuario):
    lot = services.criar_lote(ttwo, usuario, trade_date=HOJE, quantity=D("1.5"), unit_price=D("100"))
    assert lot.fx_origin == FxOrigin.PENDING
    assert lot.fx_rate is None


def test_cripto_aceita_quantidade_fracionaria(btc, usuario):
    lot = services.criar_lote(
        btc, usuario, trade_date=HOJE, quantity=D("0.0123456789"), unit_price=D("65000")
    )
    assert lot.quantity == D("0.0123456789")


def test_b3_exige_quantidade_inteira(petr4, usuario):
    with pytest.raises(ValidationError) as exc:
        services.criar_lote(petr4, usuario, trade_date=HOJE, quantity=D("10.5"), unit_price=D("30"))
    assert "quantity" in exc.value.message_dict
    lot = services.criar_lote(petr4, usuario, trade_date=HOJE, quantity=D("100"), unit_price=D("30"))
    assert lot.quantity == D("100")
    assert lot.fx_origin == FxOrigin.NOT_APPLICABLE


def test_b3_nao_aceita_cambio_de_compra(petr4, usuario):
    with pytest.raises(ValidationError) as exc:
        services.criar_lote(
            petr4, usuario, trade_date=HOJE, quantity=D("1"), unit_price=D("30"), fx_rate=D("5")
        )
    assert "fx_rate" in exc.value.message_dict


@pytest.mark.parametrize(
    "dados, campo",
    [
        ({"quantity": D("0"), "unit_price": D("1")}, "quantity"),
        ({"quantity": D("1"), "unit_price": D("0")}, "unit_price"),
        ({"quantity": D("1"), "unit_price": D("1"), "fx_rate": D("-1")}, "fx_rate"),
        ({"quantity": D("1"), "unit_price": D("1"), "trade_date": dt.date(2999, 1, 1)}, "trade_date"),
    ],
)
def test_lote_rejeita_valores_invalidos(ttwo, usuario, dados, campo):
    dados.setdefault("trade_date", HOJE)
    with pytest.raises(ValidationError) as exc:
        services.criar_lote(ttwo, usuario, **dados)
    assert campo in exc.value.message_dict


def test_alterar_lote_gera_revisao_com_estado_anterior(ttwo, usuario):
    lot = services.criar_lote(ttwo, usuario, trade_date=HOJE, quantity=D("10"), unit_price=D("150"))
    services.alterar_lote(lot, usuario, reason="corrigir preço", unit_price=D("151"))
    lot.refresh_from_db()
    assert lot.revision == 2
    assert lot.unit_price == D("151")
    rev = LotRevision.objects.get(lot=lot)
    assert rev.number == 1
    assert rev.previous["unit_price"] == "150.00000000"
    assert rev.reason == "corrigir preço"
    assert rev.changed_by == usuario


def test_alterar_lote_usa_estado_do_banco_mesmo_se_objeto_ja_mudou(ttwo, usuario):
    lot = services.criar_lote(ttwo, usuario, trade_date=HOJE, quantity=D("10"), unit_price=D("150"))
    lot.unit_price = D("999")  # simula formulário que alterou o objeto antes do serviço
    services.alterar_lote(lot, usuario, unit_price=D("151"))
    assert LotRevision.objects.get(lot=lot).previous["unit_price"] == "150.00000000"


def test_cancelar_lote_e_logico_e_sai_do_calculo(ttwo, usuario):
    a = services.criar_lote(ttwo, usuario, trade_date=HOJE, quantity=D("10"), unit_price=D("100"))
    b = services.criar_lote(ttwo, usuario, trade_date=HOJE, quantity=D("10"), unit_price=D("120"))
    assert services.posicao_do_ativo(ttwo).preco_medio == D("110")
    services.cancelar_lote(b, usuario)
    b.refresh_from_db()
    assert b.is_cancelled and b.revision == 2
    assert Lot.objects.filter(asset=ttwo).count() == 2  # nada apagado
    assert services.posicao_do_ativo(ttwo).preco_medio == D("100")
    with pytest.raises(ValueError):
        services.alterar_lote(b, usuario, unit_price=D("1"))
    assert a.revision == 1


def test_consolidado_por_moeda_separa_usd_de_brl(ttwo, petr4, btc, usuario):
    services.criar_lote(ttwo, usuario, trade_date=HOJE, quantity=D("10"), unit_price=D("100"))
    services.criar_lote(btc, usuario, trade_date=HOJE, quantity=D("0.1"), unit_price=D("60000"))
    services.criar_lote(petr4, usuario, trade_date=HOJE, quantity=D("100"), unit_price=D("30"))
    linhas = services.linhas_da_carteira()
    cons = services.consolidar_por_moeda(linhas)
    assert set(cons) == {"BRL", "USD"}
    assert cons["USD"].investido_total == D("7000")
    assert cons["BRL"].investido_total == D("3000")
    assert cons["USD"].posicoes_avaliadas == 0  # sem cotações na E2
    assert cons["USD"].retorno_pct is None


def test_linhas_com_precos_avaliam(ttwo, usuario):
    services.criar_lote(ttwo, usuario, trade_date=HOJE, quantity=D("10"), unit_price=D("100"))
    linhas = services.linhas_da_carteira({ttwo.id: (D("110"), D("105"))})
    assert linhas[0].avaliacao.resultado == D("100")
    assert linhas[0].avaliacao.retorno_pct == D("10")


# --- Telas --------------------------------------------------------------------------


def test_fluxo_cadastro_pelas_telas(cliente_logado):
    r = cliente_logado.post(
        reverse("asset_create"),
        {
            "symbol": "ttwo",
            "name": "Take-Two",
            "market": "NASDAQ",
            "custody": "AVENUE",
            "sector": "",
            "is_active": "on",
            "notes": "",
        },
    )
    assert r.status_code == 302
    asset = Asset.objects.get(symbol="TTWO")
    r = cliente_logado.post(
        reverse("lot_create", args=[asset.pk]),
        {
            "trade_date": "2026-10-01",
            "quantity": "10",
            "unit_price": "150.25",
            "fx_rate": "5.4321",
            "costs": "",
            "notes": "remessa",
        },
    )
    assert r.status_code == 302
    lot = asset.lots.get()
    assert lot.fx_origin == FxOrigin.INFORMED
    html = cliente_logado.get(reverse("asset_detail", args=[asset.pk])).content.decode()
    assert "US$ 150,25" in html
    assert "US$ 1.502,50" in html
    assert "Informado pelo usuário" in html
    # alteração pela tela gera revisão com o estado anterior correto
    r = cliente_logado.post(
        reverse("lot_update", args=[lot.pk]),
        {
            "trade_date": "2026-10-01",
            "quantity": "10",
            "unit_price": "151",
            "fx_rate": "5.4321",
            "costs": "",
            "notes": "remessa",
            "motivo": "ajuste",
        },
    )
    assert r.status_code == 302
    assert LotRevision.objects.get(lot=lot).previous["unit_price"] == "150.25000000"
    # cancelamento só por POST
    assert cliente_logado.get(reverse("lot_cancel", args=[lot.pk])).status_code == 405
    assert cliente_logado.post(reverse("lot_cancel", args=[lot.pk])).status_code == 302
    lot.refresh_from_db()
    assert lot.is_cancelled
    # dashboard continua respondendo e mostra "indisponível" para preço
    home = cliente_logado.get(reverse("home_classico")).content.decode()
    assert "indisponível" in home or "Nenhuma posição" in home


def test_b3_na_tela_oculta_campo_de_cambio_e_valida_inteiro(cliente_logado, petr4):
    html = cliente_logado.get(reverse("lot_create", args=[petr4.pk])).content.decode()
    assert "fx_rate" not in html
    r = cliente_logado.post(
        reverse("lot_create", args=[petr4.pk]),
        {"trade_date": "2026-10-01", "quantity": "10.5", "unit_price": "30", "costs": "", "notes": ""},
    )
    assert r.status_code == 200
    assert "quantidade inteira" in r.content.decode()
    assert petr4.lots.count() == 0


def test_dashboard_mostra_totais_por_moeda(cliente_logado, ttwo, petr4, usuario):
    services.criar_lote(ttwo, usuario, trade_date=HOJE, quantity=D("10"), unit_price=D("100"))
    services.criar_lote(petr4, usuario, trade_date=HOJE, quantity=D("100"), unit_price=D("30"))
    html = cliente_logado.get(reverse("home_classico")).content.decode()
    assert "US$ 1.000,00" in html
    assert "R$ 3.000,00" in html
    assert "0 de 1 posições com preço" in html
    assert "sem cotação" in html


def test_telas_exigem_login(client, ttwo):
    for nome, args in [
        ("asset_list", []),
        ("asset_create", []),
        ("asset_detail", [ttwo.pk]),
        ("lot_create", [ttwo.pk]),
    ]:
        assert client.get(reverse(nome, args=args)).status_code == 302
