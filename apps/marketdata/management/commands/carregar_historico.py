"""
Carga de histórico diário (candles) e de PTAX, respeitando o orçamento das fontes.

    manage.py carregar_historico --dias 30            # todos os ativos ativos + PTAX
    manage.py carregar_historico --dias 365 --simbolo TTWO
    manage.py carregar_historico --so-cambio --dias 90

Para cotas diárias apertadas, rode em dias separados (--simbolo) em vez de tudo de uma vez.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.marketdata import budget, collector
from apps.marketdata.adapters.base import AdapterError
from apps.portfolio.models import Asset


class Command(BaseCommand):
    help = "Carrega histórico diário de preços e PTAX."

    def add_arguments(self, parser):
        parser.add_argument("--dias", type=int, default=30)
        parser.add_argument("--simbolo", action="append", default=[])
        parser.add_argument("--so-cambio", action="store_true")

    def handle(self, *args, **opts):
        agora = timezone.now()
        if not opts["so_cambio"]:
            ativos = Asset.objects.filter(is_active=True).exclude(market="CAMBIO")
            if opts["simbolo"]:
                ativos = ativos.filter(symbol__in=[s.upper() for s in opts["simbolo"]])
            for asset in ativos:
                try:
                    n = collector.carregar_historico(asset, opts["dias"], agora)
                    self.stdout.write(f"{asset.symbol}: {n} candles")
                except budget.OrcamentoExcedido as exc:
                    self.stdout.write(f"{asset.symbol}: pulado — {exc}")
                except AdapterError as exc:
                    self.stdout.write(f"{asset.symbol}: falha {exc.kind} — {exc}")
        try:
            n = collector.carregar_historico_cambio(opts["dias"], agora)
            self.stdout.write(f"PTAX: {n} datas novas")
        except (budget.OrcamentoExcedido, AdapterError) as exc:
            self.stdout.write(f"PTAX: {exc}")
