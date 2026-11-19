"""Diagnóstico dos canais de notificação: `manage.py notificacoes --testar`."""

from django.core.management.base import BaseCommand

from apps.alerts import notify
from apps.alerts.models import Notification, NotificationStatus


class Command(BaseCommand):
    help = "Mostra os canais de notificação configurados; --testar envia uma mensagem de teste ao Telegram."

    def add_arguments(self, parser):
        parser.add_argument("--testar", action="store_true", help="envia mensagem de teste ao Telegram")
        parser.add_argument("--reenviar", action="store_true", help="tenta enviar as pendentes/falhas agora")

    def handle(self, *args, **opts):
        canais = notify.canais_ligados()
        self.stdout.write(f"canais ligados: {', '.join(canais) or 'nenhum'}")
        pend = Notification.objects.filter(status=NotificationStatus.PENDING).count()
        falhas = Notification.objects.filter(status=NotificationStatus.FAILED).count()
        self.stdout.write(f"notificações pendentes: {pend} · com falha: {falhas}")
        if opts["testar"]:
            self.stdout.write(notify.testar_telegram())
        if opts["reenviar"]:
            self.stdout.write(f"enviadas agora: {notify.enviar_pendentes()}")
