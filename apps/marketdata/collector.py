"""
Coletor: agenda por mercado, execução de ciclos com retentativas e orçamento, persistência.

Tarefas da agenda:
  NYSE / NASDAQ / B3  → cotações dos ativos do mercado, a cada INTERVALO durante o pregão,
                        e uma coleta de fechamento após o encerramento da sessão;
  CRIPTO              → a cada 5 min (INTERVALO_POR_TAREFA), 24/7;
  CAMBIO_INTRA        → câmbio intradiário em dias úteis, 09h–18h (São Paulo), a cada INTERVALO;
  CAMBIO_PTAX         → PTAX de fechamento do dia, a partir das 13h30 (São Paulo), até obter.

Garantias:
- Nunca se grava cotação sem resposta válida da fonte; falhas ficam em CollectionRun.details.
- Mesmo horário de mercado de uma cotação já gravada não gera novo registro (não "renova" a idade).
- Cota excedida → ciclo pulado e registrado (status "pulado"), sem estimativa.
- Trava transacional (pg_advisory_xact_lock) impede dois coletores simultâneos na mesma tarefa.
"""

from __future__ import annotations

import datetime as dt
import logging
import random
import time
from collections.abc import Callable
from zoneinfo import ZoneInfo

from django.db import connection, transaction
from django.utils import timezone

from apps.portfolio.models import Asset

from . import budget
from .adapters import adaptador_cambio, adaptador_cotacoes, adaptador_para_mercado, fonte_sem_chave
from .adapters.base import AdapterError, AuthError, BaseFxAdapter, RateLimitError
from .calendars import calendario
from .models import (
    CollectionRun,
    DailyBar,
    FxDailyRate,
    FxRate,
    FxRateType,
    Quote,
    QuoteType,
    RecordStatus,
    ScheduleState,
)

log = logging.getLogger("coletor")

INTERVALO = dt.timedelta(minutes=15)
# Cripto é pública e sem cota relevante na Binance: intervalo menor. Demais tarefas usam INTERVALO.
INTERVALO_POR_TAREFA = {"CRIPTO": dt.timedelta(minutes=5)}
ATRASO_FECHAMENTO = dt.timedelta(minutes=10)  # coleta de fechamento após o fim da sessão
TENTATIVAS = 3
ESPERA_BASE_S = 2.0
ESPERA_MAX_RATE_LIMIT_S = 60
SP = ZoneInfo("America/Sao_Paulo")
MERCADOS_BOLSA = ("NYSE", "NASDAQ", "B3")
TAREFAS = (*MERCADOS_BOLSA, "CRIPTO", "CAMBIO_INTRA", "CAMBIO_PTAX")
TRAVAS = {t: 7_000 + i for i, t in enumerate(TAREFAS)}


# --- Agenda ------------------------------------------------------------------------------


def estado_tarefa(tarefa: str) -> ScheduleState:
    obj, _ = ScheduleState.objects.get_or_create(task=tarefa)
    return obj


def intervalo_da_tarefa(tarefa: str) -> dt.timedelta:
    if tarefa == "B3":
        # brapi atualiza a cada 30 min no Gratuito (15 no Startup, 5 no Pro): BRAPI_INTERVALO_MIN no .env
        from django.conf import settings

        try:
            minutos = int(settings.FONTES.get("BRAPI_INTERVALO_MIN") or 30)
        except (TypeError, ValueError):
            minutos = 30
        return dt.timedelta(minutes=max(5, minutos))
    if tarefa in ("NYSE", "NASDAQ"):
        # ações dos EUA da carteira: ACOES_EUA_INTERVALO_MIN no .env (padrão 15; mínimo 1)
        from django.conf import settings

        try:
            minutos = int(settings.FONTES.get("ACOES_EUA_INTERVALO_MIN") or 15)
        except (TypeError, ValueError):
            minutos = 15
        return dt.timedelta(minutes=max(1, minutos))
    return INTERVALO_POR_TAREFA.get(tarefa, INTERVALO)


def coleta_forcada(estado: ScheduleState) -> bool:
    """Pedido "Atualizar agora" ainda não atendido (feito depois da última tentativa)."""
    pedido = estado.force_requested_at
    return pedido is not None and (estado.last_attempt_at is None or pedido > estado.last_attempt_at)


def solicitar_coleta(agora: dt.datetime | None = None) -> int:
    """Marca todas as tarefas de cotação/câmbio intradiário para rodar na próxima passagem do coletor."""
    agora = agora or timezone.now()
    n = 0
    for tarefa in (*MERCADOS_BOLSA, "CRIPTO", "CAMBIO_INTRA"):
        if tarefa == "NASDAQ":
            continue
        estado = estado_tarefa(tarefa)
        estado.force_requested_at = agora
        estado.save(update_fields=["force_requested_at", "updated_at"])
        n += 1
    return n


def tarefa_devida(tarefa: str, agora: dt.datetime, estado: ScheduleState | None = None) -> tuple[bool, str]:
    """Decide se a tarefa deve rodar agora. Devolve (devida, motivo)."""
    estado = estado or estado_tarefa(tarefa)
    ultimo = estado.last_attempt_at
    forcada = coleta_forcada(estado)
    passou_intervalo = forcada or ultimo is None or agora - ultimo >= intervalo_da_tarefa(tarefa)
    motivo_intervalo = "forçada pelo usuário" if forcada else "aguardando intervalo"

    if tarefa in MERCADOS_BOLSA or tarefa == "CRIPTO":
        if not _ativos_do_mercado(tarefa):
            return False, "sem ativos cadastrados para o mercado"
        variavel = fonte_sem_chave(tarefa)
        if variavel:
            return False, f"fonte sem chave ({variavel} vazia no .env) — tarefa pausada"

    if tarefa in MERCADOS_BOLSA:
        cal = calendario(tarefa)
        sit = cal.situacao(agora)
        if sit.estado == "desconhecido":
            return False, "calendário sem cobertura para a data (estado desconhecido)"
        if sit.estado == "aberto":
            return passou_intervalo, "pregão aberto" if passou_intervalo else motivo_intervalo
        s = sit.sessao
        if s is None:
            return False, "sem sessão encerrada conhecida"
        if estado.last_close_date == s.data:
            # Reconfirma a consolidação da sessão dos EUA uma vez, duas horas
            # após o encerramento. Não coleta continuamente o pós-mercado.
            revisao = s.fechamento + dt.timedelta(hours=2)
            if tarefa in ("NYSE", "NASDAQ") and agora >= revisao and (estado.last_success_at is None or estado.last_success_at < revisao):
                return passou_intervalo, f"reconfirmação do fechamento de {s.data}" if passou_intervalo else motivo_intervalo
            novos = _ativos_novos_sem_cotacao(tarefa, estado, agora)
            if not novos and not forcada:
                return False, f"fechamento de {s.data} já coletado"
            if not passou_intervalo:
                return False, motivo_intervalo
            if forcada:
                return True, f"fechamento de {s.data} — forçada pelo usuário"
            lista = ", ".join(novos[:5])
            return True, f"fechamento de {s.data} para ativos incluídos depois da coleta: {lista}"
        # A fonte B3 atualiza em blocos; aguardar um bloco após o call evita
        # congelar um preço intradiário como fechamento do dia.
        atraso_fechamento = ATRASO_FECHAMENTO
        if tarefa == "B3":
            atraso_fechamento += intervalo_da_tarefa(tarefa)
        if agora >= s.fechamento + atraso_fechamento:
            return passou_intervalo, f"coleta de fechamento de {s.data}"
        return False, "aguardando janela de fechamento"

    if tarefa == "CRIPTO":
        return passou_intervalo, "cripto 24/7" if passou_intervalo else motivo_intervalo

    local = agora.astimezone(SP)
    dia_util = local.weekday() < 5
    if tarefa == "CAMBIO_INTRA":
        if not dia_util or not (9 <= local.hour < 18):
            return False, "fora do horário comercial"
        return passou_intervalo, "câmbio intradiário" if passou_intervalo else motivo_intervalo

    if tarefa == "CAMBIO_PTAX":
        if not dia_util:
            return False, "fim de semana"
        if estado.last_close_date == local.date():
            return False, "PTAX do dia já obtido"
        if local.hour < 13 or (local.hour == 13 and local.minute < 30):
            return False, "antes da publicação do PTAX"
        espera = ultimo is None or agora - ultimo >= dt.timedelta(minutes=60)
        return espera, "PTAX pendente" if espera else "aguardando nova tentativa"

    return False, "tarefa desconhecida"


# --- Execução com retentativas -------------------------------------------------------------


def _com_retentativas(func: Callable, dormir: Callable[[float], None], descricao: str):
    """Executa `func` com até TENTATIVAS para erros retentáveis. Devolve (resultado, tentativas)."""
    for tentativa in range(1, TENTATIVAS + 1):
        try:
            return func(), tentativa
        except RateLimitError as exc:
            if tentativa == TENTATIVAS:
                raise
            espera = min(exc.retry_after or ESPERA_BASE_S * 2**tentativa, ESPERA_MAX_RATE_LIMIT_S)
            log.warning("%s: cota (429); aguardando %ss (tentativa %s)", descricao, espera, tentativa)
            dormir(espera)
        except AdapterError as exc:
            if not exc.retryable or tentativa == TENTATIVAS:
                raise
            espera = ESPERA_BASE_S * 2 ** (tentativa - 1) + random.uniform(0, 1)
            log.warning("%s: %s; retentando em %.1fs (tentativa %s)", descricao, exc, espera, tentativa)
            dormir(espera)
    raise AssertionError("inalcançável")


def _trava(tarefa: str) -> None:
    with connection.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(%s)", [TRAVAS[tarefa]])


# --- Ciclos --------------------------------------------------------------------------------


def _ativos_novos_sem_cotacao(tarefa: str, estado: ScheduleState, agora: dt.datetime) -> list[str]:
    """Ativos do mercado incluídos/alterados depois da última coleta e ainda sem nenhuma cotação.

    Com o pregão fechado o fechamento é coletado uma vez; um ativo incluído depois (ex.: "Incluir agora" num
    feriado) ficaria sem preço até a próxima sessão. Só conta quem entrou depois da última tentativa, para
    que um ticker com falha permanente não faça a tarefa repetir o fim de semana inteiro.
    """
    referencia = estado.last_attempt_at
    if referencia is None:
        return []
    return [
        a.symbol
        for a in _ativos_do_mercado(tarefa)
        if referencia < a.updated_at <= agora and not a.quotes.filter(status=RecordStatus.VALID).exists()
    ]


def _ativos_do_mercado(mercado: str) -> list[Asset]:
    """Ativos cotados no ciclo do mercado.

    Ações dos EUA só acompanhadas (universo do Mercado/Favoritos, sem compra) ficam fora do ciclo de 15 min:
    recebem preço pelo rodízio horário de candles (apps.mercado.coleta), para caber no plano gratuito.
    """
    mercados = ("NYSE", "NASDAQ") if mercado in ("NYSE", "NASDAQ") else (mercado,)
    ativos = Asset.objects.filter(is_active=True, market__in=mercados).order_by("symbol")
    return [a for a in ativos if not a.coleta_horaria or a.has_position]


@transaction.atomic
def executar_ciclo_mercado(
    mercado: str,
    agora: dt.datetime | None = None,
    adaptador=None,
    dormir: Callable[[float], None] = time.sleep,
    fechamento: bool = False,
) -> CollectionRun:
    """Coleta cotações de todos os ativos ativos do mercado e grava o resultado."""
    agora = agora or timezone.now()
    tarefa = "CRIPTO" if mercado == "CRIPTO" else ("NYSE" if mercado in ("NYSE", "NASDAQ") else mercado)
    _trava(tarefa)
    estado = estado_tarefa(tarefa)
    adaptador = adaptador or adaptador_cotacoes(mercado, agora=lambda: agora)
    ativos = _ativos_do_mercado(mercado)
    por_simbolo = {a.simbolo_na_fonte: a for a in ativos}
    run = CollectionRun.objects.create(
        started_at=agora, source=adaptador.info.codigo, market=tarefa, attempted=len(ativos)
    )
    estado.last_attempt_at = agora

    if not ativos:
        run.status, run.finished_at = CollectionRun.Status.SKIPPED, agora
        run.error = "nenhum ativo cadastrado para o mercado"
        run.save()
        _registrar_estado(estado, run, fechamento, None)
        return run

    # Fontes com limite por minuto (Twelve Data Basic: 8) recebem os símbolos em lotes, um por minuto.
    simbolos = list(por_simbolo)
    tamanho = adaptador.info.limite_minuto or len(simbolos)
    lotes = [simbolos[i : i + tamanho] for i in range(0, len(simbolos), tamanho)]
    resultado = None
    for indice, lote in enumerate(lotes):
        instante = agora + dt.timedelta(seconds=61 * indice)
        if indice:
            dormir(61)
        try:
            budget.reservar(adaptador.info, len(lote), instante)
        except budget.OrcamentoExcedido as exc:
            if resultado is None:
                run.status, run.finished_at, run.error = CollectionRun.Status.SKIPPED, agora, str(exc)
                run.save()
                _registrar_estado(estado, run, fechamento, None)
                log.warning("ciclo pulado | %s", exc)
                return run
            resultado.falhas.update({s: f"lote não coletado: {exc}" for s in lote})
            continue
        try:
            parcial, tentativas = _com_retentativas(
                lambda lote=lote: adaptador.cotar(lote), dormir, f"{adaptador.info.codigo}/{tarefa}"
            )
        except AuthError as exc:
            if resultado is None:
                return _falha(
                    run,
                    estado,
                    agora,
                    f"credencial: {exc} — corrigir chave/plano antes de repetir",
                    fechamento,
                )
            resultado.falhas.update({s: f"credencial: {exc}" for s in lote})
            continue
        except AdapterError as exc:
            if resultado is None:
                return _falha(run, estado, agora, f"{exc.kind}: {exc}", fechamento)
            resultado.falhas.update({s: f"{exc.kind}: {exc}" for s in lote})
            continue
        run.attempts = max(run.attempts, tentativas)
        if resultado is None:
            resultado = parcial
        else:
            resultado.cotacoes.extend(parcial.cotacoes)
            resultado.falhas.update(parcial.falhas)

    novas = 0
    for cot in resultado.cotacoes:
        asset = por_simbolo.get(cot.simbolo)
        if asset is None:
            run.details[cot.simbolo] = "símbolo não esperado na resposta"
            continue
        criada = _gravar_cotacao(asset, cot, run)
        if fechamento and mercado in ("NYSE", "NASDAQ"):
            _conferir_fechamento_eua(asset, cot, agora)
        novas += int(criada)
    run.details.update(resultado.falhas)
    run.succeeded = len(resultado.cotacoes)
    run.failed = len(ativos) - run.succeeded
    run.finished_at = agora
    run.status = (
        CollectionRun.Status.COMPLETE
        if run.failed == 0
        else CollectionRun.Status.PARTIAL
        if run.succeeded
        else CollectionRun.Status.FAILED
    )
    run.save()
    sessao_fechada = calendario(mercado).ultima_sessao_encerrada(agora) if fechamento else None
    _registrar_estado(estado, run, fechamento, sessao_fechada.data if sessao_fechada else None)
    _avaliar_alertas(ativos, agora)
    log.info(
        "ciclo %s | fonte=%s status=%s ok=%s falhas=%s novas=%s",
        tarefa,
        run.source,
        run.status,
        run.succeeded,
        run.failed,
        novas,
    )
    return run


def _avaliar_alertas(ativos: list[Asset], agora: dt.datetime) -> None:
    """Avalia regras após a persistência das cotações (também em falha: suspende, não rearma)."""
    from apps.alerts import notify
    from apps.alerts import services as alert_services

    try:
        disparados = alert_services.avaliar_ativos(ativos, agora)
        if disparados:
            log.info(
                "alertas disparados: %s",
                ", ".join(f"{a.asset.symbol}:{a.version.descricao}" for a in disparados),
            )
            notify.enfileirar(disparados)
    except Exception:  # noqa: BLE001 - a avaliação não pode derrubar a coleta
        log.exception("falha ao avaliar alertas")
    try:
        enviadas = notify.enviar_pendentes(agora=agora)
        if enviadas:
            log.info("notificações enviadas: %s", enviadas)
    except Exception:  # noqa: BLE001
        log.exception("falha ao enviar notificações")


def _falha(run: CollectionRun, estado: ScheduleState, agora, erro: str, fechamento: bool) -> CollectionRun:
    run.status, run.finished_at, run.error, run.failed = (
        CollectionRun.Status.FAILED,
        agora,
        erro[:500],
        run.attempted,
    )
    run.save()
    _registrar_estado(estado, run, fechamento, None)
    log.error("ciclo %s falhou | %s", run.market, erro)
    return run


def _registrar_estado(estado: ScheduleState, run: CollectionRun, fechamento: bool, data_fechamento) -> None:
    estado.last_status = run.status
    estado.last_error = run.error
    if run.status in (CollectionRun.Status.COMPLETE, CollectionRun.Status.PARTIAL):
        estado.last_success_at = run.finished_at
        if fechamento and data_fechamento and run.status == CollectionRun.Status.COMPLETE:
            estado.last_close_date = data_fechamento
    estado.save()


def _gravar_cotacao(asset: Asset, cot, run: CollectionRun) -> bool:
    """Grava a cotação; devolve False se já existia registro para o mesmo horário de mercado."""
    campos = {
        "price": cot.preco,
        "currency": cot.moeda,
        "previous_close": cot.fechamento_anterior,
        "change_24h_pct": cot.variacao_24h_pct,
        "collected_at": cot.horario_coleta,
        "source_version": cot.fonte_versao,
        "quote_type": cot.tipo,
        "declared_delay_minutes": cot.atraso_declarado_min,
        "run": run,
        "raw": cot.bruto,
    }
    if cot.horario_mercado is None:
        Quote.objects.create(asset=asset, source=cot.fonte, market_time=None, **campos)
        return True
    _, criada = Quote.objects.get_or_create(
        asset=asset,
        source=cot.fonte,
        market_time=cot.horario_mercado,
        status=RecordStatus.VALID,
        defaults=campos,
    )
    if not criada:
        run.details[cot.simbolo] = (
            f"sem nova observação (mesmo horário de mercado {cot.horario_mercado.isoformat()})"
        )
    return criada


@transaction.atomic
def executar_ciclo_cambio(
    tipo: str, agora: dt.datetime | None = None, adaptador: BaseFxAdapter | None = None, dormir=time.sleep
) -> CollectionRun:
    """tipo: 'intradiario' → FxRate; 'fechamento' → FxDailyRate (PTAX) do dia local."""
    agora = agora or timezone.now()
    tarefa = "CAMBIO_PTAX" if tipo == "fechamento" else "CAMBIO_INTRA"
    _trava(tarefa)
    estado = estado_tarefa(tarefa)
    adaptador = adaptador or adaptador_cambio(tipo)
    run = CollectionRun.objects.create(
        started_at=agora, source=adaptador.info.codigo, market=tarefa, attempted=1
    )
    estado.last_attempt_at = agora
    try:
        budget.reservar(adaptador.info, 1, agora)
        if tipo == "fechamento":
            data = agora.astimezone(SP).date()
            cambio, run.attempts = _com_retentativas(lambda: adaptador.cambio_diario(data), dormir, tarefa)
            if cambio is None:
                run.status, run.finished_at, run.error = (
                    CollectionRun.Status.FAILED,
                    agora,
                    f"sem PTAX para {data}",
                )
                run.save()
                _registrar_estado(estado, run, False, None)
                return run
            FxDailyRate.objects.update_or_create(
                pair=cambio.par,
                date=data,
                rate_type=FxRateType.PTAX_CLOSE,
                source=cambio.fonte,
                defaults={"bid": cambio.compra, "ask": cambio.venda, "collected_at": agora},
            )
            estado.last_close_date = data
        else:
            cambio, run.attempts = _com_retentativas(adaptador.cambio, dormir, tarefa)
            FxRate.objects.create(
                pair=cambio.par,
                rate_type=cambio.tipo,
                bid=cambio.compra,
                ask=cambio.venda,
                rate_time=cambio.horario,
                collected_at=agora,
                source=cambio.fonte,
                run=run,
                raw=cambio.bruto,
            )
            _cotacao_do_dolar(cambio, agora, run)
    except budget.OrcamentoExcedido as exc:
        run.status, run.finished_at, run.error = CollectionRun.Status.SKIPPED, agora, str(exc)
        run.save()
        _registrar_estado(estado, run, False, None)
        return run
    except AdapterError as exc:
        return _falha(run, estado, agora, f"{exc.kind}: {exc}", False)
    run.status, run.finished_at, run.succeeded = CollectionRun.Status.COMPLETE, agora, 1
    run.save()
    _registrar_estado(estado, run, False, None)
    return run


def _cotacao_do_dolar(cambio, agora: dt.datetime, run: CollectionRun) -> None:
    """Espelha o câmbio intradiário no pseudo-ativo USD/BRL (alertas, favoritos, gráficos)."""
    from decimal import Decimal

    from apps.mercado.dolar import ativo_dolar

    try:
        asset = ativo_dolar()
        pct = cambio.bruto.get("pctChange") if isinstance(cambio.bruto, dict) else None
        try:
            variacao = Decimal(str(pct)) if pct not in (None, "") else None
        except ArithmeticError:
            variacao = None
        anterior = None
        if variacao is not None and variacao > Decimal("-100"):
            anterior = cambio.compra / (1 + variacao / 100)
        Quote.objects.create(
            asset=asset,
            price=cambio.compra,
            currency="BRL",
            previous_close=anterior,
            change_24h_pct=variacao,
            market_time=None,
            collected_at=agora,
            source=cambio.fonte,
            source_version="cambio_intradiario",
            quote_type=QuoteType.UNDECLARED,
            run=run,
            raw={
                **(cambio.bruto or {}),
                "horario_fonte": cambio.horario.isoformat() if cambio.horario else None,
            },
        )
        _avaliar_alertas([asset], agora)
    except Exception:  # noqa: BLE001 - o espelho nunca derruba a coleta de câmbio
        log.exception("falha ao registrar a cotação do dólar comercial")


def carregar_historico(asset: Asset, dias: int = 30, agora: dt.datetime | None = None, adaptador=None) -> int:
    """Busca candles diários e grava/atualiza DailyBar (uma fonte por registro). Devolve nº de registros."""
    agora = agora or timezone.now()
    adaptador = adaptador or adaptador_para_mercado(asset.market)
    budget.reservar(adaptador.info, 1, agora)
    candles = adaptador.historico(asset.simbolo_na_fonte, dias)
    n = 0
    for c in candles:
        DailyBar.objects.update_or_create(
            asset=asset,
            session_date=c.data,
            source=c.fonte,
            defaults={
                "time_basis": c.base_tempo,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume,
                "provisional": c.provisorio,
                "collected_at": agora,
            },
        )
        n += 1
    return n


def carregar_historico_cambio(dias: int = 30, agora: dt.datetime | None = None, adaptador=None) -> int:
    agora = agora or timezone.now()
    adaptador = adaptador or adaptador_cambio("fechamento")
    hoje = agora.astimezone(SP).date()
    n = 0
    for i in range(dias):
        data = hoje - dt.timedelta(days=i)
        if (
            data.weekday() >= 5
            or FxDailyRate.objects.filter(date=data, rate_type=FxRateType.PTAX_CLOSE).exists()
        ):
            continue
        budget.reservar(adaptador.info, 1, agora)
        cambio = adaptador.cambio_diario(data)
        if cambio is None:
            continue
        FxDailyRate.objects.update_or_create(
            pair=cambio.par,
            date=data,
            rate_type=FxRateType.PTAX_CLOSE,
            source=cambio.fonte,
            defaults={"bid": cambio.compra, "ask": cambio.venda, "collected_at": agora},
        )
        n += 1
    return n


# --- Laço principal ---------------------------------------------------------------------------


def executar_tarefas_devidas(agora: dt.datetime | None = None, dormir=time.sleep) -> list[CollectionRun]:
    agora = agora or timezone.now()
    runs: list[CollectionRun] = []
    for tarefa in TAREFAS:
        if tarefa == "NASDAQ":
            continue  # coberto pela tarefa NYSE (mesmo calendário e fonte)
        estado = estado_tarefa(tarefa)
        devida, motivo = tarefa_devida(tarefa, agora, estado)
        if not devida:
            log.debug("tarefa %s não devida: %s", tarefa, motivo)
            continue
        log.info("tarefa %s devida: %s", tarefa, motivo)
        if tarefa in MERCADOS_BOLSA:
            fechamento = calendario(tarefa).situacao(agora).estado == "fechado"
            runs.append(executar_ciclo_mercado(tarefa, agora, dormir=dormir, fechamento=fechamento))
        elif tarefa == "CRIPTO":
            runs.append(executar_ciclo_mercado("CRIPTO", agora, dormir=dormir))
        elif tarefa == "CAMBIO_INTRA":
            runs.append(executar_ciclo_cambio("intradiario", agora, dormir=dormir))
        elif tarefa == "CAMBIO_PTAX":
            runs.append(executar_ciclo_cambio("fechamento", agora, dormir=dormir))
    _rotinas_de_mercado(agora)
    _rotinas_de_aviso(agora)
    return runs


def _rotinas_de_mercado(agora: dt.datetime) -> None:
    """Candles de 1 ano, rodízio do universo, analistas/notícias/resultados e Focus (apps.mercado)."""
    from django.conf import settings

    if not settings.PORTAL.get("COLETA_MERCADO", False):
        return
    from apps.mercado import coleta

    try:
        coleta.executar(agora)
    except Exception:  # noqa: BLE001 - inteligência de mercado nunca derruba a coleta de cotações
        log.exception("falha nas rotinas de mercado")


def _rotinas_de_aviso(agora: dt.datetime) -> None:
    """Resumo diário por horário e vigia do coletor; nunca derrubam a passagem."""
    from apps.alerts import notify, resumo_diario, telegram_bot, vigia

    try:
        tratados = telegram_bot.processar_updates(agora=agora)
        if tratados:
            log.info("telegram: %s comando(s)/clique(s) tratado(s)", tratados)
    except Exception:  # noqa: BLE001
        log.exception("falha ao ler comandos do Telegram")
    try:
        if resumo_diario.enviar_resumo_se_devido(agora):
            log.info("resumo diário enfileirado")
    except Exception:  # noqa: BLE001
        log.exception("falha ao montar o resumo diário")
    try:
        for inc in vigia.verificar(agora):
            log.warning("vigia: %s %s", "normalizado" if inc.resolved_at else "aberto", inc.message)
    except Exception:  # noqa: BLE001
        log.exception("falha no vigia do coletor")
    try:
        enviadas = notify.enviar_pendentes(agora=agora)
        if enviadas:
            log.info("notificações enviadas: %s", enviadas)
    except Exception:  # noqa: BLE001
        log.exception("falha ao enviar notificações")


def _conferir_fechamento_eua(asset, cot, agora):
    """Persist confirmed regular-session close; never derive it from a screenshot."""
    if cot.fonte != "twelvedata" or cot.bruto.get("is_market_open") is not False:
        return False
    sessao = calendario(asset.market).ultima_sessao_encerrada(agora)
    if sessao is None or agora < sessao.fechamento + dt.timedelta(hours=2):
        return False
    try:
        data_fonte = dt.datetime.fromisoformat(cot.bruto["timestamp_candle"]).astimezone(ZoneInfo("America/New_York")).date()
    except (KeyError, TypeError, ValueError):
        return False
    if data_fonte != sessao.data:
        return False
    DailyBar.objects.update_or_create(asset=asset, session_date=sessao.data, source=cot.fonte,
        defaults={"time_basis":"sessao_local", "close":cot.preco, "provisional":False, "collected_at":cot.horario_coleta})
    return True
