"""Public direct BRL crypto references; no currency pegs, orders or broker credentials."""
import datetime as dt
from django.conf import settings
from django.utils import timezone
from apps.marketdata.adapters.binance import BinanceAdapter
from apps.marketdata.adapters.base import validar_cotacao, AdapterError
from apps.portfolio.models import Asset
from apps.managed_portfolios.models import CurrencyBar, CurrencyQuote

_attempts={}


def refresh_crypto_analysis(now=None,adapter=None):
    now=now or timezone.now()
    if settings.PORTAL['MODO_DEMO']:
        return {'status':'disabled'}
    assets=list(Asset.objects.filter(is_active=True,market='CRIPTO',symbol__in=['BTC','SOL']))
    quotes=[];histories=[]
    for asset in assets:
        q=CurrencyQuote.objects.filter(asset=asset,currency='BRL').first()
        if (not q or now-q.collected_at>=dt.timedelta(minutes=5)) and _attempts.get(('quote',asset.pk),now)<=now:
            quotes.append(asset)
            _attempts[('quote',asset.pk)]=now+dt.timedelta(minutes=5)
        bar=CurrencyBar.objects.filter(asset=asset,currency='BRL').order_by('-collected_at').first()
        if (not bar or now-bar.collected_at>=dt.timedelta(hours=24)) and _attempts.get(('history',asset.pk),now)<=now:
            histories.append(asset)
            _attempts[('history',asset.pk)]=now+dt.timedelta(hours=24)
    if not quotes and not histories:
        return {'status':'recent','quotes':0,'bars':0}
    provider=adapter or BinanceAdapter(agora=lambda:now)
    n_quotes=n_bars=0
    unavailable=[]
    try:
        if quotes:
            mapping={a.symbol+'BRL':a for a in quotes}
            try:
                result=provider.cotar(list(mapping))
                responses=result.cotacoes
                unavailable.extend(getattr(result,'falhas',{}))
            except AdapterError:
                responses=[]
                unavailable.extend(mapping)
            for q in responses:
                if q.simbolo not in mapping or validar_cotacao(q,'BRL',now):
                    continue
                _,created=CurrencyQuote.objects.get_or_create(asset=mapping[q.simbolo],currency='BRL',market_time=q.horario_mercado,
                    defaults={'price':q.preco,'previous_close':q.fechamento_anterior,'change_24h_pct':q.variacao_24h_pct,
                        'collected_at':q.horario_coleta,'source':q.fonte})
                n_quotes+=int(created)
        for asset in histories:
            try:
                bars=provider.historico(asset.symbol+'BRL',dias=90)
            except AdapterError:
                unavailable.append(asset.symbol+'BRL:historico')
                continue
            for bar in bars:
                if bar.provisorio or bar.close<=0 or bar.data>=timezone.localdate(now):
                    continue
                CurrencyBar.objects.update_or_create(asset=asset,currency='BRL',date=bar.data,source=bar.fonte,
                    defaults={'close':bar.close,'collected_at':now})
                n_bars+=1
    finally:
        if adapter is None:
            provider.client.close()
    return {'status':'partial' if unavailable else 'ok','quotes':n_quotes,'bars':n_bars,'unavailable':unavailable}
