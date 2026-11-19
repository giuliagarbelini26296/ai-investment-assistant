"""One public WebSocket in the collector; REST remains the failover.

Records a sampled price every 15 seconds, not every tick. No order endpoint or key.
"""
import datetime as dt
import json
import logging
import threading
from decimal import Decimal,InvalidOperation
from django.conf import settings
from django.db import connection,transaction
from django.utils import timezone
from apps.marketdata.models import Quote
from apps.managed_portfolios.models import CurrencyQuote
from apps.portfolio.models import Asset
from .models import LiveFeedState

log=logging.getLogger('coletor')
SYMBOLS={'BTCBRL':('BTC','BRL'),'SOLBRL':('SOL','BRL'),'BTCUSDT':('BTC','USDT'),'SOLUSDT':('SOL','USDT')}
URL='wss://stream.binance.com:443/stream?streams='+'/'.join(s.lower()+'@ticker' for s in SYMBOLS)
_started=False


def decode(raw,now=None):
    now=now or timezone.now()
    try:
        event=json.loads(raw).get('data',{})
        symbol=event['s'];price=Decimal(event['c']);previous=Decimal(event['o']);change=Decimal(event['P'])
        moment=dt.datetime.fromtimestamp(int(event['E'])/1000,tz=dt.UTC)
        if symbol not in SYMBOLS or event.get('e')!='24hrTicker':return None
        if not all(v.is_finite() for v in (price,previous,change)) or price<=0 or previous<=0:return None
        if abs((now-moment).total_seconds())>60:return None
        if price>Decimal('999999999999') or abs(change)>Decimal('999999'):return None
        return symbol,{'price':price,'previous_close':previous,'change_24h_pct':change,'market_time':moment}
    except (ValueError,KeyError,TypeError,AttributeError,InvalidOperation,OverflowError,OSError):return None


def persist(latest,now=None):
    now=now or timezone.now();saved=[]
    assets={a.symbol:a for a in Asset.objects.filter(market='CRIPTO',symbol__in=['BTC','SOL'],is_active=True)}
    with transaction.atomic():
        for symbol,data in latest.items():
            ticker,currency=SYMBOLS[symbol]
            if ticker not in assets or now-data['market_time']>dt.timedelta(seconds=60):continue
            if currency=='BRL':
                _,created=CurrencyQuote.objects.get_or_create(asset=assets[ticker],currency=currency,market_time=data['market_time'],
                    defaults={**{k:v for k,v in data.items() if k!='market_time'},'collected_at':now,'source':'binance'})
            else:
                _,created=Quote.objects.get_or_create(asset=assets[ticker],source='binance',market_time=data['market_time'],
                    defaults={**{k:v for k,v in data.items() if k!='market_time'},'currency':currency,
                        'collected_at':now,'quote_type':'spot','source_version':'websocket',
                        'raw':{'symbol':symbol,'transport':'websocket'}})
            if created:saved.append(symbol)
        if saved:
            LiveFeedState.objects.update_or_create(source='binance_stream',defaults={'last_success':now,'last_attempt':now,'error':'','symbols':saved})
    return saved


def run_forever():
    from websockets.sync.client import connect
    backoff=5
    # PostgreSQL connection-scoped lock prevents two collector processes subscribing twice.
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_try_advisory_lock(%s)',[921607311])
        if not cursor.fetchone()[0]:return
    try:
        while True:
            try:
                LiveFeedState.objects.update_or_create(source='binance_stream',defaults={'last_attempt':timezone.now()})
                with connect(URL,open_timeout=10,close_timeout=5,max_size=65536,ping_interval=20,ping_timeout=20) as stream:
                    latest={};last_flush=timezone.now()-dt.timedelta(seconds=15)
                    backoff=5
                    while True:
                        result=decode(stream.recv(timeout=40))
                        if result:latest[result[0]]=result[1]
                        now=timezone.now()
                        if (now-last_flush).total_seconds()>=15:
                            persist(latest,now);last_flush=now
            except Exception:
                # Do not log response bodies, URLs, credentials or user holdings.
                log.warning('Fluxo público de cripto interrompido; coleta periódica permanece disponível.')
                try:LiveFeedState.objects.update_or_create(source='binance_stream',defaults={'last_attempt':timezone.now(),'error':'Conexão interrompida; tentando novamente com espera progressiva.'})
                except Exception:
                    # A lost lock connection must never continue an unlocked stream.
                    return
                threading.Event().wait(backoff);backoff=min(300,backoff*2)
    finally:
        connection.close()


def start():
    global _started
    if _started or not settings.PORTAL_CRYPTO_STREAM or settings.PORTAL['MODO_DEMO'] or not settings.PORTAL.get('COLETA_MERCADO'):return
    _started=True
    threading.Thread(target=run_forever,name='public-crypto-stream',daemon=True).start()
