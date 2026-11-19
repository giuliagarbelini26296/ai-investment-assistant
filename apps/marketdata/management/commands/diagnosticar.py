"""
Diagnóstico de um mercado: por que um ativo aparece "indisponível".

    python manage.py diagnosticar B3
    python manage.py diagnosticar NYSE

Mostra a tarefa do coletor (devida ou não e o motivo), o último ciclo com as falhas por ativo, o consumo da
fonte e, por ativo, a última cotação, os candles e o último erro. Nunca imprime chaves: só se a variável
está preenchida ou vazia.
"""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.marketdata import budget, collector
from apps.marketdata.adapters import adaptador_cotacoes, chave_da_fonte, fonte_cotacao
from apps.marketdata.calendars import calendario
from apps.marketdata.models import CollectionRun, DailyBar, Quote, RecordStatus
from apps.portfolio.models import Asset

MERCADOS = {"B3": ("B3",), "NYSE": ("NYSE", "NASDAQ"), "NASDAQ": ("NYSE", "NASDAQ"), "CRIPTO": ("CRIPTO",)}


def _h(valor) -> str:
    return timezone.localtime(valor, collector.SP).strftime("%d/%m %H:%M") if valor else "—"


class Command(BaseCommand):
    help = "Explica, por ativo, de onde vem (ou por que falta) a cotação de um mercado."

    def add_arguments(self, parser):
        parser.add_argument("mercado", choices=sorted(MERCADOS), help="B3, NYSE, NASDAQ ou CRIPTO")

    def handle(self, *args, **options):
        mercado = options["mercado"]
        tarefa = "NYSE" if mercado == "NASDAQ" else mercado
        agora = timezone.now()
        w = self.stdout.write

        codigo = fonte_cotacao(tarefa) or "?"
        w(
            f"Portal {settings.PORTAL['VERSAO']} | {_h(agora)} (São Paulo) | modo demo: "
            f"{'sim' if settings.PORTAL['MODO_DEMO'] else 'não'}"
        )
        w(f"Fonte de cotação: {codigo} | chave/token: {'preenchida' if chave_da_fonte(codigo) else 'vazia'}")
        if tarefa != "CRIPTO":
            sit = calendario(tarefa).situacao(agora)
            sessao = sit.sessao.data.strftime("%d/%m") if sit.sessao else "—"
            w(f"Calendário {tarefa}: {sit.estado} (sessão de referência {sessao})")

        estado = collector.estado_tarefa(tarefa)
        devida, motivo = collector.tarefa_devida(tarefa, agora, estado)
        w(f"Tarefa {tarefa}: {'DEVIDA' if devida else 'parada'} — {motivo}")
        w(
            f"  última tentativa {_h(estado.last_attempt_at)} | "
            f"último sucesso {_h(estado.last_success_at)} | status {estado.last_status or '—'} | "
            f"fechamento coletado: {estado.last_close_date or '—'}"
        )
        if estado.last_error:
            w(f"  último erro: {estado.last_error[:200]}")

        run = CollectionRun.objects.filter(market=tarefa).order_by("-started_at").first()
        if run:
            w(
                f"Último ciclo: {_h(run.started_at)} fonte={run.source} status={run.status} "
                f"ok={run.succeeded} falhas={run.failed}"
            )
            if run.error:
                w(f"  erro: {run.error[:200]}")
            for simbolo, detalhe in sorted((run.details or {}).items()):
                w(f"  {simbolo}: {str(detalhe)[:160]}")
        else:
            w("Último ciclo: nenhum registrado para este mercado")

        try:
            info = adaptador_cotacoes(tarefa).info
            consumo = budget.saldo(info, agora)
            if consumo:
                partes = " | ".join(f"{p} {u}/{lim}" for p, (u, lim) in consumo.items())
                w(f"Consumo {info.codigo}: {partes}")
        except Exception as exc:  # diagnóstico não pode quebrar por causa do orçamento
            w(f"Consumo: não lido ({type(exc).__name__})")

        ativos = Asset.objects.filter(is_active=True, market__in=MERCADOS[mercado]).order_by("symbol")
        if not ativos:
            raise CommandError(f"nenhum ativo ativo no mercado {mercado}")
        no_ciclo = {a.pk for a in collector._ativos_do_mercado(tarefa)}
        w("")
        w(f"{'ATIVO':8} {'COMPRA':6} {'CICLO':5} {'ÚLTIMA COTAÇÃO':24} {'CANDLES':16} ÚLTIMO ERRO DE CANDLES")
        for a in ativos:
            q = Quote.objects.filter(asset=a, status=RecordStatus.VALID).order_by("-collected_at").first()
            cot = f"{_h(q.market_time or q.collected_at)} {q.source}" if q else "nenhuma"
            barras = DailyBar.objects.filter(asset=a)
            ultima = barras.order_by("-session_date").first()
            candles = f"{barras.count()} até {ultima.session_date:%d/%m}" if ultima else "0"
            ref = a.atualizacoes.filter(kind="candles").first()
            erro = (ref.last_error[:70] if ref and ref.last_error else "—") if ref else "aguardando carga"
            w(
                f"{a.symbol:8} {'sim' if a.active_lots.exists() else 'não':6} "
                f"{'sim' if a.pk in no_ciclo else 'não':5} {cot:24} {candles:16} {erro}"
            )
        w("")
        w("Sem cotação e sem candles = 'indisponível' na tela. Com candles, a tela usa o último fechamento.")
