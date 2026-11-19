"""General education only: outbound prompts come exclusively from this allowlist."""
import re
import unicodedata
import httpx
from django.conf import settings

ENDPOINT = 'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions'
MODEL = 'gemini-3.5-flash-lite'
TOPICS = (
    (r'preco medio|media ponderada', 'Explique o preço médio ponderado de ações e a fórmula, sem valores de exemplo.'),
    (r'dividendo|provento|\bjcp\b', 'Explique dividendos, JCP, data com, data ex e diferença entre direito anunciado e dinheiro recebido.'),
    (r'lucro|prejuizo|resultado', 'Explique lucro e prejuízo realizados e em aberto, resultado diário e acumulado, custos e impostos.'),
    (r'saldo|patrimonio|limite de negociacao|garantia', 'Explique a diferença entre saldo em dinheiro, valor das posições, patrimônio e limite de negociação.'),
    (r'ordem limitada|book|oferta', 'Explique ordens limitadas, ofertas de compra e venda e por que uma ordem pode não executar.'),
    (r'cambio|\bbdr\b|dolar', 'Explique a diferença entre ação americana e BDR e como o câmbio afeta a comparação de resultados.'),
    (r'diversifica|risco|\betf\b', 'Explique diversificação, concentração, ETFs e risco de perda, sem recomendar ativos ou operações.'),
)
SYSTEM = ('Você é um educador financeiro. Responda em português simples e em até quatro parágrafos. '
          'Explique apenas o tema geral recebido. Não conhece carteiras, pessoas, preços ou notícias atuais. '
          'Não invente dados atuais nem recomende operações, quantidades, preços de entrada ou promessas de lucro.')


def status():
    key = bool(getattr(settings, 'PORTAL_GEMINI_KEY', ''))
    enabled = bool(getattr(settings, 'PORTAL_GEMINI_ENABLED', False))
    free = bool(getattr(settings, 'PORTAL_GEMINI_FREE_TIER_CONFIRMED', False))
    available = key and enabled and free
    return {'available': available, 'enabled': enabled, 'keyConfigured': key,
            'generalOnly': True, 'model': MODEL, 'provider': 'gemini', 'providerLabel': 'Gemini · perguntas gerais',
            'dailyLimit': 20, 'pricingUrl': 'https://ai.google.dev/gemini-api/docs/pricing',
            'reason': None if available else 'Falta configurar a chave no servidor e confirmar o projeto no nível gratuito.',
            'billing': 'Uso sujeito às cotas do nível gratuito do projeto. Sem troca automática de provedor.',
            'privacy': 'Somente temas educativos predefinidos são enviados. Carteiras e conversas anteriores ficam no portal.'}


def topic_for(message):
    normalized = ''.join(c for c in unicodedata.normalize('NFD', message.lower()) if not unicodedata.combining(c))
    return next((prompt for pattern, prompt in TOPICS if re.search(pattern, normalized)), None)



def needs_portal(message):
    text = ''.join(c for c in unicodedata.normalize('NFD', message.lower()) if not unicodedata.combining(c))
    return bool(re.search(r'\b(minha|meu|minhas|meus|carteira|ana|bruno|extrato|posicao|posicoes)\b|\b[A-Za-z]{3,5}\d{1,2}\b|\b(nvidia|amd|take.two|oracle|checkpoint|ttwo|orcl|chkp|msft|amzn|googl|avgo)\b', text))


def portal_query(user, account, message, state):
    # Read and calculate locally. These objects are never passed to the Gemini client.
    from .ollama_assistant import facts_for, authoritative_answer, amount
    facts, evidence, portfolio, selected, snapshot = facts_for(user, account, message, state)
    answer = authoritative_answer(account, message, portfolio, selected, snapshot, evidence, facts)
    if len(selected) == 1:
        row = selected[0]
        answer += '\n\nPreço médio: ' + amount(row.get('averagePrice'), row['currency']) + '.'
    return {**evidence, 'answer': answer, 'mode': 'local', 'provider': 'portal',
            'aiGenerated': False, 'orders_enabled': False}


def query(user, account, message, state, client=None):
    # account, state, identity and the raw message never enter the remote request.
    from .assistant import AssistantUnavailable, reserve_turn
    if needs_portal(message):
        return portal_query(user, account, message, state)
    if not status()['available']:
        raise AssistantUnavailable(status()['reason'])
    topic = topic_for(message)
    result = {'rows': [], 'plans': [], 'mode': 'ai', 'provider': 'gemini', 'model': MODEL,
              'aiGenerated': False, 'generalOnly': True, 'orders_enabled': False}
    if topic is None:
        return {**result, 'answer': 'O Gemini está limitado a explicações gerais sobre preço médio, lucro e prejuízo, dividendos, saldo, ordens, câmbio e risco. Para valores da carteira, selecione “Por regras”.'}
    reserve_turn(user, limit=20)
    own_client = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(20, connect=5), follow_redirects=False)
    try:
        response = client.post(ENDPOINT, headers={'Authorization': 'Bearer ' + settings.PORTAL_GEMINI_KEY},
                               json={'model': MODEL, 'messages': [{'role': 'system', 'content': SYSTEM},
                                      {'role': 'user', 'content': topic}], 'max_tokens': 600, 'temperature': 0.2})
        if response.status_code == 429:
            raise AssistantUnavailable('A cota gratuita do Gemini foi atingida. Tente mais tarde ou use “Por regras”.')
        response.raise_for_status()
        answer = response.json()['choices'][0]['message']['content']
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError('empty response')
        return {**result, 'answer': answer.strip(), 'aiGenerated': True}
    except AssistantUnavailable:
        raise
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
        raise AssistantUnavailable('Não foi possível consultar o Gemini. Confira a configuração ou use “Por regras”.') from None
    finally:
        if own_client:
            client.close()
