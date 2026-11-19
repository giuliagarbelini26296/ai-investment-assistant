import datetime as dt

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST, require_GET

from apps.portfolio import services as portfolio
from . import services
from .forms import CashForm, RiskForm, SimulationForm
from .models import BenchmarkState, CashEntry, RiskPreference


@login_required
@never_cache
@require_http_methods(['GET', 'POST'])
def dashboard(request):
    preference = RiskPreference.objects.filter(user=request.user).first() or RiskPreference(user=request.user)
    lines = [l for l in portfolio.linhas_da_carteira() if l.posicao]
    action = request.POST.get('action') if request.method == 'POST' else None
    risk_form = RiskForm(request.POST if action == 'limits' else None, instance=preference)
    cash_form = CashForm(request.POST if action == 'cash' else None)
    sim_form = SimulationForm(request.POST if action == 'simulate' else None)
    simulation = None
    if action == 'limits' and risk_form.is_valid():
        risk_form.save()
        messages.success(request, 'Seus limites foram salvos. São preferências pessoais de acompanhamento, não limites recomendados.')
        return redirect('/planejamento/#risco')
    if action == 'cash' and cash_form.is_valid():
        try:
            _, created = services.register_cash(cash_form, request.user)
        except ValueError as exc:
            cash_form.add_error(None, str(exc))
        else:
            messages.success(request, 'Lançamento registrado. A posição em ações não foi alterada.' if created else 'Este lançamento já foi registrado; não houve duplicação.')
            return redirect('/planejamento/#resultado')
    if action == 'simulate' and sim_form.is_valid():
        simulation = services.simulate(sim_form, lines, preference)
    now, today = timezone.now(), timezone.localdate()
    quality = [services.quality_for(line, now) for line in lines]
    concentration = services.concentrations(lines, preference)
    events = services.agenda(lines, today)
    start = today - dt.timedelta(days=90)
    for line in lines:
        if line.asset.position_balance:
            start = max(start, line.asset.position_balance.reference_date)
    performance = [services.performance(lines, c, start, today) for c in sorted({l.asset.native_currency for l in lines})]
    fx = concentration['fx']
    usd = [l for l in lines if l.asset.native_currency == 'USD']
    fx_report = {'available': False, 'reason': 'Falta câmbio de aquisição dos saldos conciliados; não usamos o dólar atual como custo histórico.'}
    if usd and fx and not fx.desatualizado and all(l.avaliacao for l in usd):
        fx_report['current_value'] = sum((l.avaliacao.valor_atual for l in usd), services.ZERO) * fx.valor
        if all(not l.asset.position_balance and all(lot.fx_rate or lot.fx_resolved for lot in l.asset.active_lots) for l in usd):
            cost_usd = sum((l.posicao.investido for l in usd), services.ZERO)
            cost_brl = sum((lot.quantity * lot.unit_price * (lot.fx_rate or lot.fx_resolved) for l in usd for lot in l.asset.active_lots), services.ZERO)
            fx_report.update(available=True, asset_effect=(sum((l.avaliacao.valor_atual for l in usd), services.ZERO) - cost_usd) * fx.valor,
                currency_effect=cost_usd * fx.valor - cost_brl, total=fx_report['current_value'] - cost_brl)
    return render(request, 'insights/dashboard.html', {
        'quality': quality, 'concentration': concentration, 'events': events, 'today': today,
        'until': today + dt.timedelta(days=7), 'risk_form': risk_form, 'cash_form': cash_form,
        'sim_form': sim_form, 'simulation': simulation, 'results': services.economic_results(lines),
        'entries': CashEntry.objects.select_related('asset').all()[:100], 'fx_report': fx_report,
        'performance': performance, 'benchmark_states': BenchmarkState.objects.all(),
        'watchlist': services.watchlist(lines, quality, events, preference, concentration),
        'checked': now, 'page_title': 'Planejamento',
    })


@login_required
@require_POST
def cancel_entry(request, key):
    if request.POST.get('confirm') != 'yes':
        messages.error(request, 'Confirme o cancelamento para preservar uma trilha clara.')
    else:
        changed = CashEntry.objects.filter(pk=key, cancelled_at=None).update(cancelled_at=timezone.now(), cancelled_by=request.user)
        messages.success(request, 'Lançamento cancelado; registro preservado no histórico.' if changed else 'Lançamento já cancelado ou inexistente.')
    return redirect('/planejamento/#resultado')


@login_required
@never_cache
@require_GET
def calendar_export(request):
    """Date-only calendar. No invented event time or third-party notifications."""
    lines = [l for l in portfolio.linhas_da_carteira() if l.posicao]
    events = services.agenda(lines)
    def escape(value):
        return str(value).replace('\\','\\\\').replace('\n','\\n').replace(';','\\;').replace(',','\\,').replace('\r','')
    rows = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//ID3//Planejamento//PT', 'CALSCALE:GREGORIAN']
    for event in events:
        rows.extend(['BEGIN:VEVENT', f"UID:{event['symbol']}-{event['date']:%Y%m%d}@portal-investimentos",
            f"DTSTAMP:{timezone.now():%Y%m%dT%H%M%SZ}", f"DTSTART;VALUE=DATE:{event['date']:%Y%m%d}",
            f"DTEND;VALUE=DATE:{event['date'] + dt.timedelta(days=1):%Y%m%d}",
            'SUMMARY:' + escape(f"{event['symbol']} · {event['title']}"),
            'DESCRIPTION:' + escape((event.get('timing') or 'Horário não informado') + '\nData sujeita a alteração. ' + event.get('source','')),
            'END:VEVENT'])
    rows.append('END:VCALENDAR')
    # RFC 5545 folding counts UTF-8 octets, not Python characters.
    folded=[]
    for row in rows:
        part=''
        for char in row:
            if len((part+char).encode('utf-8')) > 73:
                folded.append(part); part=' '+char
            else:
                part+=char
        folded.append(part)
    response = HttpResponse('\r\n'.join(folded)+'\r\n', content_type='text/calendar; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="agenda-carteira.ics"'
    return response
