import logging
import time

from django.core.management.base import BaseCommand
from django.db import connection

from apps.noticias import ingest

log = logging.getLogger("noticias")


class Command(BaseCommand):
    help = "Atualiza notícias públicas e traduz títulos/resumos localmente, sem alterar a carteira."

    def add_arguments(self, parser):
        parser.add_argument("--uma-vez", action="store_true")
        parser.add_argument("--sem-traducao", action="store_true")
        parser.add_argument("--limite-traducao", type=int, default=24)

    def handle(self, *args, **options):
        if not ingest.acquire_worker_lock():
            self.stdout.write("Já existe uma rotina de notícias em execução.")
            return
        try:
            while True:
                result = ingest.run_once(
                    translate_limit=0 if options["sem_traducao"] else options["limite_traducao"]
                )
                log.info(
                    "central de notícias: fontes=%s traduzidas=%s", result["fontes"], result["traduzidas"]
                )
                if options["uma_vez"]:
                    return
                time.sleep(5 if result["traduzidas"] else 30)
                if connection.connection is None:
                    raise RuntimeError("Conexão da rotina de notícias encerrada")
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [840031103])
