import copy
import json
from unittest.mock import patch
import httpx
from django.test import SimpleTestCase, override_settings
from apps.insights import assistant, gemini_assistant as gemini

@override_settings(PORTAL_AI_PROVIDER='gemini', PORTAL_GEMINI_KEY='test-secret', PORTAL_GEMINI_ENABLED=True,
                   PORTAL_GEMINI_FREE_TIER_CONFIRMED=True)
class GeminiGeneralTests(SimpleTestCase):
    def test_request_excludes_identity_portfolio_history_and_raw_message(self):
        calls=[]
        def handler(request):
            calls.append(request)
            return httpx.Response(200,json={'choices':[{'message':{'content':'Explicação geral.'}}]})
        state={'history':[{'role':'user','content':'saldo privado 123456'}], 'scenario':{'budget_brl':'999'}}
        before=copy.deepcopy(state)
        with httpx.Client(transport=httpx.MockTransport(handler)) as client, patch.object(assistant,'reserve_turn') as reserve:
            result=assistant.ai_query(object(),'bruno','Explique preço médio. Dado privado 888 e contato pessoal@example.org',state,client=client)
        body=calls[0].content.decode()
        for secret in ['bruno','888','pessoal@example.org','123456','999','test-secret']:
            self.assertNotIn(secret,body)
        self.assertEqual(calls[0].url,gemini.ENDPOINT)
        self.assertEqual(len(json.loads(body)['messages']),2)
        reserve.assert_called_once()
        self.assertEqual(before,state)
        self.assertTrue(result['generalOnly'])
        self.assertEqual(result['rows'],[])

    def test_unsupported_question_never_calls_api(self):
        with patch.object(gemini.httpx,'Client',side_effect=AssertionError('network')), patch.object(assistant,'reserve_turn') as reserve:
            result=gemini.query(None,'ana','Explique um assunto não cadastrado',{})
        reserve.assert_not_called()
        self.assertFalse(result['aiGenerated'])

    def test_quota_does_not_retry_or_fallback(self):
        calls=[]
        def handler(request):
            calls.append(request)
            return httpx.Response(429,json={})
        with httpx.Client(transport=httpx.MockTransport(handler)) as client, patch.object(assistant,'reserve_turn'):
            with self.assertRaisesMessage(assistant.AssistantUnavailable,'cota gratuita'):
                gemini.query(None,'ana','Explique lucro',{},client=client)
        self.assertEqual(len(calls),1)

    def test_missing_free_tier_confirmation_disables_provider(self):
        with override_settings(PORTAL_GEMINI_FREE_TIER_CONFIRMED=False), patch.object(gemini.httpx,'Client',side_effect=AssertionError('network')):
            self.assertFalse(assistant.availability())
            with self.assertRaises(assistant.AssistantUnavailable):
                gemini.query(None,'ana','Explique lucro',{})

    def test_model_and_status(self):
        self.assertTrue(assistant.availability())
        self.assertEqual(assistant.active_model(),'gemini-3.5-flash-lite')
        self.assertNotIn('test-secret',json.dumps(gemini.status()))

    def test_portfolio_question_is_handled_locally_without_remote_call(self):
        with patch.object(gemini, 'portal_query', return_value={'mode':'local','provider':'portal'}) as local, patch.object(gemini.httpx,'Client',side_effect=AssertionError('network forbidden')), patch.object(assistant,'reserve_turn') as reserve:
            result=gemini.query(None,'ana','Qual o meu preço médio da NVDC34?',{})
        local.assert_called_once()
        reserve.assert_not_called()
        self.assertEqual(result['provider'],'portal')
