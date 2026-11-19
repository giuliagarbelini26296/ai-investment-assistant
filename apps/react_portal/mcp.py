"""Servidor MCP do ID3 (Model Context Protocol, transporte Streamable HTTP, sem estado).

POST /mcp/  com  Authorization: Bearer <chave de escopo "leitura">

Somente leitura: nenhuma ferramenta registra, altera ou envia nada. Cada chave enxerga só as
carteiras configuradas nela. Limite de 120 consultas a ferramentas por hora por chave, todas registradas.
"""
import datetime as dt
import json
from decimal import Decimal

from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt

from .inbound import chave_valida
from .models import McpCall

VERSOES = ('2025-06-18', '2025-03-26', '2024-11-05')
LIMITE_HORA = 120
ORIGENS = ('https://portal.example.com',)
INSTRUCOES = (
    'Portal de Investimentos, somente leitura. Valores na moeda nativa de cada ativo (BRL ou USD), '
    'sem conversão cambial. Cotações têm fonte e horário próprios e podem ter atraso; não são ordens nem '
    'recomendações. Carteiras: ana (BTG + Avenue) e bruno (BTG + cripto). "Dia" compara com o fechamento '
    'anterior (cripto: 24 h).'
)


def _num(v):
    if v is None:
        return None
    return float(round(Decimal(v), 6)) if isinstance(v, Decimal) else v


def _iso(v):
    return v.isoformat() if hasattr(v, 'isoformat') else v


def _carteira(account):
    from .api import portfolio_payload
    return portfolio_payload({'account': account, 'history': '0'}, 'mcp')


def _posicao(p):
    return {
        'ativo': p['symbol'], 'nome': p['name'], 'mercado': p['market'], 'moeda': p['currency'], 'corretora': p['broker'],
        'quantidade': _num(p['quantity']), 'preco_medio': _num(p['averagePrice']), 'investido': _num(p['costBasis']),
        'cotacao': _num(p['price']), 'valor': _num(p['value']), 'resultado': _num(p['totalGain']),
        'retorno_pct': _num(p['totalReturn']), 'dia_pct': _num(p['dayPercent']), 'dia_valor': _num(p['dayGain']),
        'cotacao_em': _iso(p['quoteAt']), 'fonte': p['quoteSource'], 'situacao': p['quoteStatus'],
    }


def _totais(t):
    return {'moeda': t['currency'], 'posicoes': t['count'], 'com_cotacao': t['quoted'], 'investido': _num(t['invested']),
            'valor': _num(t['value']), 'resultado': _num(t['gain']), 'retorno_pct': _num(t['returnPct']),
            'dia': _num(t['day']), 'dia_pct': _num(t['dayPct'])}


def _conta(args, key):
    conta = args.get('conta')
    if conta is None:
        return list(key.accounts)
    if conta not in key.accounts:
        raise ValueError(f'Carteira "{conta}" não disponível para esta chave. Disponíveis: {", ".join(key.accounts)}.')
    return [conta]


# ---------- ferramentas ----------

def t_carteira(args, key):
    saida = {}
    for conta in _conta(args, key):
        d = _carteira(conta)
        saida[conta] = {'consulta_em': _iso(d['meta']['generatedAt']), 'totais': [_totais(t) for t in d['totals']],
                        'posicoes': [_posicao(p) for p in d['positions']]}
    return saida


def t_movimento(args, key):
    limite = min(max(int(args.get('limite', 5)), 1), 15)
    saida = {}
    for conta in _conta(args, key):
        todas = _carteira(conta)['positions']
        pos = sorted((_posicao(p) for p in todas if p['dayPercent'] is not None), key=lambda p: p['dia_pct'], reverse=True)
        saida[conta] = {'maiores_altas': [p for p in pos if p['dia_pct'] > 0][:limite],
                        'maiores_quedas': [p for p in reversed(pos) if p['dia_pct'] < 0][:limite],
                        'sem_dado_do_dia': [p['symbol'] for p in todas if p['dayPercent'] is None]}
    return saida


def t_cotacoes(args, key):
    pedidos = {str(s).upper() for s in (args.get('ativos') or [])}
    saida = []
    for conta in _conta({}, key):
        for p in _carteira(conta)['positions']:
            if (not pedidos or p['symbol'] in pedidos) and p['symbol'] not in {x['ativo'] for x in saida}:
                q = _posicao(p)
                saida.append({k: q[k] for k in ('ativo', 'nome', 'moeda', 'cotacao', 'dia_pct', 'cotacao_em', 'fonte', 'situacao')})
    faltam = sorted(pedidos - {x['ativo'] for x in saida})
    return {'cotacoes': saida, 'fora_das_carteiras': faltam}


def t_noticias(args, key):
    from apps.noticias.models import Article

    from .push import ativos_relacionados
    horas = min(max(int(args.get('horas', 24)), 1), 72)
    mapa = ativos_relacionados(tuple(key.accounts))
    alvo = str(args.get('ativo', '')).upper()
    ids = [pk for pk, s in mapa.items() if not alvo or s == alvo]
    artigos = (Article.objects.filter(published_at__gte=timezone.now() - dt.timedelta(hours=horas), assets__in=ids)
               .distinct().prefetch_related('assets').order_by('-published_at')[:20])
    return {'noticias': [{'titulo': a.title, 'resumo': (a.description or '')[:400], 'publicado_em': _iso(a.published_at),
                          'veiculo': a.publisher, 'ativos': sorted({mapa[x.pk] for x in a.assets.all() if x.pk in mapa}),
                          'link': a.url} for a in artigos]}


def t_evolucao(args, key):
    from .evolution import evolution_payload
    saida = {}
    for conta in _conta(args, key):
        e = evolution_payload(conta)
        saida[conta] = {
            'patrimonio_diario': {m: [{'data': _iso(r['date']), 'valor': _num(r['value']), 'investido': _num(r['invested']),
                                       'completo': r['complete']} for r in rows] for m, rows in e['snapshots'].items()},
            'rentabilidade': {m: {'inicio': _iso(p['start']), 'fim': _iso(p['end']), 'carteira_pct': _num(p['portfolio']),
                                  'referencia': p['benchmarkLabel'], 'referencia_pct': _num(p['reference']), 'cdi_pct': _num(p['cdi']),
                                  'avisos': p['warnings'][:1]} for m, p in e['performance'].items()},
            'observacao': e['performanceNote']}
    return saida


def t_indicadores(args, key):
    from apps.mercado.models import MacroSerie
    nomes = {432: 'selic_meta_aa', 433: 'ipca_mes', 13522: 'ipca_12m', 12: 'cdi_dia'}
    saida = {}
    for codigo, nome in nomes.items():
        m = MacroSerie.objects.filter(codigo=codigo).order_by('-data').first()
        if m:
            saida[nome] = {'valor_pct': _num(m.valor), 'referencia': _iso(m.data)}
    return {'indicadores': saida, 'fonte': 'Banco Central do Brasil (SGS)'}


CONTA = {'type': 'string', 'enum': ['ana', 'bruno'], 'description': 'Carteira; omita para todas as permitidas.'}
FERRAMENTAS = {
    'carteira': (t_carteira, 'Posições e totais por moeda (quantidade, preço médio, cotação, valor, resultado e variação do dia).',
                 {'conta': CONTA}),
    'movimento_do_dia': (t_movimento, 'Maiores altas e quedas do dia entre os ativos da carteira, desde o fechamento anterior.',
                         {'conta': CONTA, 'limite': {'type': 'integer', 'minimum': 1, 'maximum': 15, 'default': 5}}),
    'cotacoes': (t_cotacoes, 'Última cotação dos ativos das carteiras, com fonte, horário e variação do dia.',
                 {'ativos': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Códigos; omita para todos.'}}),
    'noticias': (t_noticias, 'Notícias recentes sobre os ativos da carteira (BDRs incluem a empresa americana).',
                 {'ativo': {'type': 'string'}, 'horas': {'type': 'integer', 'minimum': 1, 'maximum': 72, 'default': 24}}),
    'evolucao': (t_evolucao, 'Patrimônio registrado dia a dia e rentabilidade comparada (Ibovespa/S&P 500 e CDI).',
                 {'conta': CONTA}),
    'indicadores': (t_indicadores, 'Selic meta, IPCA (mês e 12 meses) e CDI diário mais recentes do Banco Central.', {}),
}


def _lista():
    return [{'name': n, 'description': d, 'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'openWorldHint': False},
             'inputSchema': {'type': 'object', 'properties': props, 'additionalProperties': False}}
            for n, (_, d, props) in FERRAMENTAS.items()]


# ---------- protocolo ----------

def _erro(id_, codigo, msg):
    return {'jsonrpc': '2.0', 'id': id_, 'error': {'code': codigo, 'message': msg}}


def _tratar(msg, key, agora):
    if not isinstance(msg, dict) or msg.get('jsonrpc') != '2.0' or not isinstance(msg.get('method'), str):
        return _erro(msg.get('id') if isinstance(msg, dict) else None, -32600, 'Requisição JSON-RPC inválida.')
    metodo, id_, params = msg['method'], msg.get('id'), msg.get('params') or {}
    if 'id' not in msg:  # notificação: sem resposta
        return None
    if metodo == 'initialize':
        pedida = params.get('protocolVersion')
        return {'jsonrpc': '2.0', 'id': id_, 'result': {
            'protocolVersion': pedida if pedida in VERSOES else VERSOES[0],
            'capabilities': {'tools': {'listChanged': False}},
            'serverInfo': {'name': 'portal-investimentos', 'title': 'Portal de Investimentos', 'version': '1.0.0'},
            'instructions': INSTRUCOES}}
    if metodo == 'ping':
        return {'jsonrpc': '2.0', 'id': id_, 'result': {}}
    if metodo == 'tools/list':
        return {'jsonrpc': '2.0', 'id': id_, 'result': {'tools': _lista()}}
    if metodo == 'tools/call':
        nome, args = params.get('name'), params.get('arguments') or {}
        if nome not in FERRAMENTAS or not isinstance(args, dict):
            McpCall.objects.create(key=key, method=metodo, tool=str(nome)[:40], ok=False, created_at=agora)
            return _erro(id_, -32602, f'Ferramenta desconhecida: {nome}.')
        try:
            dados = FERRAMENTAS[nome][0](args, key)
            ok = True
        except (ValueError, TypeError) as exc:
            dados, ok = {'erro': str(exc)}, False
        McpCall.objects.create(key=key, method=metodo, tool=nome, ok=ok, created_at=agora)
        texto = json.dumps(dados, ensure_ascii=False, default=str)
        return {'jsonrpc': '2.0', 'id': id_, 'result': {'content': [{'type': 'text', 'text': texto}],
                                                        'structuredContent': json.loads(texto), 'isError': not ok}}
    return _erro(id_, -32601, f'Método não suportado: {metodo}.')


@csrf_exempt
@never_cache
def servidor(request):
    origem = request.headers.get('Origin')
    if origem and origem not in ORIGENS:
        return JsonResponse(_erro(None, -32000, 'Origem não permitida.'), status=403)
    if request.method != 'POST':
        resposta = HttpResponse(status=405)
        resposta['Allow'] = 'POST'
        return resposta
    key = chave_valida(request, 'leitura')
    if not key or not key.accounts:
        resposta = JsonResponse(_erro(None, -32001, 'Chave de leitura ausente, inválida ou revogada.'), status=401)
        resposta['WWW-Authenticate'] = 'Bearer realm="portal-mcp"'
        return resposta
    agora = timezone.now()
    if key.mcp_calls.filter(created_at__gte=agora - dt.timedelta(hours=1)).count() >= LIMITE_HORA:
        return JsonResponse(_erro(None, -32002, f'Limite de {LIMITE_HORA} consultas por hora atingido.'), status=429)
    try:
        corpo = json.loads(request.body or b'')
    except ValueError:
        return JsonResponse(_erro(None, -32700, 'JSON inválido.'), status=400)
    key.last_used_at = agora
    key.save(update_fields=['last_used_at'])
    lote = corpo if isinstance(corpo, list) else [corpo]
    if not lote or len(lote) > 20:
        return JsonResponse(_erro(None, -32600, 'Envie de 1 a 20 mensagens.'), status=400)
    respostas = [r for r in (_tratar(m, key, agora) for m in lote) if r is not None]
    if not respostas:
        return HttpResponse(status=202)
    return JsonResponse(respostas if isinstance(corpo, list) else respostas[0], safe=False, json_dumps_params={'ensure_ascii': False})
