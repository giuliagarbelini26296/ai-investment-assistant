import json
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from .assistant import local_query,ai_query,availability,AssistantUnavailable


@login_required
@never_cache
@require_POST
def query(request):
    account=request.GET.get('carteira','ana')
    if account not in ('ana','bruno'):return JsonResponse({'error':'Carteira inválida.'},status=400)
    if len(request.body)>12000:return JsonResponse({'error':'Mensagem muito longa.'},status=400)
    try:
        data=json.loads(request.body)
        message=data.get('message','')
        if not isinstance(message,str) or not message.strip() or len(message)>2000:raise ValueError()
        mode=data.get('mode','local')
        if mode not in ('local','ai'):raise ValueError()
    except (ValueError,AttributeError):return JsonResponse({'error':'Envie uma mensagem de até 2.000 caracteres.'},status=400)
    key='assistant_'+account
    state={} if data.get('reset') else request.session.get(key,{})
    try:
        result=ai_query(request.user,account,message,state) if mode=='ai' else local_query(request.user,account,message,state)
    except AssistantUnavailable as e:return JsonResponse({'error':str(e),'ai':availability()},status=503)
    except (ValueError,ValidationError):return JsonResponse({'error':'Confira o valor informado. Use, por exemplo, “Tenho 5.000 reais disponíveis, cenário tático para SANB11”.'},status=400)
    request.session[key]=state
    return JsonResponse(result,encoder=DjangoJSONEncoder)
