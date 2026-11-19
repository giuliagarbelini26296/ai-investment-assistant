"""Coletor com relógio falso e adaptadores de teste. Sem rede."""

import datetime as dt
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from apps.marketdata import collector
from apps.marketdata.adapters.base import (
    AuthError,
    BaseAdapter,
    BaseFxAdapter,
    CotacaoNormalizada,
    FonteInfo,
    NetworkError,
    RateLimitError,
)
from apps.marketdata.adapters.demo import DemoAdapter, DemoFxAdapter
from apps.marketdata.models import (
    CollectionRun,
    DailyBar,
    FxDailyRate,
    FxRate,
    Quote,
    ScheduleState,
    SourceBudget,
)
from apps.portfolio.models import Asset

SP = ZoneInfo("America/Sao_Paulo")
NY = ZoneInfo("America/New_York")
D = Decimal
PREGAO_NY = dt.datetime(2026, 10, 29, 12, 0, tzinfo=NY)  # NYSE aberta; 13h00 SP


class FonteFalsa(BaseAdapter):
    """Adaptador controlável: devolve cotações fixas ou levanta a sequência de erros configurada."""

    info = FonteInfo(
        codigo="falsa",
        nome="falsa",
        tipo_dado="atrasado",
        atraso_declarado_min=15,
        atraso_declarado_texto="15",
        limites="",
        termos_url="",
        limite_dia=100,
    )
    moeda_cotacao = "USD"

    def __init__(self, precos=None, erros=(), horario=None, agora=None):
        super().__init__(client=None, agora=agora or (lambda: PREGAO_NY))
        self.precos = precos or {}
        self.erros = list(erros)
        self.horario = horario
        self.chamadas = 0

    def cotar(self, simbolos):
        self.chamadas += 1
        if self.erros:
            raise self.erros.pop(0)
        coleta = self.agora()
        brutas = [
            CotacaoNormalizada(
                simbolo=s,
                preco=self.precos[s],
                moeda="USD",
                horario_coleta=coleta,
                fonte="falsa",
                tipo="atrasado",
                horario_mercado=self.horario,
                fechamento_anterior=D("100"),
                atraso_declarado_min=15,
            )
            for s in simbolos
            if s in self.precos
        ]
        falhas = {s: "ausente na resposta (resposta parcial)" for s in simbolos if s not in self.precos}
        return self._finalizar(brutas, falhas)


@pytest.fixture
def ttwo(db):
    return Asset.objects.create(symbol="TTWO", name="Take-Two", market="NASDAQ", custody="AVENUE")


@pytest.fixture
def aapl(db):
    return Asset.objects.create(symbol="AAPL", name="Apple", market="NASDAQ", custody="AVENUE")


def sem_dormir(_s):
    pass


# --- Ciclo de mercado ----------------------------------------------------------------------


def test_ciclo_completo_grava_cotacoes(ttwo, aapl):
    fonte = FonteFalsa({"TTWO": D("181.23"), "AAPL": D("250")}, horario=PREGAO_NY - dt.timedelta(minutes=1))
    run = collector.executar_ciclo_mercado("NYSE", PREGAO_NY, adaptador=fonte, dormir=sem_dormir)
    assert run.status == "completo" and run.attempted == 2 and run.succeeded == 2
    q = Quote.objects.get(asset=ttwo)
    assert q.price == D("181.23") and q.source == "falsa" and q.quote_type == "atrasado" and q.run == run
    assert ScheduleState.objects.get(task="NYSE").last_success_at == PREGAO_NY
    assert SourceBudget.objects.get(source="falsa", period="dia").consumed == 2


def test_ciclo_parcial_registra_falhas_por_simbolo(ttwo, aapl):
    fonte = FonteFalsa({"TTWO": D("181.23")})
    run = collector.executar_ciclo_mercado("NYSE", PREGAO_NY, adaptador=fonte, dormir=sem_dormir)
    assert run.status == "parcial" and run.succeeded == 1 and run.failed == 1
    assert "ausente" in run.details["AAPL"]
    assert Quote.objects.filter(asset=aapl).count() == 0


def test_mesmo_horario_de_mercado_nao_gera_nova_cotacao(ttwo):
    h = PREGAO_NY - dt.timedelta(minutes=1)
    fonte = FonteFalsa({"TTWO": D("181.23")}, horario=h)
    collector.executar_ciclo_mercado("NYSE", PREGAO_NY, adaptador=fonte, dormir=sem_dormir)
    fonte2 = FonteFalsa({"TTWO": D("181.23")}, horario=h, agora=lambda: PREGAO_NY + dt.timedelta(minutes=15))
    run = collector.executar_ciclo_mercado(
        "NYSE", PREGAO_NY + dt.timedelta(minutes=15), adaptador=fonte2, dormir=sem_dormir
    )
    assert Quote.objects.filter(asset=ttwo).count() == 1
    assert "sem nova observação" in run.details["TTWO"]
    assert run.status == "completo"  # a fonte respondeu; só não houve negócio novo


def test_retentativa_em_erro_de_rede_e_sucesso(ttwo):
    fonte = FonteFalsa({"TTWO": D("1")}, erros=[NetworkError("timeout"), NetworkError("500")])
    run = collector.executar_ciclo_mercado("NYSE", PREGAO_NY, adaptador=fonte, dormir=sem_dormir)
    assert run.status == "completo" and run.attempts == 3 and fonte.chamadas == 3


def test_rate_limit_respeita_retry_after(ttwo):
    esperas = []
    fonte = FonteFalsa({"TTWO": D("1")}, erros=[RateLimitError("429", retry_after=7)])
    run = collector.executar_ciclo_mercado("NYSE", PREGAO_NY, adaptador=fonte, dormir=esperas.append)
    assert run.status == "completo" and esperas == [7]


def test_falha_persistente_apos_3_tentativas(ttwo):
    fonte = FonteFalsa({"TTWO": D("1")}, erros=[NetworkError("x")] * 3)
    run = collector.executar_ciclo_mercado("NYSE", PREGAO_NY, adaptador=fonte, dormir=sem_dormir)
    assert run.status == "falha" and run.attempts == 1 and "rede" in run.error
    assert Quote.objects.count() == 0
    estado = ScheduleState.objects.get(task="NYSE")
    assert estado.last_status == "falha" and estado.last_success_at is None


def test_erro_de_credencial_nao_retenta(ttwo):
    fonte = FonteFalsa({"TTWO": D("1")}, erros=[AuthError("401")])
    run = collector.executar_ciclo_mercado("NYSE", PREGAO_NY, adaptador=fonte, dormir=sem_dormir)
    assert run.status == "falha" and fonte.chamadas == 1 and "credencial" in run.error


def test_cota_excedida_pula_ciclo_sem_chamar_a_fonte(ttwo):
    SourceBudget.objects.create(
        source="falsa", period="dia", period_key="2026-10-29", limit=100, consumed=100
    )
    fonte = FonteFalsa({"TTWO": D("1")})
    run = collector.executar_ciclo_mercado("NYSE", PREGAO_NY, adaptador=fonte, dormir=sem_dormir)
    assert run.status == "pulado" and fonte.chamadas == 0 and "cota" in run.error


def test_sem_ativos_e_pulado(db):
    run = collector.executar_ciclo_mercado("B3", PREGAO_NY, adaptador=FonteFalsa(), dormir=sem_dormir)
    assert run.status == "pulado"


def test_coleta_de_fechamento_marca_sessao(ttwo):
    apos = dt.datetime(2026, 10, 29, 16, 30, tzinfo=NY)
    fonte = FonteFalsa(
        {"TTWO": D("1")}, agora=lambda: apos, horario=dt.datetime(2026, 10, 29, 16, 0, tzinfo=NY)
    )
    run = collector.executar_ciclo_mercado("NYSE", apos, adaptador=fonte, dormir=sem_dormir, fechamento=True)
    assert run.status == "completo"
    assert ScheduleState.objects.get(task="NYSE").last_close_date == dt.date(2026, 10, 29)


def test_cripto_com_demo_grava_spot(db):
    Asset.objects.create(symbol="BTC", name="Bitcoin", market="CRIPTO", custody="BINANCE")
    agora = dt.datetime(2026, 10, 29, 3, 0, tzinfo=dt.UTC)
    run = collector.executar_ciclo_mercado(
        "CRIPTO", agora, adaptador=DemoAdapter(agora=lambda: agora), dormir=sem_dormir
    )
    assert run.status == "completo"
    q = Quote.objects.get()
    assert q.quote_type == "spot" and q.source == "demo" and q.currency == "USD"


# --- Câmbio ----------------------------------------------------------------------------------


def test_ciclo_cambio_intradiario_e_ptax(db):
    agora = dt.datetime(2026, 10, 29, 14, 0, tzinfo=SP)
    fx = DemoFxAdapter(agora=lambda: agora)
    r1 = collector.executar_ciclo_cambio("intradiario", agora, adaptador=fx, dormir=sem_dormir)
    assert r1.status == "completo" and FxRate.objects.count() == 1
    r2 = collector.executar_ciclo_cambio("fechamento", agora, adaptador=fx, dormir=sem_dormir)
    assert r2.status == "completo"
    assert FxDailyRate.objects.get(date=dt.date(2026, 10, 29)).rate_type == "ptax_fechamento"
    assert ScheduleState.objects.get(task="CAMBIO_PTAX").last_close_date == dt.date(2026, 10, 29)


def test_ptax_em_dia_sem_cotacao_falha_sem_inventar(db):
    class SemPtax(BaseFxAdapter):
        info = DemoFxAdapter.info

        def __init__(self):
            super().__init__(client=None)

        def cambio_diario(self, data):
            return None

    agora = dt.datetime(2026, 10, 29, 14, 0, tzinfo=SP)
    run = collector.executar_ciclo_cambio("fechamento", agora, adaptador=SemPtax(), dormir=sem_dormir)
    assert run.status == "falha" and "sem PTAX" in run.error and FxDailyRate.objects.count() == 0


def test_historico_cambio_pula_fins_de_semana_e_nao_duplica(db):
    agora = dt.datetime(2026, 10, 29, 20, 0, tzinfo=SP)
    fx = DemoFxAdapter(agora=lambda: agora)
    n = collector.carregar_historico_cambio(7, agora, adaptador=fx)
    assert n == 5  # 23 (sex), 26, 27, 28, 29 — 24 e 25 são fim de semana
    assert collector.carregar_historico_cambio(7, agora, adaptador=fx) == 0


# --- Histórico ------------------------------------------------------------------------------------


def test_carregar_historico_upsert(ttwo):
    agora = dt.datetime(2026, 10, 29, 20, 0, tzinfo=dt.UTC)
    demo = DemoAdapter(agora=lambda: agora)
    assert collector.carregar_historico(ttwo, 10, agora, adaptador=demo) == 10
    assert collector.carregar_historico(ttwo, 10, agora, adaptador=demo) == 10
    assert DailyBar.objects.filter(asset=ttwo).count() == 10
    assert DailyBar.objects.get(asset=ttwo, session_date=agora.date()).provisional is True


# --- Agenda -----------------------------------------------------------------------------------------


def test_agenda_bolsa_aberta_respeita_intervalo(ttwo):
    devida, _ = collector.tarefa_devida("NYSE", PREGAO_NY)
    assert devida
    ScheduleState.objects.update_or_create(
        task="NYSE", defaults={"last_attempt_at": PREGAO_NY - dt.timedelta(minutes=5)}
    )
    assert collector.tarefa_devida("NYSE", PREGAO_NY)[0] is False
    assert collector.tarefa_devida("NYSE", PREGAO_NY + dt.timedelta(minutes=11))[0] is True


def test_agenda_bolsa_fechada_coleta_fechamento_uma_vez(ttwo):
    apos = dt.datetime(2026, 10, 29, 16, 5, tzinfo=NY)
    assert collector.tarefa_devida("NYSE", apos)[0] is False  # antes dos 10 min
    apos = dt.datetime(2026, 10, 29, 16, 11, tzinfo=NY)
    assert collector.tarefa_devida("NYSE", apos) == (True, "coleta de fechamento de 2026-10-29")
    ScheduleState.objects.update_or_create(
        task="NYSE", defaults={"last_close_date": dt.date(2026, 10, 29), "last_attempt_at": apos}
    )
    assert collector.tarefa_devida("NYSE", apos + dt.timedelta(minutes=30))[0] is False
    # duas horas após o fechamento, a consolidação é reconfirmada uma única vez
    revisao = apos + dt.timedelta(hours=3)
    assert collector.tarefa_devida("NYSE", revisao) == (True, "reconfirmação do fechamento de 2026-10-29")
    ScheduleState.objects.update_or_create(
        task="NYSE", defaults={"last_attempt_at": revisao, "last_success_at": revisao}
    )
    assert collector.tarefa_devida("NYSE", revisao + dt.timedelta(hours=1))[0] is False
    # sábado: o fechamento de sexta 30/10 ainda não foi coletado → devida uma vez
    sab = dt.datetime(2026, 10, 31, 12, 0, tzinfo=NY)
    assert collector.tarefa_devida("NYSE", sab) == (True, "coleta de fechamento de 2026-10-30")
    ScheduleState.objects.update_or_create(task="NYSE", defaults={"last_close_date": dt.date(2026, 10, 30), "last_success_at": sab})
    assert collector.tarefa_devida("NYSE", sab)[0] is False


def test_agenda_desconhecido_nao_roda(db):
    futuro = dt.datetime(2031, 1, 7, 12, 0, tzinfo=SP)
    assert collector.tarefa_devida("B3", futuro)[1].startswith("sem ativos")
    Asset.objects.create(symbol="PETR4", name="Petrobras", market="B3", custody="OUTRA")
    assert collector.tarefa_devida("B3", futuro)[1].startswith("calendário")


def test_agenda_cripto_e_cambio(db):
    assert (
        collector.tarefa_devida("CRIPTO", dt.datetime(2026, 10, 31, 3, 0, tzinfo=dt.UTC))[0] is False
    )  # sem ativos
    Asset.objects.create(symbol="BTC", name="Bitcoin", market="CRIPTO", custody="BINANCE")
    assert collector.tarefa_devida("CRIPTO", dt.datetime(2026, 10, 31, 3, 0, tzinfo=dt.UTC))[0] is True
    sab = dt.datetime(2026, 10, 31, 12, 0, tzinfo=SP)
    assert collector.tarefa_devida("CAMBIO_INTRA", sab)[0] is False
    assert collector.tarefa_devida("CAMBIO_PTAX", sab)[0] is False
    qui_manha = dt.datetime(2026, 10, 29, 10, 0, tzinfo=SP)
    assert collector.tarefa_devida("CAMBIO_INTRA", qui_manha)[0] is True
    assert collector.tarefa_devida("CAMBIO_PTAX", qui_manha)[1] == "antes da publicação do PTAX"
    tarde = dt.datetime(2026, 10, 29, 13, 31, tzinfo=SP)
    assert collector.tarefa_devida("CAMBIO_PTAX", tarde)[0] is True
    ScheduleState.objects.update_or_create(task="CAMBIO_PTAX", defaults={"last_attempt_at": tarde})
    assert (
        collector.tarefa_devida("CAMBIO_PTAX", tarde + dt.timedelta(minutes=30))[1]
        == "aguardando nova tentativa"
    )
    assert collector.tarefa_devida("CAMBIO_PTAX", tarde + dt.timedelta(minutes=61))[0] is True


def test_passagem_completa_em_modo_demo(settings, ttwo):
    settings.PORTAL = {**settings.PORTAL, "MODO_DEMO": True}
    Asset.objects.create(symbol="BTC", name="Bitcoin", market="CRIPTO", custody="BINANCE")
    agora = dt.datetime(2026, 10, 29, 14, 0, tzinfo=SP)  # NYSE aberta, câmbio intradiário, PTAX devido
    runs = collector.executar_tarefas_devidas(agora, dormir=sem_dormir)
    assert sorted(r.market for r in runs) == ["CAMBIO_INTRA", "CAMBIO_PTAX", "CRIPTO", "NYSE"]
    assert all(r.status == "completo" for r in runs)
    assert Quote.objects.filter(asset=ttwo).exists()
    # segunda passagem imediata: nada devido
    assert collector.executar_tarefas_devidas(agora + dt.timedelta(minutes=1), dormir=sem_dormir) == []
    # reinício do coletor não duplica: mesma cotação demo com mesmo horário → nenhuma nova
    n = Quote.objects.count()
    collector.executar_tarefas_devidas(agora + dt.timedelta(minutes=16), dormir=sem_dormir)
    assert Quote.objects.count() > n  # demo gera horário novo a cada ciclo
    assert CollectionRun.objects.count() >= 4
