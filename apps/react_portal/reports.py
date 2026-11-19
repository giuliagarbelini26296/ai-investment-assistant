"""Private, read-only access to the existing Excel report. No ledger writes."""
from datetime import datetime, timezone
from pathlib import Path
from django.conf import settings
from django.http import FileResponse, JsonResponse, Http404
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe

FILENAME = 'Carteira_Ana_Acompanhamento.xlsx'
SHARED_URL = 'https://docs.google.com/spreadsheets/d/1XjI6Bww4w1-mL7DL_P_Y_mZn-prLZY1i/edit'
MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


@never_cache
@require_safe
def excel(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Entre no portal para acessar o relatório.'}, status=401)
    account = request.GET.get('account', 'ana')
    if account not in ('ana', 'bruno'):
        return JsonResponse({'error': 'Carteira inválida.'}, status=400)
    path = Path(getattr(settings, 'ID3_EXCEL_REPORT_DIR', Path(settings.BASE_DIR) / 'private_reports')) / FILENAME
    available = account == 'ana' and path.is_file()
    if request.GET.get('download') == '1':
        if not available:
            raise Http404('O relatório desta carteira ainda não está disponível.')
        response = FileResponse(path.open('rb'), as_attachment=True, filename=FILENAME, content_type=MIME)
        response['X-Content-Type-Options'] = 'nosniff'
        return response
    return JsonResponse({
        'account': account, 'available': available,
        'updatedAt': datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat() if available else None,
        'downloadUrl': '/api/v1/reports/excel/?account=ana&download=1' if available else None,
        'sharedUrl': SHARED_URL if account == 'ana' else None,
        'refresh': 'Atualização horária quando Mac, Docker e Codex estão disponíveis.',
        'edits': 'Edições no Excel não alteram posições nem enviam ordens. Conflitos de edição no Drive interrompem a substituição automática até conferência.',
    })
