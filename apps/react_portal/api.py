"""Read-only API. Positions/valuation use the existing Django services."""
from decimal import Decimal

from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe

from apps.portfolio import services

ZERO = Decimal(0)
SORT_FIELDS = {'country', 'symbol', 'price', 'dayPercent', 'value', 'totalGain', 'totalReturn'}


def native_position(line, include_history=True):
    from apps.marketdata import history
    from apps.marketdata.models import DailyBar

    asset, pos, val = line.asset, line.posicao, line.avaliacao
    quote = line.atualidade.cotacao if line.atualidade else None
    previous = quote.previous_close if quote else None
    day_base = day_gain = None
    daily_eligible = line.atualidade is not None and line.atualidade.permite_alertas
    if val is not None and previous is not None and previous > 0 and daily_eligible:
        day_base = pos.quantidade * previous
        day_gain = val.valor_atual - day_base
    source = history.fonte_principal(asset) if include_history else None
    bars = list(DailyBar.objects.filter(asset=asset, source=source)
                .order_by('-session_date').values('session_date', 'close')[:400]) if source else []
    balance = asset.position_balance
    return {
        'id': asset.pk, 'symbol': asset.symbol, 'name': asset.name,
        'market': asset.market,
        'country': 'BR' if asset.market == 'B3' else 'US' if asset.market in ('NYSE', 'NASDAQ') else 'OTHER',
        'currency': asset.native_currency, 'broker': asset.get_custody_display(),
        'brokerCode': asset.custody, 'sector': asset.sector or 'Não informado',
        'quantity': pos.quantidade, 'averagePrice': pos.preco_medio, 'costBasis': pos.investido,
        'price': val.preco_atual if val else None, 'value': val.valor_atual if val else None,
        'totalGain': val.resultado if val else None, 'totalReturn': val.retorno_pct if val else None,
        'previousClose': previous, 'dayGain': day_gain, 'dayBase': day_base,
        'dayPercent': val.variacao_dia_pct if val and day_base is not None else None,
        'dayBasis': 'previous_close' if day_base is not None else None,
        'unavailable': {
            'valuation': None if val else line.motivo_sem_preco,
            'day': None if day_base is not None else 'Sem cotação atual e fechamento anterior confirmados para este pregão.',
        },
        'quoteAt': quote.market_time if quote else None,
        'collectedAt': quote.collected_at if quote else None,
        'quoteSource': quote.source if quote else None,
        'quoteState': line.atualidade.descricao if line.atualidade else line.motivo_sem_preco,
        'quoteStatus': line.atualidade.estado if line.atualidade else 'sem_cotacao',
        'referenceDate': balance.reference_date if balance else None,
        'historySource': source,
        'history': [{'date': b['session_date'], 'close': b['close']} for b in reversed(bars)],
    }


def native_totals(lines, positions):
    """Existing native consolidation; day values use exact previous-close bases."""
    result = []
    for currency, total in services.consolidar_por_moeda(lines).items():
        rows = [p for p in positions if p['currency'] == currency]
        daily = [p for p in rows if p['dayGain'] is not None]
        day_base = sum((p['dayBase'] for p in daily), ZERO)
        day = sum((p['dayGain'] for p in daily), ZERO) if daily else None
        result.append({
            'currency': currency, 'count': total.posicoes_total,
            'quoted': total.posicoes_avaliadas, 'dayCount': len(daily),
            'invested': total.investido_total, 'assessedCost': total.investido_avaliado,
            'value': total.valor_atual if total.posicoes_avaliadas else None,
            'gain': total.resultado if total.posicoes_avaliadas else None,
            'returnPct': total.retorno_pct,
            'day': day, 'dayBase': day_base if daily else None,
            'dayPct': day / day_base * Decimal(100) if day_base > 0 else None,
            'unquotedSymbols': total.sem_preco,
        })
    return result


def select_lines(lines, query):
    broker = query.get('broker', 'all')
    status = query.get('result', 'all')
    search = query.get('search', '').strip()
    sort = query.get('sort', 'country')
    direction = query.get('direction', 'asc')
    if broker not in ('all', 'BTG', 'AVENUE') or status not in ('all', 'gain', 'loss'):
        raise ValueError('Filtro de corretora ou resultado inválido.')
    if sort not in SORT_FIELDS or direction not in ('asc', 'desc') or len(search) > 120:
        raise ValueError('Ordenação ou busca inválida.')
    selected = []
    for line in lines:
        if line.posicao is None:
            continue
        if broker != 'all' and line.asset.custody != broker:
            continue
        gain = line.avaliacao.resultado if line.avaliacao else None
        if status == 'gain' and (gain is None or gain <= 0):
            continue
        if status == 'loss' and (gain is None or gain >= 0):
            continue
        text = f'{line.asset.symbol} {line.asset.name} {line.asset.get_custody_display()}'.casefold()
        if search.casefold() not in text:
            continue
        selected.append(line)
    return selected, sort, direction


def portfolio_payload(query, username, now=None):
    from apps.mercado.models import MacroSerie
    from apps.mercado.resumo import estado_fontes

    now = now or timezone.now()
    from apps.managed_portfolios.api import ACCOUNTS, managed_positions, totals as managed_totals
    account = query.get('account','ana')
    if account not in ('ana','bruno'):
        raise ValueError('Carteira inválida. Escolha Ana ou Bruno.')
    if account == 'ana':
        lines, sort, direction = select_lines(services.linhas_da_carteira(agora=now), query)
        include_history = query.get('history', '1') != '0'
        positions = [native_position(line, include_history) for line in lines]
        available = [p for p in positions if p[sort] is not None]
        missing = [p for p in positions if p[sort] is None]
        available.sort(key=lambda p: (p[sort], p['symbol']), reverse=direction == 'desc')
        positions = available + missing
        totals = native_totals(lines,positions)
    else:
        positions = managed_positions(account,query,now)
        totals = managed_totals(positions)
    references = sorted({p['referenceDate'] for p in positions if p['referenceDate']})
    sources = [
        {'code': s['codigo'], 'name': s['nome'], 'state': s['estado'],
         'detail': s['detalhe'], 'updatedAt': s['quando']}
        for s in estado_fontes(now)
    ]
    macro = [MacroSerie.objects.filter(codigo=code).order_by('-data')
             .values('codigo', 'nome', 'data', 'valor', 'collected_at').first()
             for code in (432, 433, 13522)]
    from .models import BrokerSnapshot
    snapshot = BrokerSnapshot.objects.filter(account=account).first()
    broker_day = None
    if snapshot and snapshot.data.get('totals', {}).get('daily_gain') is not None:
        broker_day = {'value': snapshot.data['totals']['daily_gain'], 'percent': snapshot.data['totals'].get('daily_return_percent'), 'currency': snapshot.data['currency'], 'capturedAt': snapshot.captured_at, 'source': snapshot.data.get('source', 'BTG')}
    return {
        'meta': {
            'demo': bool(settings.PORTAL['MODO_DEMO']), 'schemaVersion': '1.0', 'generatedAt': now, 'mode': 'database', 'readOnly': True,
            'ownerLabel': 'Ana' if account=='ana' else 'Bruno', 'accessScope': 'shared_portfolio',
            'account':account,'accounts':ACCOUNTS,'brokerDay':broker_day,
            'saleUrl':'/vendas/registrar/?carteira='+account,
            'purchaseUrl':'/compras/registrar/' if account=='ana' else '/carteiras/bruno/compras/registrar/',
            'positionReferenceDate': references[-1] if references else None,
            'refreshSeconds': 60,
            'legacyUrl': settings.ID3_LEGACY_URL,
            'disclaimer': 'Consulta ao Django. Cotações mantêm fonte e horário próprios; '
                          'não são preços necessariamente em tempo real. Resultados excluem taxas, '
                          'impostos e proventos. O dia mede a variação das posições atuais, sem fluxos intradiários.',
        },
        'positions': positions, 'totals': totals,
        'sources': sources, 'macro': [m for m in macro if m],
    }


@never_cache
@require_safe
def portfolio(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': {'code': 'authentication_required',
                                      'message': 'Entre com seu usuário do portal.',
                                      'loginUrl': getattr(settings, 'ID3_API_LOGIN_URL', '/conta/entrar/?next=/')}}, status=401)
    try:
        payload = portfolio_payload(request.GET, request.user.get_username())
    except ValueError as exc:
        return JsonResponse({'error': {'code': 'invalid_query', 'message': str(exc)}}, status=400)
    return JsonResponse(payload, json_dumps_params={'ensure_ascii': False})
