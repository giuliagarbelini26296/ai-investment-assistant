"""Opt-in notices inside the portal only; no messaging or brokerage order endpoints."""
import datetime as dt
from django.conf import settings
from django.utils import timezone
from .models import DecisionPreference,DecisionNotice
from .decisions import evaluate

_next_run=None


def refresh_notices(now=None):
    global _next_run
    now=now or timezone.now()
    if settings.PORTAL['MODO_DEMO'] or _next_run and now<_next_run:
        return 0
    _next_run=now+dt.timedelta(minutes=5)
    count=0
    for preference in DecisionPreference.objects.filter(monitor_enabled=True).select_related('user'):
        if not preference.horizon or not preference.profile:
            continue
        data=evaluate(preference.user,preference.account,preference,now=now)
        for row in data['rows']:
            if row['code'] not in ('candidate','concentration') or row['blocks'] or row['price'] is None:
                continue
            detail=' '.join(row['reasons'])+' Fonte: '+row['source']+'. Apenas alerta do cenário; nenhuma ordem enviada.'
            _,created=DecisionNotice.objects.get_or_create(user=preference.user,account=preference.account,
                asset=row['asset'],signal=row['code'],session_date=timezone.localdate(now),
                defaults={'title':row['signal'],'detail':detail,'price':row['price'],'currency':row['currency']})
            count+=int(created)
    return count
