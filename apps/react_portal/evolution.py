"""Evolução real da carteira: fotos diárias do patrimônio e rentabilidade vs. referências.

O patrimônio vem das fotos diárias do portal (origem 'portal') e, antes delas, dos dias
reconstruídos a partir do histórico de movimentações do BTG (origem 'btg', ver reconstrucao.py).
A rentabilidade encadeia Modified Dietz entre pregões, descontando compras e vendas (flow) e
somando proventos (income). O CDI acumulado usa a série diária 12 do SGS/BCB.
"""
import datetime as dt
from decimal import Decimal

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe

from .models import PatrimonioDiario

HUNDRED = Decimal(100)
ACCOUNTS = ('ana', 'bruno')


INTERVALO_FOTO = dt.timedelta(minutes=5)


def registrar_patrimonio(agora=None, *, forcar=False):
    """Grava/atualiza a foto do dia de cada carteira e moeda. Retorna quantas linhas gravou.

    O coletor chama a cada passagem (30 s); a foto é refeita no máximo a cada 5 minutos.
    """
    from .api import portfolio_payload

    agora = agora or timezone.now()
    hoje = timezone.localdate(agora)
    ultima = PatrimonioDiario.objects.filter(date=hoje).order_by('-captured_at').values_list('captured_at', flat=True).first()
    if not forcar and ultima and agora - ultima < INTERVALO_FOTO:
        return 0
    gravadas = 0
    for account in ACCOUNTS:
        payload = portfolio_payload({'account': account, 'history': '0'}, 'coletor', now=agora)
        for t in payload['totals']:
            if not t['count']:
                continue
            PatrimonioDiario.objects.update_or_create(
                account=account, currency=t['currency'], date=hoje,
                defaults={'value': t['value'], 'invested': t['invested'], 'gain': t['gain'],
                          'positions': t['count'], 'quoted': t['quoted'], 'captured_at': agora,
                          'flow': fluxo_do_dia(account, t['currency'], hoje, t['invested'])})
            gravadas += 1
    return gravadas


def fluxo_do_dia(account, currency, dia, investido):
    """Compras ao custo menos vendas pelo valor recebido, desde a foto anterior.

    Variação do valor investido (custo) menos o lucro realizado nas vendas do dia. None quando
    não há foto anterior para comparar.
    """
    from apps.portfolio.models import Sale

    anterior = (PatrimonioDiario.objects.filter(account=account, currency=currency, date__lt=dia)
                .order_by('-date').first())
    if anterior is None:
        return None
    realizado = sum((s.quantity * s.unit_price - (s.fees or 0) - s.allocated_cost
                     for s in Sale.objects.filter(account=account, currency=currency, trade_date__gt=anterior.date,
                                                  trade_date__lte=dia)), Decimal(0))
    return investido - anterior.invested - realizado


def cdi_acumulado(dates):
    """CDI acumulado (%) em cada data, a partir da primeira, só com taxas publicadas.

    Convenção: a taxa do dia útil d remunera de d até o próximo dia útil, então o fator
    entre duas datas usa as taxas de [inicio, fim). Intervalo sem nenhuma taxa publicada
    deixa o ponto (e os seguintes) em None, em vez de supor um valor.
    """
    from apps.mercado.models import MacroSerie

    if len(dates) < 2:
        return [Decimal(0)] * len(dates) if dates else []
    taxas = {m.data: m.valor for m in MacroSerie.objects.filter(codigo=12, data__range=(dates[0], dates[-1]))}
    dias = sorted(taxas)
    saida, fator, valido = [Decimal(0)], Decimal(1), True
    for esquerda, direita in zip(dates, dates[1:], strict=False):
        no_intervalo = [taxas[d] for d in dias if esquerda <= d < direita]
        if not no_intervalo:  # taxa ainda não publicada: não supor; daqui em diante fica sem CDI
            valido = False
        for taxa in no_intervalo:
            fator *= 1 + taxa / HUNDRED
        saida.append((fator - 1) * HUNDRED if valido else None)
    return saida


def _snapshots(account):
    rows = {}
    for p in PatrimonioDiario.objects.filter(account=account).order_by('date'):
        rows.setdefault(p.currency, []).append({
            'date': p.date, 'value': p.value, 'invested': p.invested, 'gain': p.gain,
            'returnPct': p.gain / p.invested * HUNDRED if p.gain is not None and p.invested else None,
            'complete': p.complete, 'positions': p.positions, 'quoted': p.quoted, 'source': p.source})
    return rows


def _performance(account):
    """Rentabilidade encadeada entre pregões com foto completa, contra BOVA11/SPY e CDI."""
    from apps.insights.models import BenchmarkBar
    from apps.insights.services import interval_return

    saida = {}
    por_moeda = {}
    for p in PatrimonioDiario.objects.filter(account=account).order_by('date'):
        por_moeda.setdefault(p.currency, []).append(p)
    for currency, rows in por_moeda.items():
        benchmark = 'BOVA11' if currency == 'BRL' else 'SPY'
        refs = {b.date: b.close for b in BenchmarkBar.objects.filter(symbol=benchmark, date__range=(rows[0].date, rows[-1].date))}
        fluxos, anterior = {}, None
        for r in rows:
            fluxos[r.date] = (Decimal(0) if anterior is None else r.flow if r.flow is not None
                              else r.invested - anterior.invested)
            anterior = r
        marcos = [r for r in rows if r.date in refs and r.complete]
        reconstruido = [r.date for r in rows if r.source == 'btg']
        resultado = {'start': None, 'end': None, 'benchmark': benchmark,
                     'benchmarkLabel': 'Ibovespa (via BOVA11)' if benchmark == 'BOVA11' else 'S&P 500 (via SPY)',
                     'portfolio': None, 'reference': None, 'cdi': None, 'points': [], 'warnings': [],
                     'reconstructedUntil': reconstruido[-1] if reconstruido else None}
        if len(marcos) < 2:
            resultado['warnings'].append('São necessários dois pregões com foto completa do patrimônio. Nenhum período foi inventado.')
            saida[currency] = resultado
            continue
        nivel = Decimal(1)
        pontos = [{'date': marcos[0].date, 'portfolio': Decimal(0), 'benchmark': Decimal(0)}]
        for a, b in zip(marcos, marcos[1:], strict=False):
            meio = [r for r in rows if a.date < r.date <= b.date]
            dias = (b.date - a.date).days
            ret = interval_return(a.value, b.value, [(dias, sum((fluxos[r.date] for r in meio), Decimal(0)))],
                                  sum((r.income for r in meio), Decimal(0)), dias)
            if ret is None:
                resultado['warnings'].append(f'Base insuficiente para descontar os aportes em {b.date:%d/%m}.')
                break
            nivel *= 1 + ret
            pontos.append({'date': b.date, 'portfolio': (nivel - 1) * HUNDRED,
                           'benchmark': (refs[b.date] / refs[marcos[0].date] - 1) * HUNDRED})
        if currency == 'BRL':
            for ponto, cdi in zip(pontos, cdi_acumulado([p['date'] for p in pontos]), strict=False):
                ponto['cdi'] = cdi
        resultado.update(start=pontos[0]['date'], end=pontos[-1]['date'], points=pontos,
                         portfolio=pontos[-1]['portfolio'], reference=pontos[-1]['benchmark'],
                         cdi=pontos[-1].get('cdi') if currency == 'BRL' else None)
        saida[currency] = resultado
    return saida


def evolution_payload(account, agora=None):
    if account not in ACCOUNTS:
        raise ValueError('Carteira inválida. Escolha Ana ou Bruno.')
    agora = agora or timezone.now()
    return {'meta': {'schemaVersion': '1.1', 'account': account, 'generatedAt': agora,
                     'method': 'Patrimônio: foto diária gravada pelo portal; dias anteriores reconstruídos a partir do '
                               'histórico de movimentações do BTG com fechamentos reais (origem "btg"). '
                               'Rentabilidade: Modified Dietz encadeado entre pregões, descontando compras e vendas e '
                               'somando proventos. CDI: série diária 12 do SGS/Banco Central.'},
            'snapshots': _snapshots(account), 'performance': _performance(account), 'performanceNote': None}


@never_cache
@require_safe
def evolution(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': {'code': 'authentication_required', 'message': 'Entre com seu usuário do portal.'}}, status=401)
    try:
        payload = evolution_payload(request.GET.get('account', 'ana'))
    except ValueError as exc:
        return JsonResponse({'error': {'code': 'invalid_query', 'message': str(exc)}}, status=400)
    return JsonResponse(payload, json_dumps_params={'ensure_ascii': False})
