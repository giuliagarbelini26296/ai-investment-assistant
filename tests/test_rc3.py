"""rc3: resumo/evolução/sparklines, atualização ao vivo, coleta forçada, resumo diário, vigia, importação CSV."""

import datetime as dt
import json
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse

from apps.alerts import notify, resumo_diario, vigia
from apps.alerts.models import Notification, OpsIncident
from apps.marketdata import collector
from apps.marketdata.models import CollectionRun, DailyBar, FxDailyRate, Quote, ScheduleState
from apps.portfolio import importacao, services
from apps.portfolio.models import Asset, Lot
from apps.ui import resumo

D = Decimal
SP = ZoneInfo("America/Sao_Paulo")
AGORA = dt.datetime(2026, 10, 29, 15, 0, tzinfo=dt.UTC)  # qui 12:00 SP; NYSE aberta
CFG = {
    "TELEGRAM_BOT_TOKEN": "123:ABC",
    "TELEGRAM_CHAT_ID": "42",
    "FEED_TOKEN": "feed",
    "URL_PORTAL": "http://portal.local",
    "RESUMO_HORARIOS": "08:00,18:30",
}


@pytest.fixture
def carteira(db, usuario):
    ttwo = Asset.objects.create(symbol="TTWO", name="Take-Two", market="NASDAQ", custody="AVENUE")
    btc = Asset.objects.create(symbol="BTC", name="Bitcoin", market="CRIPTO", custody="BINANCE")
    services.criar_lote(
        ttwo, usuario, trade_date=dt.date(2026, 10, 1), quantity=D("10"), unit_price=D("100"), fx_rate=D("5")
    )
    services.criar_lote(
        btc,
        usuario,
        trade_date=dt.date(2026, 10, 20),
        quantity=D("0.1"),
        unit_price=D("60000"),
        fx_rate=D("5"),
    )
    for i in range(30):
        data = dt.date(2026, 9, 30) + dt.timedelta(days=i)
        if data.weekday() < 5:
            FxDailyRate.objects.create(pair="USD-BRL", date=data, bid=D("5"), source="t", collected_at=AGORA)
            DailyBar.objects.create(
                asset=ttwo,
                session_date=data,
                time_basis="sessao_local",
                close=D("100") + i,
                source="t",
                collected_at=AGORA,
            )
        DailyBar.objects.create(
            asset=btc,
            session_date=data,
            time_basis="dia_utc",
            close=D("60000") + 100 * i,
            source="binance",
            collected_at=AGORA,
        )
    Quote.objects.create(
        asset=ttwo,
        price=D("110"),
        currency="USD",
        previous_close=D("100"),
        collected_at=AGORA,
        market_time=AGORA - dt.timedelta(minutes=1),
        source="t",
        quote_type="atrasado",
        declared_delay_minutes=15,
    )
    Quote.objects.create(
        asset=btc,
        price=D("66000"),
        currency="USDT",
        change_24h_pct=D("10"),
        collected_at=AGORA,
        market_time=AGORA,
        source="binance",
        quote_type="spot",
    )
    return {"ttwo": ttwo, "btc": btc}


# --- Resumo, evolução e sparklines -------------------------------------------------------------------


def test_resultado_do_dia_e_alocacao(carteira):
    visao = services.visao_da_carteira("USD", AGORA)
    dia = resumo.resumo_do_dia(visao)
    # TTWO: 10 × (110 − 100) = 100 ; BTC: 6600 − 6000 = 600
    assert dia.valor == D("700") and dia.posicoes == 2
    assert dia.pct.quantize(D("0.01")) == D("10.00")  # 700 / 7000
    aloc = resumo.alocacao(visao)
    assert [a["simbolo"] for a in aloc] == ["BTC", "TTWO"]
    assert sum(a["pct"] for a in aloc).quantize(D("0.01")) == D("100.00")


def test_evolucao_patrimonio_repete_ultimo_fechamento_e_converte(carteira):
    ev = resumo.evolucao_patrimonio("BRL", dias=15, agora=AGORA)
    assert ev.moeda == "BRL" and len(ev.pontos) == 15
    ultimo = ev.pontos[-1]
    assert ultimo.data == dt.date(2026, 10, 29) and ultimo.ativos == 2
    # 29/10 = índice 29: TTWO fecha 129 × 10 × 5 = 6450; BTC 62900 × 0,1 × 5 = 31450
    assert ultimo.valor == D("37900")
    sab = next(p for p in ev.pontos if p.data == dt.date(2026, 10, 24))
    assert sab.repetidos == 1  # TTWO sem fechamento no sábado → fechamento de sexta
    assert any("repetem o último fechamento" in a for a in ev.avisos)
    antes_btc = next(p for p in ev.pontos if p.data == dt.date(2026, 10, 19))
    assert antes_btc.ativos == 1  # BTC comprado em 20/10 ainda não entra
    js = resumo.evolucao_como_json(ev)
    assert js["pontos"][-1]["valor"] == "37900.00" and "sem interpolação" in js["metodo"]


def test_evolucao_sem_ptax_exclui_posicao_em_outra_moeda(carteira):
    FxDailyRate.objects.all().delete()
    ev = resumo.evolucao_patrimonio("BRL", dias=5, agora=AGORA)
    assert ev.pontos == [] or all(p.ativos == 0 for p in ev.pontos)
    assert any("sem PTAX" in a for a in ev.avisos)
    ev_usd = resumo.evolucao_patrimonio("USD", dias=5, agora=AGORA)
    assert ev_usd.pontos and ev_usd.pontos[-1].ativos == 2  # moeda nativa: não precisa de câmbio


def test_sparkline():
    assert resumo.sparkline_svg([D("1")]) == ""
    svg = resumo.sparkline_svg([D("1"), D("2"), D("3")])
    assert svg.startswith("<svg") and 'class="sparkline alta"' in svg and "polyline" in svg
    assert 'class="sparkline queda"' in resumo.sparkline_svg([D("3"), D("1")])
    assert "points=" in resumo.sparkline_svg([D("5"), D("5"), D("5")])  # amplitude zero não divide por zero


# --- Telas: fragmento ao vivo, evolução JSON, atualizar agora ------------------------------------------


def test_home_e_fragmento(cliente_logado, carteira, monkeypatch):
    # Janela de 01–30/10: 30 dias após a primeira compra; último fechamento pode repetir.
    monkeypatch.setattr("django.utils.timezone.now", lambda: AGORA + dt.timedelta(days=1))
    html = cliente_logado.get(reverse("home_classico")).content.decode()
    assert 'id="carteira"' in html and "Atualizar agora" in html and "Patrimônio em BRL" in html
    assert 'id="alternar-tema"' in html and "data-theme" in html
    assert "sparkline" in html and 'id="grafico-evolucao"' in html and 'id="dados-alocacao"' in html
    frag = cliente_logado.get(reverse("carteira_fragmento")).content.decode()
    assert "fragmento-meta" in frag and "<html" not in frag and "Hoje" in frag
    js = cliente_logado.get(reverse("evolucao_json"), {"dias": "30"}).json()
    assert js["moeda"] == "BRL" and len(js["pontos"]) == 30
    assert cliente_logado.get(reverse("evolucao_json"), {"dias": "x"}).json()["dias"] == 90


def test_atualizar_agora_marca_tarefas(cliente_logado, carteira):
    from django.utils import timezone

    agora = timezone.now()
    ScheduleState.objects.update_or_create(
        task="CRIPTO", defaults={"last_attempt_at": agora - dt.timedelta(minutes=1)}
    )
    assert collector.tarefa_devida("CRIPTO", agora)[1] == "aguardando intervalo"
    r = cliente_logado.post(reverse("coletar_agora"), HTTP_X_REQUESTED_WITH="fetch")
    assert r.status_code == 200 and r.json()["solicitadas"] == 4
    estado = ScheduleState.objects.get(task="CRIPTO")
    assert collector.coleta_forcada(estado)
    devida, motivo = collector.tarefa_devida("CRIPTO", agora, estado)
    assert devida and motivo == "cripto 24/7"
    # depois de tentar, o pedido está atendido
    estado.last_attempt_at = timezone.now() + dt.timedelta(seconds=1)
    estado.save()
    assert not collector.coleta_forcada(estado)
    assert (
        collector.tarefa_devida("CRIPTO", estado.last_attempt_at + dt.timedelta(minutes=1), estado)[1]
        == "aguardando intervalo"
    )
    assert cliente_logado.post(reverse("coletar_agora")).status_code == 302


def test_intervalo_cripto_5_min(carteira):
    estado, _ = ScheduleState.objects.update_or_create(task="CRIPTO", defaults={"last_attempt_at": AGORA})
    assert collector.tarefa_devida("CRIPTO", AGORA + dt.timedelta(minutes=4), estado)[0] is False
    assert collector.tarefa_devida("CRIPTO", AGORA + dt.timedelta(minutes=5), estado)[0] is True
    estado_ny, _ = ScheduleState.objects.update_or_create(task="NYSE", defaults={"last_attempt_at": AGORA})
    assert collector.tarefa_devida("NYSE", AGORA + dt.timedelta(minutes=5), estado_ny)[0] is False
    assert collector.tarefa_devida("NYSE", AGORA + dt.timedelta(minutes=15), estado_ny)[0] is True


# --- Resumo diário -------------------------------------------------------------------------------------


def test_slot_devido():
    h = resumo_diario.horarios
    assert [t.strftime("%H:%M") for t in h()] in (["08:00", "18:30"], [])  # depende do settings padrão
    cedo = dt.datetime(2026, 10, 29, 7, 59, tzinfo=SP)
    manha = dt.datetime(2026, 10, 29, 8, 0, tzinfo=SP)
    noite = dt.datetime(2026, 10, 29, 19, 0, tzinfo=SP)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(resumo_diario, "horarios", lambda: [dt.time(8, 0), dt.time(18, 30)])
        assert resumo_diario.slot_devido(cedo, "") is None
        assert resumo_diario.slot_devido(manha, "") == "2026-10-29 08:00"
        assert resumo_diario.slot_devido(manha, "2026-10-29 08:00") is None
        assert resumo_diario.slot_devido(noite, "2026-10-29 08:00") == "2026-10-29 18:30"
        assert resumo_diario.slot_devido(noite, "2026-10-29 18:30") is None
        assert (
            resumo_diario.slot_devido(manha + dt.timedelta(days=1), "2026-10-29 18:30") == "2026-10-30 08:00"
        )


def test_texto_resumo_e_envio_unico(settings, carteira):
    settings.NOTIFICACOES = dict(CFG)
    titulo, corpo = resumo_diario.texto_resumo(AGORA)
    assert titulo.startswith("Resumo da carteira")
    assert "BRL: patrimônio R$ 38.500,00" in corpo  # (1100 + 6600) × 5
    assert "USD: patrimônio US$ 7.700,00" in corpo and "hoje US$ 700,00 (+10,00%)" in corpo
    assert "Altas: BTC +10,00%, TTWO +10,00%" in corpo
    assert "Alertas ativos: nenhum" in corpo and "Não constitui recomendação" in corpo
    noite = dt.datetime(2026, 10, 29, 18, 31, tzinfo=SP)
    assert resumo_diario.enviar_resumo_se_devido(noite) is True
    assert resumo_diario.enviar_resumo_se_devido(noite + dt.timedelta(minutes=30)) is False
    assert Notification.objects.filter(kind="resumo").count() == 2  # telegram + mac
    assert ScheduleState.objects.get(task="RESUMO").last_slot == "2026-10-29 18:30"


def test_resumo_sem_canais_nao_enfileira(settings, carteira):
    settings.NOTIFICACOES = {**CFG, "TELEGRAM_BOT_TOKEN": "", "FEED_TOKEN": ""}
    assert resumo_diario.enviar_resumo_se_devido(dt.datetime(2026, 10, 29, 9, 0, tzinfo=SP)) is False
    assert Notification.objects.count() == 0


# --- Vigia ---------------------------------------------------------------------------------------------


def _run(tarefa, status, quando, erro=""):
    return CollectionRun.objects.create(
        started_at=quando,
        finished_at=quando,
        source="t",
        market=tarefa,
        status=status,
        error=erro,
        attempted=1,
    )


def test_vigia_abre_e_resolve_incidente_de_falhas(settings, carteira):
    settings.NOTIFICACOES = dict(CFG)
    for i in range(3):
        _run("CRIPTO", "falha", AGORA - dt.timedelta(minutes=15 * (3 - i)), erro="rede: timeout")
    mudados = vigia.verificar(AGORA)
    assert len(mudados) == 1 and mudados[0].key == "falhas:CRIPTO" and mudados[0].resolved_at is None
    n = Notification.objects.get(kind="operacao", channel="telegram")
    assert "3 coletas seguidas falharam" in n.body and "timeout" in n.body
    assert vigia.verificar(AGORA + dt.timedelta(minutes=5)) == []  # não repete
    _run("CRIPTO", "completo", AGORA + dt.timedelta(minutes=10))
    mudados = vigia.verificar(AGORA + dt.timedelta(minutes=11))
    assert len(mudados) == 1 and mudados[0].resolved_at is not None
    assert Notification.objects.filter(kind="operacao", title="Coleta normalizada").count() == 2
    assert OpsIncident.objects.filter(resolved_at__isnull=True).count() == 0


def test_vigia_detecta_coleta_parada(settings, carteira):
    settings.NOTIFICACOES = dict(CFG)
    ScheduleState.objects.update_or_create(
        task="CRIPTO",
        defaults={
            "last_attempt_at": AGORA - dt.timedelta(minutes=50),
            "last_success_at": AGORA - dt.timedelta(minutes=50),
        },
    )
    mudados = vigia.verificar(AGORA)
    assert [m.key for m in mudados] == ["parado:CRIPTO"]
    assert "sem coleta bem-sucedida há 50 min" in mudados[0].message


def test_vigia_sem_canais_registra_sem_notificar(settings, carteira):
    settings.NOTIFICACOES = {**CFG, "TELEGRAM_BOT_TOKEN": "", "FEED_TOKEN": ""}
    for i in range(3):
        _run("NYSE", "falha", AGORA - dt.timedelta(minutes=15 * (3 - i)))
    inc = vigia.verificar(AGORA)[0]
    assert inc.notified_at is None and Notification.objects.count() == 0


def test_passagem_do_coletor_roda_rotinas_de_aviso(settings, carteira, monkeypatch):
    settings.NOTIFICACOES = dict(CFG)
    chamadas = []
    monkeypatch.setattr(vigia, "verificar", lambda agora: chamadas.append("vigia") or [])
    monkeypatch.setattr(
        resumo_diario, "enviar_resumo_se_devido", lambda agora: chamadas.append("resumo") or False
    )
    monkeypatch.setattr(notify, "enviar_pendentes", lambda **kw: chamadas.append("envio") or 0)
    collector.executar_tarefas_devidas(dt.datetime(2026, 10, 31, 3, 0, tzinfo=dt.UTC), dormir=lambda s: None)
    assert chamadas[-3:] == ["resumo", "vigia", "envio"]


def test_telegram_envia_resumo_e_operacao(settings, carteira):
    import httpx

    settings.NOTIFICACOES = dict(CFG)
    notify.enfileirar_texto("resumo", "Resumo", "corpo do resumo")
    notify.enfileirar_texto("operacao", "Coleta com problema", "detalhe")
    enviados = []
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda r: enviados.append(json.loads(r.content)) or httpx.Response(200, json={"ok": True})
        )
    )
    assert notify.enviar_pendentes(client=client, agora=AGORA) == 2
    textos = [e["text"] for e in enviados]
    assert any(t.startswith("📊 Resumo\ncorpo do resumo") for t in textos)
    assert any(t.startswith("🛠 Coleta com problema\ndetalhe") for t in textos)
    assert all("/alertas/" not in t for t in textos)  # link só em alertas de regra
    feed = notify.feed_mac()
    assert len(feed) == 2 and feed[0]["titulo"] == "Resumo"


# --- Importação CSV -----------------------------------------------------------------------------------


CSV_GENERICO = b"""simbolo,data,quantidade,preco,moeda,cambio,mercado
TTWO,2026-03-10,10,150.25,USD,5.12,
TTWO,10/03/2026,"5","151,00",,,
BTC,2026-05-02,0.015,61000,USD,,
PETR4,2026-05-02,100,"38,50",BRL,,B3
PETR4,2026-05-03,10.5,38,BRL,,B3
NOVO,2026-05-02,1,10,,,
XPTO,2030-01-01,1,10,,,NYSE
"""

CSV_BINANCE = b"""Date(UTC),Pair,Side,Price,Executed,Amount,Fee
2026-05-02 14:03:11,BTCUSDT,BUY,61000.50,0.01500000BTC,915.0075USDT,0.00001500BTC
2026-05-03 09:00:00,BTCUSDT,SELL,62000,0.00500000BTC,310USDT,0.31USDT
2026-05-04 09:00:00,ETHUSDT,BUY,3000,1.5ETH,4500USDT,0ETH
2026-05-05 09:00:00,BTCBRL,BUY,350000,0.001BTC,350BRL,0BTC
"""


def test_importacao_generico_previa_e_confirmacao(carteira, usuario):
    previa = importacao.pre_visualizar(CSV_GENERICO, custodia_padrao="AVENUE")
    assert previa.formato == "generico" and len(previa.linhas) == 7
    ok = {li.n: li for li in previa.validas}
    erros = {li.n: li.erro for li in previa.com_erro}
    assert set(ok) == {2, 3, 4, 5}
    assert ok[2].cambio == D("5.12") and ok[3].preco == D("151.00") and ok[3].moeda == "USD"
    assert ok[5].criar_ativo and ok[5].mercado == "B3" and ok[5].moeda == "BRL" and ok[5].custodia == "AVENUE"
    assert "quantidade inteira" in erros[6]
    assert "informe a coluna mercado" in erros[7]
    assert "futuro" in erros[8]
    compras, ativos = importacao.confirmar(importacao.serializar(previa), usuario)
    assert compras == 4 and ativos == 1
    assert Asset.objects.get(symbol="PETR4").market == "B3"
    lote = Lot.objects.get(asset__symbol="TTWO", trade_date=dt.date(2026, 3, 10), quantity=D("10"))
    assert lote.fx_rate == D("5.12") and lote.fx_origin == "informado" and "CSV" in lote.notes


def test_importacao_binance(carteira, usuario):
    previa = importacao.pre_visualizar(CSV_BINANCE)
    assert previa.formato == "binance"
    assert [li.n for li in previa.ignoradas] == [3]
    validas = {li.simbolo: li for li in previa.validas}
    assert set(validas) == {"BTC", "ETH"}
    assert validas["BTC"].quantidade == D("0.015") and validas["BTC"].preco == D("61000.50")
    assert (
        validas["BTC"].moeda == "USD"
        and "USDT" in validas["BTC"].observacao
        and "taxa" in validas["BTC"].observacao
    )
    assert (
        validas["ETH"].criar_ativo
        and validas["ETH"].mercado == "CRIPTO"
        and validas["ETH"].custodia == "BINANCE"
    )
    assert any("BRL" in li.erro for li in previa.com_erro)  # BTCBRL: moeda ≠ nativa do ativo cripto (USD)
    compras, ativos = importacao.confirmar(importacao.serializar(previa), usuario)
    assert compras == 2 and ativos == 1 and Asset.objects.filter(symbol="ETH", market="CRIPTO").exists()


def test_importacao_cabecalho_desconhecido_e_tela(cliente_logado, carteira):
    previa = importacao.pre_visualizar(b"a,b,c\n1,2,3\n")
    assert previa.formato == "desconhecido" and "não reconhecido" in previa.erro_geral
    from django.core.files.uploadedfile import SimpleUploadedFile

    url = reverse("importar_compras")
    assert "Formatos aceitos" in cliente_logado.get(url).content.decode()
    r = cliente_logado.post(
        url, {"arquivo": SimpleUploadedFile("c.csv", CSV_BINANCE, content_type="text/csv")}
    )
    html = r.content.decode()
    assert r.status_code == 200 and "Confirmar 2 compra(s)" in html and "ignorada" in html
    antes = Lot.objects.count()
    r = cliente_logado.post(url, {"acao": "confirmar"})
    assert r.status_code == 302 and Lot.objects.count() == antes + 2
    r = cliente_logado.post(url, {"acao": "confirmar"})  # sessão já consumida
    assert r.status_code == 302 and Lot.objects.count() == antes + 2


# --- Fonte sem chave: tarefa pausada, aviso na carteira ------------------------------------------------


def test_mercado_sem_chave_fica_pausado_e_avisa(settings, cliente_logado, carteira):
    settings.PORTAL = {**settings.PORTAL, "MODO_DEMO": False}
    settings.FONTES = {"CHAVES": {"twelvedata": "", "brapi": "", "awesomeapi": "", "coingecko": ""}}
    devida, motivo = collector.tarefa_devida("NYSE", AGORA)
    assert devida is False and "TWELVEDATA_API_KEY" in motivo
    assert collector.tarefa_devida("CRIPTO", AGORA)[0] is True  # Binance não exige chave
    html = cliente_logado.get(reverse("home_classico")).content.decode()
    assert "TTWO sem cotação: a fonte exige chave" in html
    settings.FONTES = {"CHAVES": {"twelvedata": "k", "brapi": "", "awesomeapi": "", "coingecko": ""}}
    assert collector.tarefa_devida("NYSE", AGORA)[0] is True
    assert "exige chave" not in cliente_logado.get(reverse("home_classico")).content.decode()


# --- Painel de alertas ao vivo e ações rápidas; alerta pelo gráfico ----------------------------------


def test_painel_fragmento_e_acoes_rapidas(cliente_logado, carteira, usuario):
    from apps.alerts import services as alerts
    from apps.alerts.models import Alert, Rule

    from .test_alerts import cotar, regra

    ttwo = carteira["ttwo"]
    rule = regra(usuario, ttwo, classification="aviso")
    cotar(ttwo, "80", dt.datetime.now(dt.UTC))
    alerta = alerts.avaliar_ativo(ttwo)[0]
    html = cliente_logado.get(reverse("alertas")).content.decode()
    assert (
        'id="painel-alertas"' in html and "🔕 24 h" in html and "✏️ limite" in html and "pausar regra" in html
    )
    frag = cliente_logado.get(reverse("alertas_fragmento")).content.decode()
    assert "fragmento-meta" in frag and 'data-ativos="1"' in frag and 'data-nao-vistos="1"' in frag
    # silenciar 24 h via fetch → JSON; some do contador de não vistos; continua ativo
    r = cliente_logado.post(
        reverse("alerta_silenciar", args=[alerta.pk]), {"horas": "24"}, HTTP_X_REQUESTED_WITH="fetch"
    )
    assert r.status_code == 200 and r.json()["ok"] and "silenciado por 24 h" in r.json()["mensagem"]
    alerta.refresh_from_db()
    assert (
        alerta.silenciado() and alerta.seen_at is not None and alerta.is_active and alerts.nao_vistos() == 0
    )
    assert 'data-nao-vistos="0"' in cliente_logado.get(reverse("alertas_fragmento")).content.decode()
    # ajustar limite: 10 → "7,5"
    r = cliente_logado.post(
        reverse("regra_limite", args=[rule.pk]), {"valor": "7,5"}, HTTP_X_REQUESTED_WITH="fetch"
    )
    assert r.status_code == 200 and "v2" in r.json()["mensagem"]
    rule.refresh_from_db()
    assert rule.versao_atual.threshold == D("7.5") and rule.versao_atual.reason == "ajuste rápido pelo painel"
    assert Alert.objects.get(pk=alerta.pk).resolved_at is not None
    r = cliente_logado.post(
        reverse("regra_limite", args=[rule.pk]), {"valor": "abc"}, HTTP_X_REQUESTED_WITH="fetch"
    )
    assert r.status_code == 400 and not r.json()["ok"]
    # pausar via fetch
    r = cliente_logado.post(reverse("regra_desativar", args=[rule.pk]), HTTP_X_REQUESTED_WITH="fetch")
    assert (
        r.status_code == 200
        and "pausada" in r.json()["mensagem"]
        and not Rule.objects.get(pk=rule.pk).is_active
    )
    # sem cabeçalho de fetch continua redirecionando (formulário comum)
    assert cliente_logado.post(reverse("regra_reativar", args=[rule.pk])).status_code == 302
    assert cliente_logado.post(reverse("alerta_silenciar", args=[9999])).status_code == 302


def test_alerta_pelo_grafico_prefill_e_linhas(cliente_logado, carteira, usuario):
    from .test_alerts import regra

    ttwo = carteira["ttwo"]
    regra(usuario, ttwo, family="preco_absoluto", condition="abaixo", threshold=D("90"), currency="USD")
    regra(
        usuario, None, family="preco_absoluto", condition="acima", threshold=D("600"), currency="BRL"
    )  # outra moeda: fora
    html = cliente_logado.get(reverse("asset_detail", args=[ttwo.pk])).content.decode()
    assert 'id="alerta-rapido"' in html and 'id="linhas-grafico"' in html
    linhas = json.loads(html.split('id="linhas-grafico">')[1].split("</script>")[0])
    assert [li["tipo"] for li in linhas] == ["medio", "abaixo"]
    assert linhas[0]["valor"] == "100.00" and linhas[1]["valor"].startswith("90")
    form = cliente_logado.get(
        reverse("regra_nova"), {"ativo": ttwo.pk, "tipo": "abaixo", "valor": "95,50", "currency": "USD"}
    ).content.decode()
    assert 'value="95,50"' in form and '<option value="abaixo" selected' in form
