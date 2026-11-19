import copy,io,zipfile
from decimal import Decimal as D
from django.test import TestCase
from django.utils import timezone
from django.core.exceptions import ValidationError
from .models import BrokerSnapshot
from .workbook_review import review,insights
from .workbook_export import export
from .workbook import empty_data,calculate

class ReviewTests(TestCase):
 def detailed(self):
  row={'symbol':'TEST3','quantity':'10','price':'12','average':'10','average_with_dividends':'10','invested':'100','value':'120','gain':'20','daily_gain':'5','dividends':'0','weight_percent':'100','return_percent':'20','daily_return_percent':'4.35','blocked':'0','available':'10','d0':'0'}
  return {'source':'BTG','currency':'BRL','scope':'Ações','capture_window':'09/11/2026 18:00','source_files':['print.png'],'rows':[row],'notes':[],'totals':{'invested':'100','value':'120','gain':'20','daily_gain':'5','dividends':'0','return_percent':'20','daily_return_percent':'4.35'}}
 def positions(self):
  return [{'symbol':'TEST3','currency':'BRL','quantity':D(10),'costBasis':D(100),'value':D(120)}]
 def test_dated_evidence_and_account_isolation(self):
  record=BrokerSnapshot.objects.create(account='ana',source_key='test',captured_at=timezone.now(),data=self.detailed())
  r=review('ana',self.positions());self.assertEqual(r['matched'],1);self.assertTrue(r['cost_ok']);self.assertEqual(r['issues'],[])
  self.assertIsNone(review('bruno',self.positions()))
  record.data['source']='Changed';self.assertRaises(ValidationError,record.save)
  p=self.positions();p[0]['costBasis']=D(101);r=review('ana',p);self.assertEqual(r['matched'],0);self.assertEqual(r['cost_difference'],D(-1))
 def test_summary_does_not_distribute_cost_difference(self):
  d=self.detailed();d['coverage']='summary';d['rows']=[];d['totals']={'invested':'156948.04','value':'168376.49','gain':'11428.45','daily_gain':'7408.85','dividends':'0','return_percent':'7.28','daily_return_percent':'4.60'}
  BrokerSnapshot.objects.create(account='bruno',source_key='summary',captured_at=timezone.now(),data=d)
  p=self.positions();p[0]['costBasis']=D('157043.24');before=copy.deepcopy(p)
  r=review('bruno',p);self.assertFalse(r['detailed']);self.assertFalse(r['cost_ok']);self.assertEqual(r['cost_difference'],D('-95.20'));self.assertEqual(r['issues'],[]);self.assertEqual(p,before)
 def test_currency_separation_partial_coverage_and_recovery(self):
  p=self.positions()+[{'symbol':'US','currency':'USD','quantity':D(1),'costBasis':D(200),'value':D(160)}]
  a=insights(p);self.assertEqual(a[0]['value'],120);self.assertEqual(a[1]['value'],160);self.assertEqual(a[1]['rows'][0]['recovery_percent'],25);self.assertEqual(a[1]['impact_one_percent'],D('1.6'))
  p.append({'symbol':'MISSING','currency':'BRL','quantity':D(1),'costBasis':D(10),'value':None})
  a=insights(p);self.assertIsNone(a[0]['top_weight']);self.assertIsNone(a[0]['rows'][0]['weight']);self.assertEqual(a[1]['top_weight'],100)
 def test_export_contains_immutable_reference_and_numeric_caches(self):
  p=self.positions();p[0].update(averagePrice=D(10),price=D(12),totalGain=D(20),totalReturn=D(20),quoteSource='Source',quoteAt=timezone.now())
  BrokerSnapshot.objects.create(account='ana',source_key='export',captured_at=timezone.now(),data=self.detailed())
  d=empty_data();payload={'owner':'Ana','generatedAt':timezone.now(),'data':d,'positions':p,'totals':[{'currency':'BRL','invested':D(100),'value':D(120),'gain':D(20),'returnPct':D(20),'quoted':1,'count':1}],'calculated':calculate(d,p,[]),'insights':insights(p),'brokerSnapshot':review('ana',p)}
  z=zipfile.ZipFile(io.BytesIO(export(payload)));self.assertIn(b'Conferencia BTG',z.read('xl/workbook.xml'))
  self.assertIn(b'<v>1.2</v>',z.read('xl/worksheets/sheet8.xml'))
  self.assertNotIn(b't="e"',z.read('xl/worksheets/sheet8.xml'));self.assertIn(b'120',z.read('xl/worksheets/sheet9.xml'))
