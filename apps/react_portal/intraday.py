"""Gráfico de 1 dia: as cotações que o portal coletou no último pregão de um ativo.

Usa só cotações válidas já gravadas (sem consulta nova a fontes). A frequência segue a coleta de
cada fonte: B3 (brapi) a cada ~30 min e com atraso declarado; EUA e cripto bem mais frequentes.
"""
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe

MAX_PONTOS = 240


def _amostra(pontos, limite=MAX_PONTOS):
    if len(pontos) <= limite:
        return pontos
    passo = (len(pontos) - 1) / (limite - 1)
    return [pontos[round(i * passo)] for i in range(limite)]


def intraday_payload(symbol):
    from apps.marketdata.models import Quote, RecordStatus
    from apps.portfolio.models import Asset

    asset = Asset.objects.filter(symbol=symbol.upper()).first()
    if asset is None:
        return None
    base = Quote.objects.filter(asset=asset, status=RecordStatus.VALID).exclude(market_time=None)
    ultima = base.order_by('-market_time').first()
    if ultima is None:
        return {'symbol': asset.symbol, 'date': None, 'points': [], 'source': None}
    dia = timezone.localdate(ultima.market_time)
    vistos, pontos, anterior = set(), [], None
    for q in base.filter(market_time__date=dia, source=ultima.source).order_by('market_time'):
        if q.market_time in vistos:
            continue
        vistos.add(q.market_time)
        anterior = anterior or q.previous_close
        pontos.append({'time': q.market_time, 'price': q.price})
    return {'symbol': asset.symbol, 'date': dia, 'currency': ultima.currency, 'source': ultima.source,
            'delayMinutes': ultima.declared_delay_minutes, 'previousClose': anterior, 'points': _amostra(pontos)}


@never_cache
@require_safe
def intraday(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': {'code': 'authentication_required', 'message': 'Entre com seu usuário do portal.'}}, status=401)
    symbol = request.GET.get('symbol', '').strip()
    if not symbol or len(symbol) > 12 or not symbol.isalnum():
        return JsonResponse({'error': {'code': 'invalid_query', 'message': 'Ativo inválido.'}}, status=400)
    payload = intraday_payload(symbol)
    if payload is None:
        return JsonResponse({'error': {'code': 'not_found', 'message': 'Ativo não encontrado.'}}, status=404)
    return JsonResponse(payload, json_dumps_params={'ensure_ascii': False})
