"""Public app metadata and installation help; no financial data or offline storage."""
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET
from django.views.decorators.cache import never_cache

@require_GET
def manifest(request):
    return JsonResponse({
        'id':'/', 'name':'Portal de Investimentos', 'short_name':'Portal Invest',
        'description':'Suas carteiras, resultados e acompanhamento de investimentos no ID3.',
        'lang':'pt-BR', 'start_url':'/?origem=app', 'scope':'/',
        'display':'standalone', 'background_color':'#f7f8f4', 'theme_color':'#173b34',
        'icons':[
            {'src':'/static/id3_app/icon-192.png','sizes':'192x192','type':'image/png','purpose':'any'},
            {'src':'/static/id3_app/icon-512.png','sizes':'512x512','type':'image/png','purpose':'any'},
            {'src':'/static/id3_app/icon-maskable-512.png','sizes':'512x512','type':'image/png','purpose':'maskable'},
        ],
    },content_type='application/manifest+json')

@never_cache
@require_GET
def instructions(request):
    return render(request,'react_portal/install.html')
