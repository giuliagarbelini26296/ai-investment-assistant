from django.core.management.base import BaseCommand
from apps.managed_portfolios.collection import refresh_currency_quotes


class Command(BaseCommand):
    help='Atualiza referências diretas de cripto em reais das carteiras adicionais.'

    def handle(self,*args,**options):
        self.stdout.write(str(refresh_currency_quotes()))
