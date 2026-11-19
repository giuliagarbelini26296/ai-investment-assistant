"""BRL crypto market reference, not a broker connection or a custody assignment."""
import datetime as dt
from django.conf import settings
from django.utils import timezone
from apps.marketdata.adapters.binance import BinanceAdapter
from apps.marketdata.adapters.base import validar_cotacao
from .models import Balance, CurrencyQuote


def refresh_currency_quotes(now=None, adapter=None):
    now=now or timezone.now()
    if settings.PORTAL['MODO_DEMO']:
        return {'state':'disabled'}
    assets={b.asset_id:b.asset for b in Balance.objects.filter(asset__market='CRIPTO', currency='BRL').select_related('asset')}
    updated=0
    for asset in assets.values():
        if asset.symbol!='BTC':
            continue
        old=CurrencyQuote.objects.filter(asset=asset,currency='BRL').first()
        if old and now-old.collected_at<dt.timedelta(minutes=5):
            continue
        result=(adapter or BinanceAdapter(agora=lambda:now)).cotar(['BTCBRL'])
        for quote in result.cotacoes:
            if quote.simbolo!='BTCBRL' or validar_cotacao(quote,'BRL',now):
                continue
            _,created=CurrencyQuote.objects.get_or_create(asset=asset,currency='BRL',market_time=quote.horario_mercado,
                defaults={'price':quote.preco,'previous_close':quote.fechamento_anterior,
                    'change_24h_pct':quote.variacao_24h_pct,'source':quote.fonte,
                    'collected_at':quote.horario_coleta})
            updated+=int(created)
    return {'state':'ok','updated':updated}
