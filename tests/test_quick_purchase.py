import json
import datetime as dt
import uuid
from decimal import Decimal as D

import pytest
from django.core import signing
from django.utils import timezone

from apps.portfolio import services
from apps.portfolio.models import Asset, Lot, LotRevision, PositionBalance, PurchaseReceipt
from apps.portfolio.quick_purchase import SALT

PAGE = '/compras/registrar/'
PREVIEW = '/compras/previa.json'


@pytest.fixture
def bank(db):
    asset = Asset.objects.create(symbol='BBAS3', name='Banco do Brasil', market='B3', custody='BTG')
    services.registrar_saldo(asset, reference_date=timezone.localdate(), quantity=D(200),
        average_price=D('16.30'), source_key='fixture', evidence='Saldo de teste.')
    return asset


def purchase(asset, **changes):
    data = {'asset_choice':str(asset.pk), 'trade_date':timezone.localdate().isoformat(),
            'quantity':'100', 'unit_price':'16,50', 'submission_key':str(uuid.uuid4()), 'not_included':'yes'}
    data.update(changes)
    return data


def review(client, data):
    response = client.post(PAGE, {**data, 'action':'review'})
    assert response.status_code == 200
    assert not response.context['form'].errors, response.context['form'].errors
    assert response.context['preview']
    return response.context['review_signature']


def test_requires_authentication_and_read_only_preview(client, bank):
    assert client.get(PAGE).status_code == 302
    assert client.get(PREVIEW, purchase(bank)).status_code == 401
    assert Lot.objects.count() == 0


def test_locale_preview_calculates_from_reconciled_cost_without_writes(cliente_logado, bank):
    before = list(PositionBalance.objects.values())
    response = cliente_logado.get(PREVIEW, purchase(bank))
    assert response.status_code == 200, response.content
    body = response.json()
    assert body['currency']=='BRL' and D(body['before']['cost']) == D('3260')
    assert D(body['purchase']['total']) == D('1650')
    assert D(body['after']['quantity']) == D('300')
    assert D(body['after']['cost']) == D('4910')
    assert D(body['after']['averagePrice']) == D(4910)/D(300)
    assert Lot.objects.count()==0 and PurchaseReceipt.objects.count()==0
    assert list(PositionBalance.objects.values()) == before
    assert 'no-store' in response['Cache-Control']


def test_confirm_creates_exactly_one_ledger_lot_and_retry_is_idempotent(cliente_logado, bank, usuario):
    data = purchase(bank)
    signature = review(cliente_logado, data)
    assert Lot.objects.count()==0
    confirm = {**data, 'action':'confirm','review_signature':signature}
    response = cliente_logado.post(PAGE, confirm)
    assert response.status_code==302 and response['Location']=='/'
    lot = Lot.objects.get(asset=bank)
    assert lot.quantity==D(100) and lot.unit_price==D('16.5') and lot.created_by==usuario
    assert lot.trade_date==timezone.localdate()
    assert PurchaseReceipt.objects.get().lot_id==lot.pk
    assert cliente_logado.post(PAGE, confirm).status_code==302
    assert Lot.objects.count()==1 and PurchaseReceipt.objects.count()==1
    assert PositionBalance.objects.count()==1 and LotRevision.objects.count()==0
    pos = services.posicao_do_ativo(Asset.objects.get(pk=bank.pk))
    assert pos.quantidade==D(300) and pos.investido==D(4910)
    assert pos.preco_medio==D(4910)/D(300)
    home = cliente_logado.get('/')
    import re
    notices=json.loads(re.search(r'id="id3-notices"[^>]*>(.*?)</script>',home.content.decode()).group(1))
    assert any('duplicação' in n['text'] for n in notices)


def test_confirm_requires_review_and_unchanged_payload(cliente_logado, bank):
    data = purchase(bank)
    assert cliente_logado.post(PAGE, {**data,'action':'confirm'}).status_code==200
    assert Lot.objects.count()==0
    signature=review(cliente_logado,data)
    response=cliente_logado.post(PAGE,{**data,'quantity':'200','action':'confirm','review_signature':signature})
    assert response.status_code==200 and Lot.objects.count()==0


def test_stale_position_requires_new_review(cliente_logado, bank, usuario):
    data=purchase(bank);signature=review(cliente_logado,data)
    services.criar_lote(bank,usuario,trade_date=timezone.localdate(),quantity=D(10),unit_price=D(15))
    response=cliente_logado.post(PAGE,{**data,'action':'confirm','review_signature':signature})
    assert response.status_code==200 and Lot.objects.count()==1
    assert 'posição mudou' in response.content.decode()
    assert response.context['preview']['before']['quantity']==D(210)
    new_sig=response.context['review_signature']
    assert cliente_logado.post(PAGE,{**data,'action':'confirm','review_signature':new_sig}).status_code==302
    assert Lot.objects.count()==2


def test_same_token_with_different_values_never_adds_another_purchase(cliente_logado, bank):
    data=purchase(bank);sig=review(cliente_logado,data)
    cliente_logado.post(PAGE,{**data,'action':'confirm','review_signature':sig})
    altered={**data,'quantity':'300'};new_sig=review(cliente_logado,altered)
    response=cliente_logado.post(PAGE,{**altered,'action':'confirm','review_signature':new_sig})
    assert response.status_code==200 and Lot.objects.count()==1
    assert response.context['form'].non_field_errors()


@pytest.mark.parametrize('fields', [
    {'quantity':'0'},{'quantity':'-1'},{'quantity':'1,5'},{'quantity':'NaN'},
    {'unit_price':'0'},{'unit_price':'-1'},{'unit_price':'1e3'},
    {'unit_price':'1,2,3'},{'unit_price':'1,16%'},
    {'trade_date':(timezone.localdate()+dt.timedelta(days=1)).isoformat()},
    {'asset_choice':'9999999'},
])
def test_invalid_values_do_not_create_trades(cliente_logado, bank, fields):
    response=cliente_logado.post(PAGE,purchase(bank,**fields))
    assert response.status_code==200 and response.context['form'].errors
    assert Lot.objects.count()==0


def test_us_fractional_first_purchase_and_currency_kept_separate(cliente_logado, db):
    asset=Asset.objects.create(symbol='TEST',name='Teste EUA',market='NASDAQ',custody='AVENUE')
    data=purchase(asset,quantity='0.125',unit_price='214.57')
    body=cliente_logado.get(PREVIEW,data).json()
    assert body['currency']=='USD'
    assert D(body['after']['quantity'])==D('.125')
    assert D(body['after']['averagePrice'])==D('214.57')
    sig=review(cliente_logado,data)
    assert cliente_logado.post(PAGE,{**data,'action':'confirm','review_signature':sig}).status_code==302
    assert Lot.objects.get().quantity==D('.125')


def test_round_trip_of_repeating_pm_does_not_change_accumulated_cost(cliente_logado, db, usuario):
    asset=Asset.objects.create(symbol='TEST',name='Teste',market='B3',custody='BTG')
    services.criar_lote(asset,usuario,trade_date=timezone.localdate(),quantity=D(2),unit_price=D(33))
    services.criar_lote(asset,usuario,trade_date=timezone.localdate(),quantity=D(1),unit_price=D(34))
    body=cliente_logado.get(PREVIEW,purchase(asset,quantity='1',unit_price='50')).json()
    assert D(body['before']['cost'])==D(100)
    assert D(body['after']['cost'])==D(150)
    assert D(body['after']['averagePrice'])==D('37.5')


def test_new_asset_flow_returns_to_purchase(cliente_logado):
    response=cliente_logado.post('/ativos/novo/?compra=1',
        {'symbol':'TEST','name':'Teste','market':'NASDAQ','custody':'AVENUE','is_active':'on'})
    asset=Asset.objects.get(symbol='TEST')
    assert response.status_code==302 and response['Location']==f'{PAGE}?ativo={asset.pk}'
    page=cliente_logado.get(response['Location'])
    assert str(page.context['form']['asset_choice'].value())==str(asset.pk)


def test_correcting_review_and_cancel_leave_ledger_unchanged(cliente_logado, bank):
    data=purchase(bank)
    review(cliente_logado,data)
    response=cliente_logado.post(PAGE,{**data,'action':'edit'})
    assert response.context['preview'] is None
    assert Lot.objects.count()==0


def test_schema_adds_no_user_permissions(db):
    from django.contrib.auth.models import Permission
    assert PurchaseReceipt._meta.default_permissions==()
    assert not Permission.objects.filter(content_type__app_label='portfolio',content_type__model='purchasereceipt').exists()


def test_confirmation_requires_csrf(usuario, bank):
    from django.test import Client
    client=Client(enforce_csrf_checks=True);client.force_login(usuario)
    assert client.post(PAGE,purchase(bank)).status_code==403


def test_older_purchase_is_not_added_on_top_of_later_reconciled_balance(cliente_logado, bank):
    data=purchase(bank,trade_date=(timezone.localdate()-dt.timedelta(days=1)).isoformat())
    response=cliente_logado.post(PAGE,data)
    assert response.context['form'].errors.get('trade_date')
    assert Lot.objects.count()==0


def test_explicit_not_included_acknowledgement_is_required(cliente_logado, bank):
    data=purchase(bank);sig=review(cliente_logado,data)
    data.pop('not_included')
    response=cliente_logado.post(PAGE,{**data,'action':'confirm','review_signature':sig})
    assert response.status_code==200 and response.context['form'].non_field_errors()
    assert Lot.objects.count()==0


def test_cancelled_receipt_replay_does_not_recreate_purchase(cliente_logado, bank, usuario):
    data=purchase(bank);sig=review(cliente_logado,data)
    cliente_logado.post(PAGE,{**data,'action':'confirm','review_signature':sig})
    lot=Lot.objects.get();services.cancelar_lote(lot,usuario)
    response=cliente_logado.post(PAGE,{**data,'action':'confirm','review_signature':sig})
    assert response.status_code==302 and Lot.objects.count()==1
    assert Lot.objects.get().is_cancelled
    assert services.posicao_do_ativo(Asset.objects.get(pk=bank.pk)).quantidade==D(200)


@pytest.mark.django_db(transaction=True)
def test_parallel_retries_create_a_single_purchase(usuario, bank):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from django.db import connection, close_old_connections
    from apps.portfolio.quick_purchase import PurchaseForm, record_purchase, signed_review, preview_data
    if connection.vendor != 'postgresql':
        pytest.skip('Requires PostgreSQL row locks')
    data=purchase(bank);form=PurchaseForm(data);assert form.is_valid()
    reviewed=signing.loads(signed_review(form,usuario,preview_data(form)),salt=SALT)
    barrier=Barrier(2)
    def run():
        close_old_connections()
        try:
            local=PurchaseForm(data);assert local.is_valid()
            barrier.wait(timeout=10)
            lot,created=record_purchase(local,usuario,reviewed)
            return created
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as executor:
        result=list(executor.map(lambda _:run(),range(2)))
    assert sorted(result)==[False,True]
    assert Lot.objects.count()==1 and PurchaseReceipt.objects.count()==1
