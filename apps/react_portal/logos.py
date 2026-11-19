"""Logos dos ativos, buscadas pelo servidor e servidas do próprio domínio.

O navegador nunca consulta terceiros (não revela a carteira). Cada logo é baixada uma vez de
icons.brapi.dev, filtrada (SVG sem scripts, eventos, links externos ou entidades) e guardada.
Ausências também ficam guardadas por 7 dias para não repetir consultas.
"""
import datetime as dt
import re

import httpx
from django.http import HttpResponse, HttpResponseNotFound
from django.utils import timezone
from django.views.decorators.http import require_safe

from .models import AssetLogo

FONTE = 'https://icons.brapi.dev/icons/{}.svg'
SIMBOLO = re.compile(r'^[A-Z0-9]{2,12}$')
TAMANHO_MAX = 60_000
REVALIDAR_AUSENTE = dt.timedelta(days=7)
REVALIDAR_PRESENTE = dt.timedelta(days=30)
PROIBIDO = re.compile(
    r'<\s*(script|foreignObject|iframe|object|embed|use|image|a|animate|set|style)\b'
    r'|\bon[a-z]+\s*='
    r'|javascript:|data:|<!ENTITY|<!DOCTYPE'
    r'|(?:xlink:)?href\s*=\s*["\'](?!#)'
    r'|url\(\s*["\']?(?!#)',
    re.IGNORECASE)


def candidatos(symbol):
    """O próprio código e, para ativos dos EUA, os formatos de BDR da B3 (MSFT34, C1HK34)."""
    nomes = [symbol]
    if symbol.isalpha() and 2 <= len(symbol) <= 5:
        nomes += [symbol[:4] + '34', symbol[0] + '1' + symbol[1:3] + '34']
    return list(dict.fromkeys(nomes))


def svg_seguro(texto):
    texto = (texto or '').strip()
    if texto.startswith('<?xml'):
        texto = texto[texto.find('?>') + 2:].strip()
    if not texto.startswith('<svg') or not texto.endswith('</svg>') or len(texto) > TAMANHO_MAX:
        return None
    return None if PROIBIDO.search(texto) else texto


def buscar(symbol, client=None):
    client = client or httpx.Client(timeout=4, follow_redirects=False)
    for nome in candidatos(symbol):
        try:
            r = client.get(FONTE.format(nome))
        except httpx.HTTPError:
            return None  # falha de rede: não grava ausência, tenta de novo depois
        if r.status_code == 200 and r.headers.get('content-type', '').startswith('image/svg'):
            svg = svg_seguro(r.text)
            if svg:
                return {'svg': svg, 'found': True, 'source_symbol': nome}
    return {'svg': '', 'found': False, 'source_symbol': ''}


def logo_do_ativo(symbol, agora=None, client=None):
    agora = agora or timezone.now()
    atual = AssetLogo.objects.filter(symbol=symbol).first()
    if atual and agora - atual.fetched_at < (REVALIDAR_PRESENTE if atual.found else REVALIDAR_AUSENTE):
        return atual
    novo = buscar(symbol, client)
    if novo is None:
        return atual
    logo, _ = AssetLogo.objects.update_or_create(symbol=symbol, defaults={**novo, 'fetched_at': agora})
    return logo


@require_safe
def logo(request, symbol):
    if not request.user.is_authenticated:
        return HttpResponse(status=401)
    symbol = symbol.upper()
    if not SIMBOLO.match(symbol):
        return HttpResponseNotFound()
    item = logo_do_ativo(symbol)
    if not item or not item.found:
        return HttpResponseNotFound()
    resposta = HttpResponse(item.svg, content_type='image/svg+xml')
    resposta['Cache-Control'] = 'private, max-age=86400'
    resposta['Content-Security-Policy'] = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
    resposta['X-Content-Type-Options'] = 'nosniff'
    return resposta
