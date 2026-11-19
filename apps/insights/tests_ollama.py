import copy,json
from decimal import Decimal as D
from types import SimpleNamespace
from unittest.mock import patch,MagicMock
import httpx
from django.test import TestCase,override_settings,Client
from django.contrib.auth import get_user_model
from django.utils import timezone
from apps.insights import assistant,ollama_assistant as local
from apps.insights.models import AssistantUsage
from apps.portfolio.models import Lot

class FakeClient:
 def __init__(self,text='Um resultado em aberto só é realizado quando a posição é encerrada.',failure=False):self.calls=[];self.text=text;self.failure=failure
 def post(self,url,json):
  self.calls.append((url,json))
  if self.failure:raise httpx.ReadTimeout('local model timeout')
  return SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'done_reason':'stop','message':{'content':__import__('json').dumps({'explanation':self.text})}})

@override_settings(PORTAL_AI_PROVIDER='ollama',PORTAL_OLLAMA_ENABLED=True,PORTAL_OLLAMA_URL='http://host.docker.internal:11434',PORTAL_OLLAMA_MODEL='qwen3.5:4b',PORTAL_AI_ENABLED=False,PORTAL_AI_KEY='private-never-sent',PORTAL_OLLAMA_DAILY_TURNS=60)
class OllamaTests(TestCase):
 def setUp(self):
  self.user=get_user_model().objects.create_user(username='local-qa',password=None)
  now=timezone.now();self.row={'symbol':'NVDC34','currency':'BRL','quantity':D(700),'costBasis':D('14468'),'price':D('20.46'),'value':D('14322'),'totalGain':D('-146'),'quoteSource':'confirmed-source','quoteAt':now,'quoteState':'Fonte com horário declarado'}
  self.p={'meta':{'ownerLabel':'Ana','generatedAt':now},'positions':[self.row],'totals':[{'currency':'BRL','invested':D('14468'),'value':D('14322'),'gain':D('-146'),'day':D('-146'),'quoted':1,'count':1}]}
  self.facts={'carteira':'Ana','posicoes':[self.row],'totais_por_moeda':self.p['totals'],'proventos_registrados':{'recebidos':{'BRL':D(0),'USD':D(0)}}}
 def invoke(self,client,state=None,account='ana',message='Estou perdendo com Nvidia?'):
  state=state if state is not None else {}
  with patch.object(local,'status',return_value={'available':True}),patch.object(local,'facts_for',return_value=(self.facts,{'rows':[],'plans':[]},self.p,[self.row],None)):
   return assistant.ai_query(self.user,account,message,state,client=client)
 def test_only_local_endpoint_and_no_paid_fallback(self):
  c=FakeClient();r=self.invoke(c)
  self.assertEqual(r['provider'],'ollama');self.assertTrue(r['aiGenerated']);self.assertIn('prejuízo em aberto',r['answer']);self.assertIn('-R$ 146,00',r['answer'])
  self.assertEqual(c.calls[0][0],'http://host.docker.internal:11434/api/chat')
  self.assertNotIn('private-never-sent',json.dumps(c.calls));self.assertFalse(c.calls[0][1]['stream']);self.assertFalse(Lot.objects.exists())
  with self.assertRaises(assistant.AssistantUnavailable):assistant._openai_query(self.user,'ana','test',{},client=c)
  self.assertEqual(len(c.calls),1)
 def test_numeric_or_order_claim_is_rejected(self):
  for text in ['Você ganhou R$ 1.000,00.','Compre agora.','Vai subir amanhã.','Lucro realizado (proventos recebidos).','O resultado realizado permanece zero.','Isso não significa que você esteja perdendo dinheiro no momento.']:
   r=self.invoke(FakeClient(text));self.assertFalse(r['aiGenerated']);self.assertEqual(r['mode'],'local');self.assertIn('prejuízo em aberto',r['answer']);self.assertNotIn('1.000',r['answer'])
 def test_invalid_endpoint_and_cloud_tag(self):
  for url in ['https://api.openai.com','http://evil.example:11434','http://localhost:11434/forward','http://localhost:11434?remote=true']:
   with override_settings(PORTAL_OLLAMA_URL=url):self.assertRaises(ValueError,local.endpoint)
  with override_settings(PORTAL_OLLAMA_MODEL='something:cloud'):self.assertFalse(local.status()['available'])
 def test_offline_never_calls_paid_or_changes_state(self):
  c=FakeClient(failure=True);s={'scenario':{'budget_brl':'50'}};before=copy.deepcopy(s)
  self.assertRaises(assistant.AssistantUnavailable,self.invoke,c,s)
  self.assertEqual(before,s);self.assertEqual(len(c.calls),1);self.assertNotIn('openai.com',c.calls[0][0])
 def test_disabled_does_not_open_connection(self):
  with override_settings(PORTAL_OLLAMA_ENABLED=False),patch.object(local.httpx,'Client',side_effect=AssertionError('network forbidden')):
   self.assertRaises(assistant.AssistantUnavailable,assistant.ai_query,self.user,'ana','test',{})
 def test_quota_and_busy_do_not_generate(self):
  AssistantUsage.objects.create(user=self.user,date=timezone.localdate(),turns=60);c=FakeClient();self.assertRaises(assistant.AssistantUnavailable,self.invoke,c);self.assertEqual(c.calls,[])
 def test_invalid_model_output(self):
  for value in ([],{}, {'message':[]},{'message':{'content':'not-json'}},{'message':{'content':'{"explanation":null}'}}):self.assertIsNone(local.safe_explanation(value))
 def test_source_status_and_auth(self):
  with patch.object(local,'status',return_value={'enabled':True,'available':True,'reason':None}):
   self.assertTrue(assistant.availability());self.assertEqual(assistant.sources_status()['ai_provider'],'ollama')
   c=Client();self.assertEqual(c.get('/api/v1/assistant/status/').status_code,401);c.force_login(self.user);p=c.get('/api/v1/assistant/status/').json();self.assertEqual(p['model'],'qwen3.5:4b');self.assertFalse(p['paidEnabled'])
 def test_model_health_excludes_remote_models(self):
  for remote in (False,True):
   local._health.clear();model={'name':'qwen3.5:4b','size':4000000000}
   if remote:model['remote_host']='https://ollama.com'
   response=SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'models':[model]});c=MagicMock();c.__enter__.return_value.get.return_value=response
   with patch.object(local.httpx,'Client',return_value=c):self.assertEqual(local.status()['available'],not remote)
 def test_educational_sale_definition_is_allowed(self):
  body={'message':{'content':json.dumps({'explanation':'O resultado só é realizado na venda da posição; os proventos são recebimentos separados.'})}}
  self.assertIsNotNone(local.safe_explanation(body))
 def test_income_unknown_is_not_broker_zero(self):
  text=local.authoritative_answer('ana','Quais proventos recebi?',self.p,[self.row],None,{},self.facts)
  self.assertIn('não confirma ausência',text)
 def test_broker_difference_remains_visible(self):
  snapshot={'data':{'capture_window':'09/11/2026 18:06','currency':'BRL','totals':{'invested':'156948.04','value':'168376.49','gain':'11428.45','daily_gain':'7408.85'}},'cost_ok':False,'cost_difference':D('-95.20')}
  text=local.authoritative_answer('bruno','Meu extrato BTG',self.p,[self.row],snapshot,{},self.facts);self.assertIn('-R$ 95,20',text);self.assertIn('falta conferir',text)
