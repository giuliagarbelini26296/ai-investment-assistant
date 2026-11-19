import uuid
from datetime import timedelta
from decimal import Decimal as D
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone
from .models import Asset, Sale
from . import services
from .sales import preview,record_sale
from apps.managed_portfolios.models import Portfolio
from apps.managed_portfolios import services as managed

class SaleTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('seller',password='test-password')
        self.asset=Asset.objects.create(symbol='A1MD34',name='AMD',market='B3',custody='BTG')
        self.date=timezone.localdate()
        services.registrar_saldo(self.asset,reference_date=self.date,quantity=D(100),average_price=D('300.091'),invested_cost=D('30009.10'),source_key='initial',evidence='test')
    def sell(self,account='ana',quantity='40',price='373.3563',key=None,fees=None):
        p=preview(account,self.asset,quantity=D(quantity),unit_price=D(price),trade_date=self.date,fees=fees)
        return record_sale(account,self.asset,self.user,quantity=D(quantity),unit_price=D(price),trade_date=self.date,fees=fees,submission_key=key or uuid.uuid4(),reviewed_before=[str(p['before_quantity']),str(p['before_cost'])])
    def test_partial_exact_cost_and_retry(self):
        key=uuid.uuid4();s,_=self.sell(key=key)
        pos=services.posicao_do_ativo(self.asset)
        self.assertEqual(pos.quantidade,D(60));self.assertEqual(pos.investido,D('18005.46'))
        self.assertEqual(s.gross_gain,D('2930.612'));self.assertIsNone(s.net_gain)
        repeated,created=record_sale('ana',self.asset,self.user,quantity=D(40),unit_price=D('373.3563'),trade_date=self.date,submission_key=key,reviewed_before=['100','30009.10000000'])
        self.assertFalse(created);self.assertEqual(repeated.pk,s.pk);self.assertEqual(Sale.objects.count(),1)
    def test_total_sale_then_repurchase(self):
        self.sell(quantity='100');self.assertIsNone(services.posicao_do_ativo(self.asset))
        services.criar_lote(self.asset,self.user,trade_date=self.date,quantity=D(2),unit_price=D(350))
        pos=services.posicao_do_ativo(self.asset)
        self.assertEqual(pos.quantidade,D(2));self.assertEqual(pos.investido,D(700))
    def test_stale_oversell_fractional_and_future(self):
        for qty,date in [(D(101),self.date),(D('1.5'),self.date),(D(1),self.date+timedelta(days=1))]:
            with self.assertRaises(ValidationError):preview('ana',self.asset,quantity=qty,unit_price=D(300),trade_date=date)
        with self.assertRaises(ValidationError):record_sale('ana',self.asset,self.user,quantity=D(1),unit_price=D(300),trade_date=self.date,submission_key=uuid.uuid4(),reviewed_before=['wrong'])
        self.assertEqual(Sale.objects.count(),0)
    def test_bruno_isolation_full_close(self):
        bruno,_=Portfolio.objects.get_or_create(slug='bruno',defaults={'name':'Bruno'})
        managed.reconcile(bruno,self.asset,quantity=D(10),reported_average=D(200),invested_cost=D(2000),currency='BRL',reference_date=self.date,source_key='initial',evidence='test')
        sale,_=self.sell(account='bruno',quantity='10',price='250',fees=D(3))
        self.assertEqual(sale.net_gain,D(497));self.assertIsNone(managed.position(bruno,self.asset)[0])
        self.assertEqual(services.posicao_do_ativo(self.asset).quantidade,D(100))
    def test_authenticated_review_confirmation(self):
        self.assertEqual(self.client.get('/vendas/registrar/').status_code,302)
        self.client.force_login(self.user)
        key=uuid.uuid4();data={'asset':self.asset.pk,'trade_date':str(self.date),'quantity':'40','unit_price':'373,3563','fees':'','submission_key':str(key),'action':'review'}
        response=self.client.post('/vendas/registrar/?carteira=ana',data)
        self.assertEqual(response.status_code,200);self.assertContains(response,'Conferir A1MD34');self.assertEqual(Sale.objects.count(),0)
        token=response.context['review_signature'];self.assertTrue(token)
        data.update(action='confirm',review_signature=token,ack='yes')
        self.assertEqual(self.client.post('/vendas/registrar/?carteira=ana',data).status_code,302)
        self.assertContains(self.client.get('/vendas/registrar/?carteira=ana'),'A1MD34')
        self.assertEqual(Sale.objects.count(),1)

    def test_evidence_import_without_user(self):
        sale,_=record_sale('ana',self.asset,None,quantity=D(40),unit_price=D('373.3563'),trade_date=self.date,submission_key=uuid.uuid4(),reviewed_before=['100.0000000000','30009.10000000'],evidence='Comprovante do usuário')
        self.assertIsNone(sale.created_by)
