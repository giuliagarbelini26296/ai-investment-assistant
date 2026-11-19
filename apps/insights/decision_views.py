from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest
from django.shortcuts import render,redirect
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from .models import DecisionPreference,ValueReference,DecisionNotice,TechnicalWatch
from .decision_forms import DecisionForm,ReferenceForm
from .decisions import evaluate


@login_required
@never_cache
@require_http_methods(['GET','POST'])
def dashboard(request):
    account=request.GET.get('carteira','ana')
    if account not in ('ana','bruno'):
        return HttpResponseBadRequest('Escolha a carteira Ana ou Bruno.')
    preference=DecisionPreference.objects.filter(user=request.user,account=account).first()
    if preference is None:
        preference=DecisionPreference(user=request.user,account=account,profile='arrojado' if account=='ana' else '',horizon='')
    action=request.POST.get('action') if request.method=='POST' else ''
    form=DecisionForm(request.POST if action=='settings' else None,instance=preference)
    reference_form=ReferenceForm(request.POST if action=='reference' else None)
    if action=='watch_toggle':
        watch=TechnicalWatch.objects.filter(user=request.user,pk=request.POST.get('watch_id')).first() if request.POST.get('watch_id','').isdigit() else None
        if watch:
            watch.active=not watch.active
            watch.save(update_fields=['active'])
            messages.success(request,'Acompanhamento atualizado para as carteiras indicadas neste ativo.')
        return redirect('/avaliacao/?carteira='+account+'#acompanhamento')
    if action=='settings' and form.is_valid():
        preference=form.save()
        messages.success(request,'Parâmetros do cenário salvos apenas para esta carteira. Nenhuma operação foi enviada.')
        return redirect('/avaliacao/?carteira='+account)
    if action=='reference' and reference_form.is_valid():
        values={name:reference_form.cleaned_data[name] for name in ['currency','price','reference_date','source']}
        ValueReference.objects.update_or_create(user=request.user,account=account,
            asset=reference_form.cleaned_data['asset'],defaults=values)
        messages.success(request,'Premissa de valor salva com fonte e data. Ela não é uma cotação nem uma garantia de retorno.')
        return redirect('/avaliacao/?carteira='+account+'#avaliacoes')
    # Invalid submissions never alter the saved scenario or produce a plan from invalid input.
    preference=DecisionPreference.objects.filter(user=request.user,account=account).first() or DecisionPreference(user=request.user,account=account,profile='arrojado' if account=='ana' else '',horizon='')
    try:
        result=evaluate(request.user,account,preference,request.GET)
    except ValueError:
        return HttpResponseBadRequest('Filtro de avaliação inválido.')
    from .assistant import sources_status
    watches=TechnicalWatch.objects.filter(user=request.user).select_related('asset').order_by('asset__symbol')
    result.update(watches=[w for w in watches if getattr(w,account)],assistant_status=sources_status(),form=form,reference_form=reference_form,
        notices=DecisionNotice.objects.filter(user=request.user,account=account).select_related('asset')[:20])
    return render(request,'insights/decisions.html',result)
