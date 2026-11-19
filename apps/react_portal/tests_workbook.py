import json,uuid,zipfile,io
from decimal import Decimal as D
from django.test import TestCase,Client
from django.contrib.auth import get_user_model
from apps.portfolio.models import Asset,Lot
from .models import WorkbookState,WorkbookRevision
from .workbook import empty_data,normalize,calculate

class WorkbookTests(TestCase):
 def setUp(self):
  self.user=get_user_model().objects.create_user(username='qa',password='local-only-testing')
  self.a=Asset.objects.create(symbol='ITUB4',name='Itaú',market='B3')
  self.us=Asset.objects.create(symbol='AMZN',name='Amazon',market='NASDAQ')
  self.client.force_login(self.user)
 def post(self,data,version=0,account='ana',action='save'):
  return self.client.post('/api/v1/workbook/?account='+account,json.dumps({'data':data,'version':version,'action':action}),content_type='application/json')
 def test_auth_and_csrf(self):
  anonymous=Client();self.assertEqual(anonymous.get('/api/v1/workbook/').status_code,401)
  self.assertEqual(anonymous.get('/api/v1/assistant/status/').status_code,401)
  protected=Client(enforce_csrf_checks=True);protected.force_login(self.user)
  self.assertEqual(protected.post('/api/v1/workbook/',json.dumps({}),content_type='application/json').status_code,403)
 def test_no_write_on_get_preview_and_separate_saved_accounts(self):
  self.assertEqual(self.client.get('/api/v1/workbook/').status_code,200);self.assertFalse(WorkbookState.objects.exists())
  d=empty_data();d['notes']='Ana =HYPERLINK("bad")'
  self.assertEqual(self.post(d,action='preview').status_code,200);self.assertFalse(WorkbookState.objects.exists())
  self.assertEqual(self.post(d).status_code,200);self.assertEqual(WorkbookRevision.objects.count(),1)
  self.assertEqual(self.post(d).status_code,409);self.assertEqual(WorkbookRevision.objects.count(),1)
  bruno=self.client.get('/api/v1/workbook/?account=bruno').json();self.assertEqual(bruno['data']['notes'],'')
  self.assertEqual(Lot.objects.count(),0)
 def test_decimal_validation_and_fraction(self):
  for x in ['NaN','Infinity','-1','100000001','1.001']:
   d=empty_data();d['settings']['cash_brl']=x;self.assertRaises(ValueError,normalize,d)
  d=empty_data();d['scenarios']=[{'id':str(uuid.uuid4()),'symbol':'ITUB4','quantity':'1.5','price':'10','fees':'0'}]
  self.assertRaises(ValueError,normalize,d);d['scenarios'][0]['symbol']='AMZN';self.assertEqual(normalize(d)['scenarios'][0]['quantity'],'1.5')
 def test_net_income_and_sequential_average(self):
  d=empty_data();d['dividends']=[{'id':str(uuid.uuid4()),'date':'2026-11-09','symbol':'ITUB4','currency':'BRL','kind':'jcp','status':'received','gross':'10','tax':None,'reference':''},{'id':str(uuid.uuid4()),'date':'2026-11-09','symbol':'AMZN','currency':'USD','kind':'dividend','status':'announced','gross':'20','tax':'0','reference':''}]
  d['scenarios']=[{'id':str(uuid.uuid4()),'symbol':'ITUB4','currency':'BRL','quantity':'10','price':'20','fees':'5'},{'id':str(uuid.uuid4()),'symbol':'ITUB4','currency':'BRL','quantity':'10','price':'30','fees':'0'}]
  c=calculate(d,[{'symbol':'ITUB4','quantity':'10','costBasis':'100'}],[]);self.assertEqual(c['received']['BRL'],0);self.assertEqual(c['pending_dividends'],1)
  self.assertEqual(c['scenarios'][0]['new_average'],D('15.25'));self.assertEqual(c['scenarios'][1]['previous_cost'],305)
  d['dividends'][0]['tax']='1.50';self.assertEqual(calculate(d,[],[])['received']['BRL'],D('8.50'))
 def test_budget_not_reused(self):
  d=empty_data();d['settings']['cash_brl']='100';d['budgets']={'ITUB4':'70','AMZN':'70'}
  watch=[{'symbol':'ITUB4','currency':'BRL','price':D(10),'blocks':[]},{'symbol':'AMZN','currency':'BRL','price':D(10),'blocks':[]}]
  c=calculate(d,[],watch);self.assertTrue(all(r['quantity'] is None for r in c['radar']))
  d['budgets']['AMZN']='30';c=calculate(d,[],watch);self.assertEqual(c['radar'][0]['quantity'],7)
 def test_export_and_literal_text(self):
  d=empty_data();d['notes']='=HYPERLINK("https://invalid","test")';self.assertEqual(self.post(d).status_code,200)
  r=self.client.get('/api/v1/workbook/?account=ana&download=1');self.assertEqual(r.status_code,200)
  z=zipfile.ZipFile(io.BytesIO(r.content));self.assertEqual(len([x for x in z.namelist() if x.startswith('xl/worksheets/sheet') and x.endswith('.xml')]),6)
  self.assertIn(b'HYPERLINK',z.read('xl/sharedStrings.xml'));self.assertNotIn(b'<f>',z.read('xl/worksheets/sheet5.xml'))
  self.assertIn('no-store',r['Cache-Control'])

 def test_research_and_removed_export_tabs_preserve_saved_inputs(self):
  d=empty_data();d['settings']['monthly_expenses']='1500';d['settings']['cash_usd']='25'
  self.assertEqual(self.post(d).status_code,200)
  for account in ('ana','bruno'):
   p=self.client.get('/api/v1/workbook/?account='+account).json()
   self.assertEqual([r['symbol'] for r in p['researchRadar']['entries']],['GOOGL','CRM','AMZN'])
  self.assertEqual(WorkbookState.objects.get(account='ana').data['settings']['monthly_expenses'],'1500')
  z=zipfile.ZipFile(io.BytesIO(self.client.get('/api/v1/workbook/?account=ana&download=1').content))
  from xml.etree import ElementTree as ET
  sheets=ET.fromstring(z.read('xl/workbook.xml')).find('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheets')
  names=[x.attrib['name'] for x in sheets]
  self.assertNotIn('Proventos',names);self.assertNotIn('Planejamento',names);self.assertIn('Radar',names)
  shared=z.read('xl/sharedStrings.xml')
  self.assertIn(b'Salesforce',shared);self.assertIn(b'GOOGL',shared);self.assertNotIn(b'Microsoft',shared)
