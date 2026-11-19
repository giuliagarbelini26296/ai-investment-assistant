"""Read-only Alpaca IEX minute-bar stream. No orders, balances or BDR substitution.

The IEX feed is partial US venue coverage. REST collection remains independent.
Credentials stay on the server; the only remote endpoint is the fixed data stream.
"""
import datetime as dt
import json
import logging
import threading
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from apps.marketdata.calendars import calendario
from apps.marketdata.models import DailyBar, Quote, QuoteType
from apps.portfolio.models import Asset
from .models import LiveFeedState

SYMBOLS = ('TTWO', 'AMD', 'ORCL', 'CHKP')
URL = 'wss://stream.data.alpaca.markets/v2/iex'
SOURCE = 'alpaca_iex'
LOCK = 921607312
_started = False
log = logging.getLogger('coletor')


def decode(raw, now=None):
    """Use completed minute bars, not bid/ask midpoints or unfiltered trade ticks."""
    now = now or timezone.now()
    out = []
    try:
        events = json.loads(raw, parse_float=Decimal)
        if not isinstance(events, list):
            return []
        for e in events:
            if not isinstance(e, dict) or e.get('T') != 'b' or e.get('S') not in SYMBOLS:
                continue
            try:
                price = Decimal(str(e['c']))
                moment = dt.datetime.fromisoformat(e['t'].replace('Z', '+00:00'))
                if moment.tzinfo is None or not dt.timedelta(0) <= now-moment <= dt.timedelta(minutes=3):
                    continue
                if not price.is_finite() or not 0 < price < Decimal('1000000000'):
                    continue
                out.append((e['S'], price, moment))
            except (KeyError, ValueError, TypeError, InvalidOperation, OverflowError):
                continue
    except (ValueError, TypeError, InvalidOperation):
        return []
    return out


def persist(events, now=None):
    now = now or timezone.now()
    saved = []
    with transaction.atomic():
        for symbol, price, moment in events:
            if not dt.timedelta(0) <= now-moment <= dt.timedelta(minutes=3):
                continue
            # Never create a position, map AMD to A1MD34, or TTWO to T1TW34.
            assets = Asset.objects.filter(symbol=symbol, market__in=['NYSE', 'NASDAQ'], is_active=True)
            for asset in assets:
                cal = calendario(asset.market)
                sit = cal.situacao(now)
                if sit.estado != 'aberto' or not sit.sessao or moment.astimezone(cal.tz).date() != sit.sessao.data:
                    continue
                previous_session = cal.ultima_sessao_encerrada(now)
                previous = (DailyBar.objects.filter(asset=asset, session_date=previous_session.data,
                    provisional=False).exclude(source=SOURCE).order_by('-collected_at').first()
                    if previous_session else None)
                _, created = Quote.objects.get_or_create(asset=asset, source=SOURCE, market_time=moment,
                    defaults={'price':price, 'currency':'USD', 'previous_close':previous.close if previous else None,
                        'collected_at':now, 'quote_type':QuoteType.REAL_TIME, 'declared_delay_minutes':0,
                        'source_version':'v2/iex/minute-bars', 'raw':{'feed':'iex', 'coverage':'partial',
                            'transport':'websocket', 'interval':'1min',
                            'previous_close_source':previous.source if previous else None}})
                if created:
                    saved.append(symbol)
        if saved:
            LiveFeedState.objects.update_or_create(source=SOURCE, defaults={
                'last_success':now, 'last_attempt':now, 'error':'', 'symbols':sorted(set(saved))})
    return saved


def run_forever():
    from websockets.sync.client import connect
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_try_advisory_lock(%s)', [LOCK])
        if not cursor.fetchone()[0]:
            return
    locked_connection = connection.connection
    backoff = 5
    try:
        while True:
            try:
                if connection.connection is not locked_connection:
                    return
                LiveFeedState.objects.update_or_create(source=SOURCE, defaults={'last_attempt':timezone.now()})
                with connect(URL, open_timeout=10, close_timeout=5, max_size=262144,
                        ping_interval=20, ping_timeout=20) as stream:
                    stream.send(json.dumps({'action':'auth', 'key':settings.ALPACA_API_KEY,
                        'secret':settings.ALPACA_SECRET_KEY}))
                    authenticated = False
                    while True:
                        try:
                            raw = stream.recv(timeout=70)
                        except TimeoutError:
                            if not authenticated:
                                raise RuntimeError('Authentication timeout') from None
                            continue  # no market bars outside regular hours is normal
                        messages = json.loads(raw)
                        if not isinstance(messages, list):
                            raise ValueError('Unexpected payload')
                        if any(isinstance(e,dict) and e.get('T')=='error' for e in messages):
                            raise RuntimeError('Market data request rejected')
                        if not authenticated and any(isinstance(e,dict) and e.get('T')=='success'
                                and e.get('msg')=='authenticated' for e in messages):
                            authenticated = True
                            stream.send(json.dumps({'action':'subscribe','bars':list(SYMBOLS)}))
                        if authenticated:
                            if connection.connection is not locked_connection:
                                return
                            if persist(decode(raw)):
                                backoff = 5
            except Exception:
                # Do not expose provider payloads, credentials or account data.
                log.warning('Alpaca IEX indisponível; coleta periódica dos EUA mantida.')
                if connection.connection is not locked_connection:
                    return
                try:
                    LiveFeedState.objects.update_or_create(source=SOURCE, defaults={
                        'last_attempt':timezone.now(),
                        'error':'Streaming IEX indisponível; verificar credenciais/acesso. Coleta periódica mantida.'})
                except Exception:
                    return
                threading.Event().wait(backoff)
                backoff = min(300, backoff*2)
    finally:
        connection.close()


def start():
    global _started
    if (_started or not settings.PORTAL_ALPACA_STREAM or not settings.ALPACA_API_KEY
            or not settings.ALPACA_SECRET_KEY or settings.PORTAL['MODO_DEMO']
            or not settings.PORTAL.get('COLETA_MERCADO')):
        return
    _started = True
    threading.Thread(target=run_forever, name='alpaca-iex-stream', daemon=True).start()
