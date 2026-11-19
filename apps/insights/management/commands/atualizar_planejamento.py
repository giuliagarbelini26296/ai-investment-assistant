from django.core.management.base import BaseCommand
from apps.insights.collection import refresh_benchmarks


class Command(BaseCommand):
    help = 'Atualiza somente as referências BOVA11 e SPY quando devidas, sem alterar a carteira.'

    def handle(self, *args, **options):
        refresh_benchmarks()
        self.stdout.write('Referências conferidas conforme a agenda diária.')
