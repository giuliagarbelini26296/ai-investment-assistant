import datetime as dt
from decimal import Decimal as D
import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone
from apps.portfolio import services
from apps.portfolio.models import Asset,Lot,LotRevision,PositionBalance
from apps.valuation import core
from apps.portfolio.quick_purchase import PurchaseForm,preview_data
from apps.react_portal.api import portfolio_payload

pytestmark=pytest.mark.django_db
ROWS=[('BBAS3','120','17.10','2051.89'),('BPAC11','80','49.90','3991.71'),
      ('ITUB4','400','38.10','15239.30'),('SBSP3','150','24.40','3659.52')]


def record(asset,q,pm,cost=None,key='new'):
    return services.registrar_saldo(asset,reference_date=timezone.localdate(),quantity=D(q),
        average_price=D(pm),invested_cost=D(cost) if cost is not None else None,source_key=key,evidence='Investido do extrato confirmado pela usuária.')


@pytest.mark.parametrize('symbol,q,pm,cost',ROWS)
def test_exact_cost_overrides_rounded_unit_average(symbol,q,pm,cost):
    asset=Asset.objects.create(symbol=symbol,name=symbol,market='B3',custody='BTG')
    old=record(asset,q,pm,key='old')
    balance=record(asset,q,pm,cost)
    pos=services.posicao_do_ativo(asset)
    assert pos.quantidade==D(q) and pos.investido==D(cost)
    assert pos.preco_medio==D(cost)/D(q)
    assert core.arredondar_moeda(pos.preco_medio)==D(pm)
    old.refresh_from_db();assert old.invested_cost is None
    assert balance.covered_lots==[] and Lot.objects.count()==0 and LotRevision.objects.count()==0


def test_totals_exact_and_avenue_unchanged():
    for symbol,q,pm,cost in ROWS:
        a=Asset.objects.create(symbol=symbol,name=symbol,market='B3',custody='BTG');record(a,q,pm,cost)
    us=Asset.objects.create(symbol='TTWO',name='TTWO',market='NASDAQ',custody='AVENUE')
    original=record(us,'100.47592','214.57',key='avenue')
    before=list(PositionBalance.objects.filter(asset=us).values())
    body=portfolio_payload({'history':'0'},'teste')
    totals={t['currency']:t['invested'] for t in body['totals']}
    assert totals=={'BRL':D('24942.42'),'USD':D('21559.1181544')}
    assert list(PositionBalance.objects.filter(asset=us).values())==before
    assert original.invested_cost is None


def test_subsequent_purchase_uses_exact_accumulated_cost(usuario):
    asset=Asset.objects.create(symbol='ITUB4',name='ITUB4',market='B3',custody='BTG')
    record(asset,'400','38.10','15239.30')
    form=PurchaseForm({'asset_choice':asset.pk,'trade_date':timezone.localdate(),'quantity':'100','unit_price':'40','submission_key':'b6f90103-7fd7-4385-9762-9c18eb3b83ac'})
    assert form.is_valid(),form.errors
    preview=preview_data(form)
    assert preview['before']['cost']==D('15239.30')
    assert preview['after']['cost']==D('19239.30')
    assert preview['after']['averagePrice']==D('38.4786')
    services.criar_lote(asset,usuario,trade_date=timezone.localdate(),quantity=D(100),unit_price=D(40))
    pos=services.posicao_do_ativo(asset)
    assert pos.investido==D('19239.30') and pos.quantidade==500


def test_idempotence_includes_exact_cost():
    asset=Asset.objects.create(symbol='BBAS3',name='BBAS3',market='B3',custody='BTG')
    first=record(asset,'120','17.10','2051.89')
    assert record(asset,'120','17.10','2051.89').pk==first.pk
    with pytest.raises(ValueError):record(asset,'120','17.10','2051.90')
    assert PositionBalance.objects.count()==1


@pytest.mark.parametrize('cost',['0','-1','99999','NaN'])
def test_invalid_or_inconsistent_cost_rejected(cost):
    asset=Asset.objects.create(symbol='BBAS3',name='BBAS3',market='B3',custody='BTG')
    with pytest.raises(ValidationError):record(asset,'120','17.10',cost)
    assert not PositionBalance.objects.exists()


def test_covered_purchase_and_revision_history_preserved(usuario):
    asset=Asset.objects.create(symbol='BBAS3',name='BBAS3',market='B3',custody='BTG')
    lot=services.criar_lote(asset,usuario,trade_date=timezone.localdate()-dt.timedelta(days=1),quantity=D(100),unit_price=D('16.15'))
    services.alterar_lote(lot,usuario,reason='histórico',unit_price=D('16.16'))
    before=(list(Lot.objects.values()),list(LotRevision.objects.values()))
    record(asset,'120','17.10','2051.89')
    assert services.posicao_do_ativo(asset).quantidade==120
    assert before==(list(Lot.objects.values()),list(LotRevision.objects.values()))


def test_old_balances_without_exact_cost_keep_previous_behavior():
    asset=Asset.objects.create(symbol='BBAS3',name='BBAS3',market='B3',custody='BTG')
    record(asset,'120','17.10')
    assert services.posicao_do_ativo(asset).investido==2052


def test_evolution_uses_exact_cost_only_from_balance_reference():
    from apps.marketdata.models import DailyBar
    from apps.ui.resumo import evolucao_patrimonio
    asset=Asset.objects.create(symbol='BBAS3',name='BBAS3',market='B3',custody='BTG')
    now=timezone.now()
    record(asset,'120','17.10','2051.89')
    DailyBar.objects.create(asset=asset,session_date=timezone.localdate(now),time_basis='sessao_local',
        close=D('17'),source='teste',collected_at=now)
    points=evolucao_patrimonio('BRL',dias=2,agora=now).pontos
    assert len(points)==1
    assert points[0].data==timezone.localdate(now)
    assert points[0].investido==D('2051.89') and points[0].valor==D('2040')
