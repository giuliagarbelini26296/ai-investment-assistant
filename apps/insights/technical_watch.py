"""Market observation only. No budget, recommendation quantities or account changes."""
import datetime as dt
from decimal import Decimal
from types import SimpleNamespace
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from apps.alerts import notify
from apps.alerts.models import NotificationKind,Channel
from .models import TechnicalWatch,DecisionNotice
from .decisions import decision_row

_next_run=None
# Same transparent technical criteria shown in evaluation; not a suitability profile.
PARAMETERS=SimpleNamespace(entry_tolerance=Decimal('2'),safety_margin=Decimal('20'),horizon='tatico',profile='observacao')
METHOD='MM20 ≥ MM50, preço ≥ MM50, RSI14 entre 35 e 65 e preço até 2% acima da MM20; dados válidos e sem bloqueio de fundamentos.'


def refresh_watches(now=None):
    global _next_run
    now=now or timezone.now()
    if settings.PORTAL['MODO_DEMO'] or _next_run and now<_next_run:return 0
    _next_run=now+dt.timedelta(minutes=5)
    count=0
    for watch in TechnicalWatch.objects.filter(active=True).select_related('asset','user'):
        if not watch.asset.is_active or not (watch.ana or watch.bruno):continue
        # A faulty instrument must not stop observation of every other instrument.
        try:
            row=decision_row(watch.asset,None,PARAMETERS,None,now)
            count+=record(watch.pk,row,now)
        except Exception:
            TechnicalWatch.objects.filter(pk=watch.pk).update(last_checked=now,last_status='indisponivel',
                detail='Não foi possível validar os dados nesta passagem. Nenhum sinal estimado.')
    return count


@transaction.atomic
def record(watch_id,row,now):
    watch=TechnicalWatch.objects.select_for_update().select_related('user','asset').get(pk=watch_id)
    if not watch.active or not (watch.ana or watch.bruno):return 0
    if watch.last_checked and watch.last_checked>=now:return 0
    watch.last_checked=now
    if row['blocks'] or row['price'] is None:
        watch.last_status='dados_insuficientes'
        watch.detail=' '.join(row['blocks']) or 'Cotação indisponível.'
        watch.save(update_fields=['last_checked','last_status','detail'])
        return 0  # Missing data never rearms a previously true condition.
    eligible=bool(row['eligible'])
    previous=watch.last_eligible
    watch.last_eligible=eligible
    watch.last_status='condicao_atendida' if eligible else 'aguardando'
    watch.detail=' '.join(row['reasons']+row['warnings'])
    changed=eligible and previous is not True or previous is True and not eligible
    cooldown=not watch.last_notice_at or now-watch.last_notice_at>=dt.timedelta(hours=6)
    created_notices=0
    if changed and cooldown:
        title='Condição técnica atendida' if eligible else 'Condição técnica deixou de ser atendida'
        owners=[owner for owner,enabled in [('ana',watch.ana),('bruno',watch.bruno)] if enabled]
        market_time=row.get('quote_time')
        stamp=timezone.localtime(market_time).strftime('%d/%m %H:%M') if market_time else 'não informado'
        body=(f"{watch.asset.symbol} · {', '.join(o.title() for o in owners)}\n"
            f"Preço observado: {row['currency']} {row['price']:.2f}. Fonte: {row['source']}; mercado: {stamp}.\n"
            f"{title}. {METHOD}\n"+' '.join(row['warnings'])+'\n'
            'Quantidade não calculada: orçamento livre e limites individuais não confirmados. Não use limite operacional como caixa.\n'
            'Observação técnica; não prevê alta/queda nem envia ordens. Confira posições, caixa e cotação na corretora antes de decidir.')
        code='watch_entry' if eligible else 'watch_exit'
        for owner in owners:
            _,created=DecisionNotice.objects.get_or_create(user=watch.user,account=owner,asset=watch.asset,
                signal=code,session_date=timezone.localdate(now),defaults={'title':title,'detail':body,
                    'price':row['price'],'currency':row['currency']})
            created_notices+=int(created)
        # One Telegram message shared by the two selected owners, not one message per owner.
        if created_notices and Channel.TELEGRAM in notify.canais_ligados():
            notify.enfileirar_texto(NotificationKind.OPS,watch.asset.symbol+' · '+title,body,canais=[Channel.TELEGRAM])
        if created_notices:watch.last_notice_at=now
    watch.save(update_fields=['last_checked','last_status','detail','last_eligible','last_notice_at'])
    return created_notices
