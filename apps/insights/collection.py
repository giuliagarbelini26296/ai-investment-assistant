"""Two benchmark histories, at most once a day each; no new holdings or messaging."""
import datetime as dt
import logging

from django.db import transaction
from django.utils import timezone

from apps.marketdata.adapters import criar_adaptador
from apps.marketdata import budget
from .models import BenchmarkBar, BenchmarkState

log = logging.getLogger(__name__)
BENCHMARKS = {'BOVA11': 'brapi', 'SPY': 'twelvedata'}


def refresh_benchmarks(now=None):
    now = now or timezone.now()
    for symbol, source in BENCHMARKS.items():
        with transaction.atomic():
            state, _ = BenchmarkState.objects.get_or_create(symbol=symbol)
            state = BenchmarkState.objects.select_for_update().get(symbol=symbol)
            if state.next_attempt and state.next_attempt > now:
                continue
            state.last_attempt = now
            state.next_attempt = now + dt.timedelta(hours=24)
            state.message = 'Coleta em andamento.'
            state.save()
        try:
            adapter = criar_adaptador(source)
            budget.reservar(adapter.info, 1, now)
            try:
                bars = adapter.historico(symbol, dias=90)
            finally:
                adapter.client.close()
            valid = [b for b in bars if not b.provisorio and b.close > 0 and b.data <= timezone.localdate(now)]
            with transaction.atomic():
                for bar in valid:
                    BenchmarkBar.objects.update_or_create(symbol=symbol, date=bar.data,
                        defaults={'close':bar.close, 'source':source, 'collected_at':now})
                state.last_success = now if valid else state.last_success
                state.message = f'{len(valid)} fechamentos disponíveis · {source}.' if valid else 'Fonte sem fechamentos completos. Comparação aguarda dados.'
                state.save(update_fields=['last_success','message'])
        except Exception:
            # Don't expose provider error bodies/URLs or API keys to pages or logs.
            BenchmarkState.objects.filter(symbol=symbol).update(
                message='Referência indisponível nesta tentativa; pode haver limite ou restrição do plano. Próxima tentativa em até 24 h.')
            log.warning('Histórico de referência %s indisponível; nova tentativa diária.', symbol)
