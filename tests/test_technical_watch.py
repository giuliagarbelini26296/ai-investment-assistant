import datetime as dt
from decimal import Decimal
import pytest
from django.utils import timezone
from django.contrib.auth import get_user_model
from apps.insights.models import TechnicalWatch,DecisionNotice,DecisionPreference
from apps.insights import technical_watch
from apps.alerts.models import Notification
from apps.portfolio.models import Asset,Lot,PositionBalance
from apps.managed_portfolios.models import Purchase

pytestmark=pytest.mark.django_db

@pytest.fixture
def watch(usuario):
    asset=Asset.objects.create(symbol='NVDC34',name='Nvidia BDR',market='B3',custody='NENHUMA',acompanhamento=True)
    return TechnicalWatch.objects.create(user=usuario,asset=asset)

@pytest.fixture
def connected(settings):
    settings.NOTIFICACOES={**settings.NOTIFICACOES,'TELEGRAM_BOT_TOKEN':'test','TELEGRAM_CHAT_ID':'test','FEED_TOKEN':'test'}


def row(eligible=True,blocks=None):
    return {'blocks':blocks or [],'price':Decimal('20.30'),'eligible':eligible,'quote_time':timezone.now(),
        'currency':'BRL','source':'brapi','warnings':['Atraso declarado pela fonte.'],'reasons':['Critérios verificados.']}


def test_two_accounts_single_telegram_no_quantity(watch,connected):
    assert technical_watch.record(watch.pk,row(),timezone.now())==2
    assert set(DecisionNotice.objects.values_list('account',flat=True))=={'ana','bruno'}
    n=Notification.objects.get();assert n.channel=='telegram'
    assert 'Quantidade não calculada' in n.body and 'Ana, Bruno' in n.body and '20.30' in n.body
    assert not Lot.objects.exists() and not PositionBalance.objects.exists() and not Purchase.objects.exists()
    assert not DecisionPreference.objects.exists()


def test_first_false_is_quiet(watch,connected):
    assert technical_watch.record(watch.pk,row(False),timezone.now())==0
    assert not Notification.objects.exists()


def test_unchanged_is_quiet(watch,connected):
    now=timezone.now();technical_watch.record(watch.pk,row(),now)
    assert technical_watch.record(watch.pk,row(),now+dt.timedelta(minutes=5))==0
    assert Notification.objects.count()==1


def test_gap_does_not_rearm(watch,connected):
    now=timezone.now();technical_watch.record(watch.pk,row(),now)
    technical_watch.record(watch.pk,row(False,['Cotação vencida']),now+dt.timedelta(minutes=5))
    watch.refresh_from_db();assert watch.last_eligible is True and watch.last_status=='dados_insuficientes'
    assert technical_watch.record(watch.pk,row(),now+dt.timedelta(hours=7))==0
    assert Notification.objects.count()==1


def test_cooldown_preserves_state_change(watch,connected):
    now=timezone.now();technical_watch.record(watch.pk,row(),now)
    technical_watch.record(watch.pk,row(False),now+dt.timedelta(minutes=5))
    watch.refresh_from_db();assert watch.last_eligible is False
    assert Notification.objects.count()==1


def test_later_exit_is_not_a_sale_order(watch,connected):
    now=timezone.now();technical_watch.record(watch.pk,row(),now)
    assert technical_watch.record(watch.pk,row(False),now+dt.timedelta(hours=7))==2
    assert Notification.objects.count()==2
    assert all('não prevê alta/queda nem envia ordens' in n.body for n in Notification.objects.all())


def test_daily_notice_dedup(watch,connected):
    now=timezone.now();technical_watch.record(watch.pk,row(),now)
    # No new entry record in the same date, even after rearming.
    watch.last_eligible=False;watch.last_notice_at=now-dt.timedelta(hours=7);watch.save()
    assert technical_watch.record(watch.pk,row(),now+dt.timedelta(minutes=1))==0
    assert Notification.objects.count()==1


def test_missing_quote_never_signals(watch,connected):
    data=row();data['price']=None
    assert technical_watch.record(watch.pk,data,timezone.now())==0
    assert not Notification.objects.exists()


def test_paused_and_old_runs_are_quiet(watch,connected):
    watch.active=False;watch.save()
    assert technical_watch.record(watch.pk,row(),timezone.now())==0
    watch.active=True;watch.last_checked=timezone.now()+dt.timedelta(days=1);watch.save()
    assert technical_watch.record(watch.pk,row(),timezone.now())==0


def test_no_telegram_does_not_make_external_notifications(watch,settings):
    settings.NOTIFICACOES={}
    technical_watch.record(watch.pk,row(),timezone.now())
    assert DecisionNotice.objects.count()==2 and not Notification.objects.exists()


def test_owner_scope(watch,connected):
    watch.bruno=False;watch.save()
    technical_watch.record(watch.pk,row(),timezone.now())
    assert list(DecisionNotice.objects.values_list('account',flat=True))==['ana']
    assert 'Bruno' not in Notification.objects.get().body


def test_toggle_is_authenticated_and_user_scoped(watch,cliente_logado):
    r=cliente_logado.post('/avaliacao/?carteira=ana',{'action':'watch_toggle','watch_id':watch.pk})
    assert r.status_code==302;watch.refresh_from_db();assert not watch.active
    other=get_user_model().objects.create_user('other','other@example.invalid','pw')
    watch.user=other;watch.save()
    cliente_logado.post('/avaliacao/?carteira=ana',{'action':'watch_toggle','watch_id':watch.pk})
    watch.refresh_from_db();assert not watch.active


def test_watch_is_visible_without_saved_budget(watch,cliente_logado):
    r=cliente_logado.get('/avaliacao/?carteira=bruno')
    assert r.status_code==200
    assert b'NVDC34' in r.content and b'Quantidades pendentes' in r.content
    assert not DecisionPreference.objects.exists()


def test_disabled_collection_does_not_read_watchlist(watch,settings,monkeypatch):
    settings.PORTAL={**settings.PORTAL,'MODO_DEMO':True}
    monkeypatch.setattr(technical_watch,'decision_row',lambda *a:pytest.fail('No collection in demo'))
    assert technical_watch.refresh_watches()==0


def test_no_new_auth_permissions():
    assert TechnicalWatch._meta.default_permissions==()
