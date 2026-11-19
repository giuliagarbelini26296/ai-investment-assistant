from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.middleware.csrf import get_token
from django.contrib.messages import get_messages
from django.utils.html import json_script
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe


@never_cache
@require_safe
@login_required
def shell(request):
    html = (Path(settings.BASE_DIR) / 'apps/react_portal/static/react_portal/index.html').read_text()
    html = html.replace('<head>', f'<head><meta name="csrf-token" content="{get_token(request)}">', 1)
    notices = [{"text": str(message), "tags": message.tags} for message in get_messages(request)]
    html = html.replace("</body>", str(json_script(notices, "id3-notices")) + "</body>", 1)
    return HttpResponse(html, content_type='text/html; charset=utf-8')


def csrf_failure(request, reason=''):
    return JsonResponse({'error': {'code': 'csrf_failed',
                                  'message': 'Sessão de formulário expirada. Recarregue a página.'}}, status=403)
