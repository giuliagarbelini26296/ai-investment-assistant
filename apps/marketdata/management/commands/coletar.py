"""
Coletor de dados de mercado.

Laço: a cada --verificar segundos (30 por padrão), executa as tarefas devidas da agenda (bolsas no pregão,
cripto 24/7 a cada 5 min, câmbio intradiário e PTAX a cada 15 min, resumo diário no Telegram) e o vigia.
"Atualizar agora" no portal marca as tarefas para a próxima passagem. --uma-vez executa uma passagem e
encerra. --tarefa força uma tarefa específica
(ignora a agenda), útil na homologação.
"""

import logging
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.marketdata import collector

log = logging.getLogger("coletor")


class Command(BaseCommand):
    help = "Executa o coletor de cotações e câmbio conforme a agenda por mercado."

    def add_arguments(self, parser):
        parser.add_argument("--uma-vez", action="store_true", help="Uma passagem pela agenda e encerra.")
        parser.add_argument(
            "--verificar", type=int, default=30, help="Segundos entre verificações da agenda."
        )
        parser.add_argument(
            "--tarefa", choices=collector.TAREFAS, help="Força a execução de uma tarefa agora."
        )
        parser.add_argument(
            "--agenda", action="store_true", help="Mostra, por tarefa, se está devida e o motivo; não coleta."
        )

    def handle(self, *args, **options):
        log.info(
            "coletor iniciado | versao=%s modo_demo=%s intervalo=%s",
            settings.PORTAL["VERSAO"],
            settings.PORTAL["MODO_DEMO"],
            collector.INTERVALO,
        )
        if options["agenda"]:
            agora = timezone.now()
            for tarefa in collector.TAREFAS:
                if tarefa == "NASDAQ":
                    continue
                estado = collector.estado_tarefa(tarefa)
                devida, motivo = collector.tarefa_devida(tarefa, agora, estado)
                ultimo = (
                    estado.last_success_at.astimezone(collector.SP).strftime("%d/%m %H:%M")
                    if estado.last_success_at
                    else "nunca"
                )
                self.stdout.write(
                    f"{tarefa:13} {'DEVIDA ' if devida else 'parada '} {motivo} | último sucesso: {ultimo}"
                    + (f" | último erro: {estado.last_error[:90]}" if estado.last_error else "")
                )
            return
        if options["tarefa"]:
            run = self._forcar(options["tarefa"])
            self.stdout.write(
                f"{run.market}: {run.status} ok={run.succeeded} falhas={run.failed} {run.error}"
            )
            return
        if not options["uma_vez"]:
            from apps.insights.live_crypto import start
            start()
            from apps.insights.live_alpaca import start as start_alpaca
            start_alpaca()
        while True:
            agora = timezone.now()
            try:
                runs = collector.executar_tarefas_devidas(agora)
                for run in runs:
                    self.stdout.write(
                        f"{run.started_at:%H:%M} {run.market}: {run.status} "
                        f"ok={run.succeeded} falhas={run.failed}" + (f" | {run.error}" if run.error else "")
                    )
            except Exception:  # noqa: BLE001 - o laço nunca deve morrer por um ciclo
                log.exception("erro inesperado na passagem da agenda")
            if settings.PORTAL.get("COLETA_MERCADO") and not settings.PORTAL["MODO_DEMO"]:
                try:
                    from apps.insights.decision_collection import refresh_crypto_analysis
                    refresh_crypto_analysis(agora)
                except Exception:
                    log.warning("Referências de cripto para avaliação aguardando dados nesta passagem.")
                try:
                    from apps.insights.decision_monitor import refresh_notices
                    refresh_notices(agora)
                except Exception:
                    log.warning("Monitor de avaliação aguardando dados nesta passagem.")
                try:
                    from apps.insights.technical_watch import refresh_watches
                    refresh_watches(agora)
                except Exception:
                    log.warning("Acompanhamento técnico aguardando dados nesta passagem.")
                try:
                    from apps.insights.collection import refresh_benchmarks
                    refresh_benchmarks(agora)
                except Exception:
                    log.warning("Referências do planejamento indisponíveis nesta passagem.")
                try:
                    from apps.react_portal.evolution import registrar_patrimonio
                    registrar_patrimonio(agora)
                except Exception:
                    log.warning("Foto diária do patrimônio indisponível nesta passagem.")
                try:
                    from apps.react_portal.push import notificar_noticias
                    notificar_noticias(agora)
                except Exception:
                    log.warning("Notificações de notícias indisponíveis nesta passagem.")
            if options["uma_vez"]:
                return
            self._esperar_respondendo_telegram(options["verificar"])

    @staticmethod
    def _esperar_respondendo_telegram(segundos: int) -> None:
        """Entre passagens, atende botões e comandos do Telegram com long polling de ~5 s."""
        from apps.alerts import telegram_bot

        fim = time.monotonic() + segundos
        while True:
            restante = fim - time.monotonic()
            if restante <= 0:
                return
            try:
                if telegram_bot.TelegramApi().ligado:
                    telegram_bot.processar_updates(timeout_s=min(5, int(restante)) or 1)
                    continue
            except Exception:  # noqa: BLE001 - o laço nunca deve morrer
                log.exception("falha ao atender o Telegram")
            time.sleep(min(5, restante))

    @staticmethod
    def _forcar(tarefa: str):
        agora = timezone.now()
        if tarefa in collector.MERCADOS_BOLSA:
            fechado = collector.calendario(tarefa).situacao(agora).estado == "fechado"
            return collector.executar_ciclo_mercado(tarefa, agora, fechamento=fechado)
        if tarefa == "CRIPTO":
            return collector.executar_ciclo_mercado("CRIPTO", agora)
        if tarefa == "CAMBIO_INTRA":
            return collector.executar_ciclo_cambio("intradiario", agora)
        return collector.executar_ciclo_cambio("fechamento", agora)
