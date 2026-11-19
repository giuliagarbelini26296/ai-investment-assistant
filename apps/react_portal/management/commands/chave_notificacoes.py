"""Cria, lista ou revoga chaves de serviços externos: notificações (enviar) ou leitura (MCP).

  manage.py chave_notificacoes criar milo --arquivo /caminho/seguro.txt
  manage.py chave_notificacoes criar assistente --escopo leitura --contas ana --arquivo /caminho/seguro.txt
  manage.py chave_notificacoes listar
  manage.py chave_notificacoes revogar milo
"""
import os
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.react_portal.inbound import gerar_chave
from apps.react_portal.models import PushApiKey


class Command(BaseCommand):
    help = 'Gerencia chaves de serviços externos que só enviam notificações do app.'

    def add_arguments(self, parser):
        parser.add_argument('acao', choices=['criar', 'listar', 'revogar'])
        parser.add_argument('nome', nargs='?')
        parser.add_argument('--arquivo', help='Grava a chave neste arquivo (permissão 600) em vez de mostrar na tela.')
        parser.add_argument('--escopo', choices=['notificacoes', 'leitura'], default='notificacoes',
                            help='notificacoes: só envia avisos. leitura: só consulta pelo MCP.')
        parser.add_argument('--contas', default='ana,bruno', help='Carteiras visíveis no MCP, separadas por vírgula.')

    def handle(self, acao, nome=None, arquivo=None, escopo='notificacoes', contas='ana,bruno', **_):
        if acao == 'listar':
            for k in PushApiKey.objects.order_by('name'):
                estado = f'revogada em {k.revoked_at:%d/%m/%Y}' if k.revoked_at else 'ativa'
                uso = f'{k.last_used_at:%d/%m %H:%M}' if k.last_used_at else 'nunca usada'
                extra = f'{k.mcp_calls.count()} consultas · carteiras {",".join(k.accounts)}' if k.scope == 'leitura' else f'{k.notifications.count()} envios'
                self.stdout.write(f'{k.name} [{k.scope}]: {k.prefix}… · {estado} · {uso} · {extra}')
            return
        if not nome:
            raise CommandError('Informe o nome da chave.')
        if acao == 'revogar':
            n = PushApiKey.objects.filter(name=nome, revoked_at__isnull=True).update(revoked_at=timezone.now())
            self.stdout.write('Chave revogada.' if n else 'Nenhuma chave ativa com esse nome.')
            return
        if PushApiKey.objects.filter(name=nome).exists():
            raise CommandError('Já existe uma chave com esse nome. Revogue-a e crie outra com nome novo.')
        lista = [c.strip() for c in contas.split(',') if c.strip()]
        if not lista or not set(lista) <= {'ana', 'bruno'}:
            raise CommandError('--contas aceita ana e/ou bruno.')
        token = gerar_chave(nome, escopo, lista)
        if arquivo:
            caminho = Path(arquivo)
            fd = os.open(caminho, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w') as fh:
                fh.write(token + '\n')
            self.stdout.write(f'Chave "{nome}" criada e gravada em {caminho} (permissão 600).')
        else:
            self.stdout.write(token)
