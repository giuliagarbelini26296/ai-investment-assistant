from django.core.management.base import BaseCommand

from apps.react_portal.reconstrucao import reconstruir


def _fmt(v):
    return '—' if v is None else f'{v:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


class Command(BaseCommand):
    help = 'Mostra (ou grava, com --aplicar) a Evolução reconstruída a partir do histórico do BTG.'

    def add_arguments(self, parser):
        parser.add_argument('--aplicar', action='store_true', help='Grava no banco. Sem isso, só mostra.')

    def handle(self, *args, aplicar=False, **opts):
        resumo = reconstruir(aplicar=aplicar)
        if not resumo:
            self.stdout.write('Sem registro diário do portal ainda: nada a reconstruir.')
            return
        for (conta, moeda), r in resumo.items():
            self.stdout.write(f'\n{conta.upper()} {moeda} (primeiro registro do portal: {r["primeiro"].date:%d/%m})')
            for l in r['linhas']:
                marca = 'portal' if l['date'] == r['primeiro'].date else 'btg'
                falta = f'  faltam: {",".join(l["missing"])}' if l['missing'] else ''
                self.stdout.write(f'  {l["date"]:%d/%m} {marca:6} valor {_fmt(l["value"]):>14}  investido {_fmt(l["invested"]):>14}'
                                  f'  fluxo {_fmt(l["flow"]):>12}  proventos {_fmt(l["income"])}{falta}')
            if r['incompletos']:
                self.stderr.write(f'  Não gravado: dias sem fechamento {", ".join(f"{d:%d/%m}" for d in r["incompletos"])}')
        self.stdout.write('\nGravado.' if aplicar else '\nSó prévia. Use --aplicar para gravar.')
