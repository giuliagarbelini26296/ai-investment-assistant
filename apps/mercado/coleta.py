"""
Rotinas do menu Mercado executadas pelo coletor a cada passagem (≈ 30 s), sempre dentro das cotas.

1. Candles diários (todas as ações/criptos ativas):
   - carga inicial de ~400 dias (52 semanas + médias de 200) para quem tem menos de 250 candles;
   - fechamento do dia para todos, depois do encerramento da sessão;
   - ações dos EUA só acompanhadas: rodízio de ~1 h durante o pregão, que também gera a cotação
     (o último candle diário em andamento), já que elas ficam fora do ciclo de 15 min;
   - cripto: últimos candles a cada 15 min (Binance pública).
   Twelve Data Basic (8/min, 800/dia): o rodízio usa só o que sobra no minuto depois do ciclo da carteira
   e para quando restam 120 créditos no dia.
2. Finnhub (opcional): recomendações, preço-alvo, notícias, resultados e métricas — rodízio por ativo.
3. Alpha Vantage (opcional, 25/dia): ficha OVERVIEW (1x/semana por ação) e DIVIDENDS (1x/mês), no máximo
   1 chamada por passagem e 20 por dia.
4. brapi (B3): candles no rodízio (3 por passagem, reserva de 3.000 requisições/mês para as cotações),
   estatísticas, dados financeiros e dividendos 1x/semana por ação; recurso fora do plano (403) não é
   repetido antes de 7 dias.
5. Focus (BCB): expectativas de câmbio, Selic e IPCA; SGS (BCB): Selic meta e IPCA — uma vez por dia.

Nada é estimado: falha de uma fonte fica em RefreshState.last_error e a tela mostra "indisponível".
"""

from __future__ import annotations

import datetime as dt
import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.marketdata import budget
from apps.marketdata.adapters import adaptador_para_mercado, chave_ausente, criar_adaptador
from apps.marketdata.adapters.base import AdapterError, AuthError, RateLimitError
from apps.marketdata.calendars import calendario
from apps.marketdata.models import DailyBar, Quote, QuoteType, ScheduleState
from apps.portfolio.models import Asset, Market

from .models import (
    AnalystAction,
    AnalystSnapshot,
    EarningsEvent,
    FocusExpectativa,
    Fundamentos,
    MacroSerie,
    NewsItem,
    PriceTarget,
    Provento,
    RefreshState,
)

log = logging.getLogger("mercado")

CANDLES_MINIMOS = 250
DIAS_CARGA = 400
RODIZIO_EUA = dt.timedelta(minutes=55)
RODIZIO_CRIPTO = dt.timedelta(minutes=15)
REPETIR_FALHA = dt.timedelta(minutes=30)
RESERVA_DIA_TWELVEDATA = 120
MAX_TWELVEDATA_PASSAGEM = 4
MAX_BINANCE_PASSAGEM = 15
MAX_FINNHUB_PASSAGEM = 20
MAX_AV_DIA = 20
MAX_BRAPI_PASSAGEM = 3
RESERVA_MES_BRAPI = 3000  # sobra mensal guardada para o ciclo de cotações da B3
BRAPI_TIPOS = {
    "estatisticas": dt.timedelta(days=7),
    "financeiros": dt.timedelta(days=7),
    "proventos": dt.timedelta(days=7),
}
DIVIDENDOS_AV = dt.timedelta(days=30)
AVISO_PLANO = "histórico limitado"

# tipo → intervalo entre atualizações (Finnhub)
FINNHUB_TIPOS = {
    "recomendacoes": dt.timedelta(hours=24),
    "noticias": dt.timedelta(minutes=30),
    "resultados": dt.timedelta(hours=24),
    "metricas": dt.timedelta(hours=24),
    "preco_alvo": dt.timedelta(hours=24),
    "mudancas": dt.timedelta(hours=24),
}
# Consultas que podem ser pagas: um 403 suspende o tipo para TODOS os ativos por 7 dias.
FINNHUB_PODE_SER_PAGO = ("preco_alvo", "mudancas")
SEM_ACESSO_NO_PLANO = dt.timedelta(days=7)
FICHA_AV = dt.timedelta(days=7)


def executar(agora: dt.datetime | None = None) -> dict:
    agora = agora or timezone.now()
    resumo = {}
    for nome, func in (
        ("candles", candles_passo),
        ("finnhub", finnhub_passo),
        ("alphavantage", alphavantage_passo),
        ("brapi", brapi_passo),
        ("focus", focus_passo),
        ("macro", macro_passo),
    ):
        try:
            resumo[nome] = func(agora)
        except Exception:  # noqa: BLE001 - uma rotina não derruba as outras
            log.exception("rotina de mercado %s falhou", nome)
            resumo[nome] = "erro"
    return resumo


# --- utilidades --------------------------------------------------------------------------------


def _estado(asset: Asset, kind: str) -> RefreshState:
    obj, _ = RefreshState.objects.get_or_create(asset=asset, kind=kind)
    return obj


def _devido(estado: RefreshState, agora: dt.datetime, intervalo: dt.timedelta) -> bool:
    if estado.last_attempt_at is None:
        return True
    if estado.last_success_at is None or estado.last_success_at < estado.last_attempt_at:
        return agora - estado.last_attempt_at >= REPETIR_FALHA  # falhou: tenta de novo depois de 30 min
    return agora - estado.last_success_at >= intervalo


def _ok(estado: RefreshState, agora: dt.datetime) -> None:
    estado.last_attempt_at = estado.last_success_at = agora
    estado.last_error = ""
    estado.save()


def _erro(estado: RefreshState, agora: dt.datetime, exc: Exception) -> None:
    estado.last_attempt_at = agora
    estado.last_error = f"{getattr(exc, 'kind', type(exc).__name__)}: {exc}"[:300]
    estado.save()


def _prioridade(asset: Asset, favoritos: set[int]) -> int:
    """Carteira primeiro, depois favoritos, depois o restante do universo."""
    if asset.has_position:
        return 0
    return 1 if asset.pk in favoritos else 2


def _favoritos() -> set[int]:
    from .models import Favorito

    return set(Favorito.objects.values_list("asset_id", flat=True))


def _livre(
    info, agora: dt.datetime, reserva_minuto: int = 0, reserva_dia: int = 0, reserva_mes: int = 0
) -> int:
    """Chamadas ainda disponíveis agora (menor folga entre minuto e dia), descontadas as reservas."""
    saldo = budget.saldo(info, agora)
    folgas = []
    if "minuto" in saldo:
        usado, limite = saldo["minuto"]
        folgas.append(limite - usado - reserva_minuto)
    if "dia" in saldo:
        usado, limite = saldo["dia"]
        folgas.append(limite - usado - reserva_dia)
    if "mes" in saldo:
        usado, limite = saldo["mes"]
        folgas.append(limite - usado - reserva_mes)
    return max(0, min(folgas)) if folgas else 10_000


def _modo_demo() -> bool:
    return bool(settings.PORTAL["MODO_DEMO"])


# --- 1. candles ----------------------------------------------------------------------------------


def _fonte_do_mercado(mercado: str) -> str:
    from apps.marketdata.adapters import FONTE_POR_MERCADO

    return "demo" if _modo_demo() else FONTE_POR_MERCADO[mercado]


def _necessidade_candles(asset: Asset, agora: dt.datetime, fonte: str) -> tuple[str, int] | None:
    """(motivo, dias) quando o ativo precisa de candles agora; None quando está em dia."""
    estado = _estado(asset, "candles")
    n = DailyBar.objects.filter(asset=asset, source=fonte).count()
    limitado = (
        estado.last_error.startswith(AVISO_PLANO)
        and estado.last_success_at is not None
        and agora - estado.last_success_at < SEM_ACESSO_NO_PLANO
    )
    if n < CANDLES_MINIMOS and not limitado:
        if estado.last_attempt_at and agora - estado.last_attempt_at < REPETIR_FALHA:
            return None
        if estado.last_success_at and estado.last_success_at >= (estado.last_attempt_at or agora) and n > 0:
            # já carregou o máximo que a fonte tem (ativo novo): não insiste a cada passagem
            if agora - estado.last_success_at < dt.timedelta(hours=12):
                return None
        return "carga inicial", DIAS_CARGA
    if asset.market == Market.CRYPTO:
        return ("rodízio cripto", 3) if _devido(estado, agora, RODIZIO_CRIPTO) else None
    cal = calendario(asset.market)
    sit = cal.situacao(agora)
    if sit.estado == "aberto" and asset.coleta_horaria:
        return ("rodízio horário", 5) if _devido(estado, agora, RODIZIO_EUA) else None
    if sit.estado == "fechado" and sit.sessao is not None:
        ultimo = DailyBar.objects.filter(asset=asset, source=fonte).order_by("-session_date").first()
        janela = agora >= sit.sessao.fechamento + dt.timedelta(minutes=10)
        if janela and (ultimo is None or ultimo.session_date < sit.sessao.data or ultimo.provisional):
            if _devido(estado, agora, dt.timedelta(minutes=30)):
                return "fechamento", 5
    return None


def candles_passo(agora: dt.datetime) -> dict:
    favoritos = _favoritos()
    candidatos: list[tuple[int, int, Asset, str, int]] = []
    mercados = (Market.NYSE, Market.NASDAQ, Market.CRYPTO, Market.B3)
    for asset in Asset.objects.filter(is_active=True, market__in=mercados):
        fonte = _fonte_do_mercado(asset.market)
        if not _modo_demo() and chave_ausente(fonte):
            continue
        nec = _necessidade_candles(asset, agora, fonte)
        if nec is None:
            continue
        motivo, dias = nec
        ordem_motivo = {"fechamento": 0, "carga inicial": 1, "rodízio horário": 2, "rodízio cripto": 3}[
            motivo
        ]
        candidatos.append((_prioridade(asset, favoritos), ordem_motivo, asset, motivo, dias))
    candidatos.sort(key=lambda c: (c[1], c[0], c[2].symbol))

    feitos: dict[str, list[str]] = {}
    adaptadores: dict[str, object] = {}
    cota: dict[str, int] = {}
    for _, _, asset, motivo, dias in candidatos:
        chave = {Market.CRYPTO: "CRIPTO", Market.B3: "B3"}.get(asset.market, "EUA")
        if chave not in adaptadores:
            adaptadores[chave] = adaptador_para_mercado(asset.market)
            info = adaptadores[chave].info
            if info.codigo == "twelvedata":
                from apps.marketdata.adapters import fonte_cotacao
                from apps.marketdata.collector import _ativos_do_mercado

                aberto = calendario("NYSE").situacao(agora).estado == "aberto"
                cotando = fonte_cotacao("NYSE") == "twelvedata"
                reserva = len(_ativos_do_mercado("NYSE")) if (aberto and cotando) else 0
                cota[chave] = min(
                    MAX_TWELVEDATA_PASSAGEM, _livre(info, agora, reserva, RESERVA_DIA_TWELVEDATA)
                )
            elif info.codigo == "brapi":
                cota[chave] = min(MAX_BRAPI_PASSAGEM, _livre(info, agora, reserva_mes=RESERVA_MES_BRAPI))
            else:
                cota[chave] = min(MAX_BINANCE_PASSAGEM, _livre(info, agora))
        if cota[chave] <= 0:
            continue
        adaptador = adaptadores[chave]
        estado = _estado(asset, "candles")
        try:
            budget.reservar(adaptador.info, 1, agora)
        except budget.OrcamentoExcedido:
            cota[chave] = 0
            continue
        cota[chave] -= 1
        try:
            candles = adaptador.historico(asset.simbolo_na_fonte, dias)
        except AdapterError as exc:
            _erro(estado, agora, exc)
            log.warning("candles %s: %s", asset.symbol, exc)
            if isinstance(exc, AuthError | RateLimitError):
                cota[chave] = 0
            continue
        gravar_candles(asset, candles, agora)
        _ok(estado, agora)
        aviso = getattr(adaptador, "aviso_plano", "")
        if (
            aviso
        ):  # sucesso parcial: o plano limitou a faixa (fica registrado para a tela e para não insistir)
            estado.last_error = aviso[:300]
            estado.save(update_fields=["last_error"])
        feitos.setdefault(motivo, []).append(asset.symbol)
        if asset.coleta_horaria:
            _cotacao_do_candle(asset, agora)
    if feitos:
        log.info("candles | %s", "; ".join(f"{m}: {', '.join(s)}" for m, s in feitos.items()))
    return feitos


@transaction.atomic
def gravar_candles(asset: Asset, candles, agora: dt.datetime) -> int:
    sit = calendario(asset.market).situacao(agora) if asset.market != Market.CRYPTO else None
    hoje_sessao = None
    if sit is not None and sit.estado == "aberto" and sit.sessao is not None:
        hoje_sessao = sit.sessao.data
    n = 0
    for c in candles:
        provisorio = c.provisorio or (hoje_sessao is not None and c.data == hoje_sessao)
        DailyBar.objects.update_or_create(
            asset=asset,
            session_date=c.data,
            source=c.fonte,
            defaults={
                "time_basis": c.base_tempo,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume,
                "provisional": provisorio,
                "collected_at": agora,
            },
        )
        n += 1
    return n


def _cotacao_do_candle(asset: Asset, agora: dt.datetime) -> Quote | None:
    """Ação só acompanhada: a cotação é o último candle diário (em andamento ou fechado), com rótulo."""
    from apps.marketdata.history import fonte_principal

    fonte = fonte_principal(asset)
    barras = list(DailyBar.objects.filter(asset=asset, source=fonte).order_by("-session_date")[:2])
    if not barras:
        return None
    ultimo = barras[0]
    anterior = barras[1].close if len(barras) > 1 else None
    q = Quote.objects.create(
        asset=asset,
        price=ultimo.close,
        currency="USD",
        previous_close=anterior,
        change_24h_pct=None,
        market_time=None,
        collected_at=agora,
        source=ultimo.source,
        source_version="time_series",
        quote_type=QuoteType.UNDECLARED,
        raw={"candle": ultimo.session_date.isoformat(), "provisorio": ultimo.provisional},
    )
    try:
        from apps.marketdata.collector import _avaliar_alertas

        _avaliar_alertas([asset], agora)
    except Exception:  # noqa: BLE001
        log.exception("falha ao avaliar alertas de %s", asset.symbol)
    return q


# --- 2. Finnhub ---------------------------------------------------------------------------------


def _acoes_eua():
    return Asset.objects.filter(is_active=True, market__in=(Market.NYSE, Market.NASDAQ)).order_by("symbol")


def _sem_acesso(estado: RefreshState, agora: dt.datetime) -> bool:
    """Endpoint recusado pelo plano: não tenta de novo antes de 7 dias."""
    return (
        estado.last_error.startswith("autenticacao")
        and estado.last_attempt_at is not None
        and agora - estado.last_attempt_at < SEM_ACESSO_NO_PLANO
    )


def finnhub_passo(agora: dt.datetime) -> dict:
    if _modo_demo() or chave_ausente("finnhub"):
        return {}
    adaptador = criar_adaptador("finnhub")
    livre = min(MAX_FINNHUB_PASSAGEM, _livre(adaptador.info, agora))
    if livre <= 0:
        return {}
    favoritos = _favoritos()
    hoje = timezone.localdate(agora)
    bloqueados = _fh_bloqueados(agora)
    pendencias: list[tuple[int, str, Asset]] = []
    for asset in _acoes_eua():
        for tipo, intervalo in FINNHUB_TIPOS.items():
            if asset.is_etf and tipo in ("recomendacoes", "preco_alvo", "resultados", "metricas", "mudancas"):
                continue
            if tipo in bloqueados:
                continue
            estado = _estado(asset, f"fh_{tipo}")
            if _sem_acesso(estado, agora) or not _devido(estado, agora, intervalo):
                continue
            pendencias.append((_prioridade(asset, favoritos), tipo, asset))
    from .dolar import ativo_dolar

    dolar = ativo_dolar()
    estado_cambio = _estado(dolar, "fh_noticias")
    if _devido(estado_cambio, agora, FINNHUB_TIPOS["noticias"]):
        pendencias.insert(0, (0, "noticias_cambio", dolar))
    pendencias.sort(key=lambda p: (p[0], p[2].symbol))

    feitos: dict[str, int] = {}
    suspensos: set[str] = set()
    for _, tipo, asset in pendencias[:livre]:
        if tipo in suspensos:
            continue
        estado = _estado(asset, "fh_noticias" if tipo == "noticias_cambio" else f"fh_{tipo}")
        try:
            budget.reservar(adaptador.info, 1, agora)
        except budget.OrcamentoExcedido:
            break
        try:
            if tipo == "noticias_cambio":
                _gravar_noticias(adaptador.noticias_gerais("forex"), agora, None, NewsItem.Categoria.CAMBIO)
            elif tipo == "recomendacoes":
                _gravar_recomendacoes(asset, adaptador.recomendacoes(asset.symbol), agora)
            elif tipo == "preco_alvo":
                _gravar_preco_alvo(asset, adaptador.preco_alvo(asset.symbol), agora)
            elif tipo == "noticias":
                itens = adaptador.noticias_empresa(asset.symbol, hoje - dt.timedelta(days=3), hoje)
                _gravar_noticias(itens[:20], agora, asset, NewsItem.Categoria.EMPRESA)
            elif tipo == "resultados":
                _gravar_resultados(
                    asset,
                    adaptador.resultados(
                        hoje - dt.timedelta(days=120), hoje + dt.timedelta(days=120), asset.symbol
                    ),
                    agora,
                )
            elif tipo == "metricas":
                _gravar_ficha(asset, adaptador.ficha(asset.symbol), agora)
            elif tipo == "mudancas":
                desde = hoje - dt.timedelta(days=90)
                _gravar_mudancas(asset, adaptador.mudancas_recomendacao(asset.symbol, desde), agora)
        except RateLimitError as exc:
            _erro(estado, agora, exc)
            break
        except AuthError as exc:
            _erro(estado, agora, exc)
            if tipo in FINNHUB_PODE_SER_PAGO:
                _fh_suspender(tipo, agora, exc)
                suspensos.add(tipo)
            log.warning("finnhub %s %s: %s", tipo, asset.symbol, exc)
            continue
        except AdapterError as exc:
            _erro(estado, agora, exc)
            log.warning("finnhub %s %s: %s", tipo, asset.symbol, exc)
            continue
        _ok(estado, agora)
        feitos[tipo] = feitos.get(tipo, 0) + 1
        if tipo in ("recomendacoes", "resultados"):
            _avaliar(asset, agora)
    if feitos:
        log.info("finnhub | %s", feitos)
    return feitos


def _fh_bloqueados(agora: dt.datetime) -> set[str]:
    """Tipos da Finnhub recusados pelo plano (403) nos últimos 7 dias."""
    saida = set()
    for tipo in FINNHUB_PODE_SER_PAGO:
        e = ScheduleState.objects.filter(task=f"FH_{tipo.upper()}", last_status="sem_acesso").first()
        if e and e.last_attempt_at and agora - e.last_attempt_at < SEM_ACESSO_NO_PLANO:
            saida.add(tipo)
    return saida


def _fh_suspender(tipo: str, agora: dt.datetime, exc: Exception) -> None:
    plano, _ = ScheduleState.objects.get_or_create(task=f"FH_{tipo.upper()}")
    plano.last_attempt_at, plano.last_status, plano.last_error = agora, "sem_acesso", str(exc)[:300]
    plano.save()
    log.warning("finnhub: %s sem acesso no plano; suspenso por 7 dias", tipo)


def _gravar_mudancas(asset: Asset, itens, agora: dt.datetime) -> int:
    n = 0
    for m in itens:
        AnalystAction.objects.update_or_create(
            asset=asset,
            source=m.fonte,
            date=m.data,
            firm=m.instituicao,
            defaults={
                "from_grade": m.de,
                "to_grade": m.para,
                "action": m.acao,
                "collected_at": agora,
            },
        )
        n += 1
    plano, _ = ScheduleState.objects.get_or_create(task="FH_MUDANCAS")
    if plano.last_status != "completo":
        plano.last_status, plano.last_error = "completo", ""
    plano.last_attempt_at = plano.last_success_at = agora
    plano.save()
    return n


def _avaliar(asset: Asset, agora: dt.datetime) -> None:
    try:
        from apps.marketdata.collector import _avaliar_alertas

        _avaliar_alertas([asset], agora)
    except Exception:  # noqa: BLE001
        log.exception("falha ao avaliar alertas de %s", asset.symbol)


def _gravar_recomendacoes(asset: Asset, itens, agora: dt.datetime) -> int:
    for r in itens:
        AnalystSnapshot.objects.update_or_create(
            asset=asset,
            source=r.fonte,
            period=r.periodo,
            defaults={
                "strong_buy": r.strong_buy,
                "buy": r.buy,
                "hold": r.hold,
                "sell": r.sell,
                "strong_sell": r.strong_sell,
                "collected_at": agora,
            },
        )
    return len(itens)


def _gravar_preco_alvo(asset: Asset, alvo, agora: dt.datetime) -> None:
    if alvo is None:
        return
    ultimo = PriceTarget.objects.filter(asset=asset, source=alvo.fonte).order_by("-collected_at").first()
    if ultimo and ultimo.mean == alvo.media and ultimo.reference_date == alvo.data_referencia:
        ultimo.collected_at = agora  # mesmo valor: só renova a conferência
        ultimo.save(update_fields=["collected_at"])
        return
    PriceTarget.objects.create(
        asset=asset,
        source=alvo.fonte,
        mean=alvo.media,
        median=alvo.mediana,
        high=alvo.maximo,
        low=alvo.minimo,
        reference_date=alvo.data_referencia,
        collected_at=agora,
    )


def _gravar_noticias(itens, agora: dt.datetime, asset: Asset | None, categoria: str) -> int:
    n = 0
    for item in itens:
        _, criada = NewsItem.objects.get_or_create(
            source=item.fonte,
            url=item.url,
            asset=asset,
            defaults={
                "categoria": categoria,
                "publisher": item.veiculo,
                "headline": item.titulo,
                "summary": item.resumo,
                "published_at": item.publicada_em,
                "sentiment": item.sentimento,
                "sentiment_label": item.sentimento_rotulo,
                "collected_at": agora,
            },
        )
        n += int(criada)
    # guarda só o recente: 30 dias de notícias
    NewsItem.objects.filter(published_at__lt=agora - dt.timedelta(days=30)).delete()
    return n


def _gravar_resultados(asset: Asset, itens, agora: dt.datetime) -> int:
    n = 0
    for r in itens:
        if r.simbolo != asset.symbol:
            continue
        EarningsEvent.objects.update_or_create(
            asset=asset,
            source=r.fonte,
            date=r.data,
            defaults={
                "hour": r.horario,
                "eps_estimate": r.eps_estimado,
                "eps_actual": r.eps_real,
                "revenue_estimate": r.receita_estimada,
                "revenue_actual": r.receita_real,
                "quarter": r.trimestre,
                "year": r.ano,
                "collected_at": agora,
            },
        )
        n += 1
    return n


def _gravar_ficha(asset: Asset, ficha, agora: dt.datetime) -> Fundamentos:
    """Uma ficha por ativo × fonte, atualizada só com os campos que a fonte trouxe nesta chamada."""
    campos = {
        "sector": ficha.setor,
        "industry": ficha.industria,
        "pe": ficha.pl,
        "forward_pe": ficha.pl_projetado,
        "profit_margin": ficha.margem_liquida,
        "roe": ficha.roe,
        "market_cap": ficha.valor_mercado,
        "dividend_yield": ficha.dividend_yield,
        "week52_high": ficha.maxima_52s,
        "week52_low": ficha.minima_52s,
        "latest_quarter": ficha.ultimo_trimestre,
        "price_to_book": ficha.pvp,
        "ev_ebitda": ficha.ev_ebitda,
        "revenue_growth": ficha.crescimento_receita,
        "net_debt_ebitda": ficha.divida_liquida_ebitda,
        "recommendation_key": ficha.recomendacao_chave,
        "recommendation_mean": ficha.recomendacao_media,
        "analyst_opinions": ficha.n_analistas,
    }
    presentes = {k: v for k, v in campos.items() if v not in (None, "")}
    if ficha.divida_liquida_ebitda is not None:
        presentes["net_debt_calculado"] = ficha.divida_liquida_calculada
    reg = Fundamentos.objects.filter(asset=asset, source=ficha.fonte).order_by("-collected_at").first()
    if reg is None:
        reg = Fundamentos.objects.create(asset=asset, source=ficha.fonte, collected_at=agora, **presentes)
    else:
        for k, v in presentes.items():
            setattr(reg, k, v)
        reg.collected_at = agora
        reg.save()
    if ficha.setor and not asset.sector:
        asset.sector = ficha.setor[:80]
        asset.save(update_fields=["sector", "updated_at"])
    if ficha.recomendacao is not None:
        _gravar_recomendacoes(asset, [ficha.recomendacao], agora)
    if ficha.preco_alvo is not None:
        _gravar_preco_alvo(asset, ficha.preco_alvo, agora)
    return reg


def _gravar_proventos(asset: Asset, itens, agora: dt.datetime) -> int:
    n = 0
    for p in itens:
        _, criado = Provento.objects.get_or_create(
            asset=asset,
            source=p.fonte,
            tipo=p.tipo,
            data_com=p.data_com,
            data_pagamento=p.data_pagamento,
            valor=p.valor,
            defaults={"moeda": p.moeda, "data_aprovacao": p.data_aprovacao, "collected_at": agora},
        )
        n += int(criado)
    return n


# --- 3. Alpha Vantage ------------------------------------------------------------------------------


def alphavantage_passo(agora: dt.datetime) -> dict:
    if _modo_demo() or chave_ausente("alphavantage"):
        return {}
    adaptador = criar_adaptador("alphavantage")
    usado, _ = budget.saldo(adaptador.info, agora).get("dia", (0, 25))
    if usado >= MAX_AV_DIA:
        return {}
    favoritos = _favoritos()
    bloqueados = _av_bloqueados(agora)
    candidatos = []
    for asset in _acoes_eua():
        for tipo, intervalo, kind in (
            ("ficha", FICHA_AV, "av_ficha"),
            ("dividendos", DIVIDENDOS_AV, "av_dividendos"),
        ):
            if tipo in bloqueados:
                continue  # função paga no plano atual: suspensa para todos os ativos
            if tipo == "ficha" and asset.is_etf:
                continue  # OVERVIEW não cobre ETFs
            estado = _estado(asset, kind)
            falhou = estado.last_attempt_at and (
                estado.last_success_at is None or estado.last_success_at < estado.last_attempt_at
            )
            if falhou and "por segundo" not in estado.last_error:
                if agora - estado.last_attempt_at < dt.timedelta(hours=20):
                    continue  # cota diária ou sem cobertura: tenta amanhã (limite por segundo: 30 min)
            if not _devido(estado, agora, intervalo):
                continue
            candidatos.append(
                (
                    _prioridade(asset, favoritos),
                    0 if tipo == "ficha" else 1,
                    asset.symbol,
                    tipo,
                    asset,
                    estado,
                )
            )
    if not candidatos:
        return {}
    candidatos.sort(key=lambda c: c[:3])
    _, _, _, tipo, asset, estado = candidatos[0]
    try:
        budget.reservar(adaptador.info, 1, agora)
        resultado = adaptador.ficha(asset.symbol) if tipo == "ficha" else adaptador.dividendos(asset.symbol)
    except budget.OrcamentoExcedido:
        return {}
    except AuthError as exc:
        _erro(estado, agora, exc)
        if "premium" in str(exc):
            plano, _ = ScheduleState.objects.get_or_create(task=f"AV_{tipo.upper()}")
            plano.last_attempt_at, plano.last_status, plano.last_error = agora, "sem_acesso", str(exc)[:300]
            plano.save()
            log.warning("alphavantage: %s indisponível no plano gratuito; suspenso por 7 dias", tipo)
        else:
            log.warning("alphavantage %s %s: %s", tipo, asset.symbol, exc)
        return {"erro": asset.symbol}
    except AdapterError as exc:
        _erro(estado, agora, exc)
        log.warning("alphavantage %s %s: %s", tipo, asset.symbol, exc)
        return {"erro": asset.symbol}
    if tipo == "ficha":
        _gravar_ficha(asset, resultado, agora)
    else:
        _gravar_proventos(asset, resultado, agora)
    _ok(estado, agora)
    _avaliar(asset, agora)
    return {tipo: asset.symbol}


def _av_bloqueados(agora: dt.datetime) -> set[str]:
    """Funções da Alpha Vantage recusadas como pagas ('premium') nos últimos 7 dias."""
    saida = set()
    for tipo in ("ficha", "dividendos"):
        e = ScheduleState.objects.filter(task=f"AV_{tipo.upper()}", last_status="sem_acesso").first()
        if e and e.last_attempt_at and agora - e.last_attempt_at < SEM_ACESSO_NO_PLANO:
            saida.add(tipo)
    return saida


# --- brapi (B3) --------------------------------------------------------------------------------------


def brapi_passo(agora: dt.datetime) -> dict:
    if _modo_demo():
        return {}
    ativos = list(Asset.objects.filter(is_active=True, market=Market.B3).order_by("symbol"))
    if not ativos:
        return {}
    adaptador = criar_adaptador("brapi")
    livre = min(MAX_BRAPI_PASSAGEM, _livre(adaptador.info, agora, reserva_mes=RESERVA_MES_BRAPI))
    if livre <= 0:
        return {}
    favoritos = _favoritos()
    pendencias = []
    for asset in ativos:
        for tipo, intervalo in BRAPI_TIPOS.items():
            estado = _estado(asset, f"br_{tipo}")
            if _sem_acesso(estado, agora) or not _devido(estado, agora, intervalo):
                continue
            pendencias.append((_prioridade(asset, favoritos), asset.symbol, tipo, asset, estado))
    pendencias.sort(key=lambda p: p[:3])
    feitos: dict[str, int] = {}
    for _, _, tipo, asset, estado in pendencias[:livre]:
        try:
            budget.reservar(adaptador.info, 1, agora)
        except budget.OrcamentoExcedido:
            break
        try:
            simbolo = asset.simbolo_na_fonte
            if tipo == "estatisticas":
                _gravar_ficha(asset, adaptador.estatisticas(simbolo), agora)
            elif tipo == "financeiros":
                _gravar_ficha(asset, adaptador.dados_financeiros(simbolo), agora)
            else:
                _gravar_proventos(asset, adaptador.proventos(simbolo), agora)
        except RateLimitError as exc:
            _erro(estado, agora, exc)
            break
        except AdapterError as exc:
            _erro(estado, agora, exc)
            log.warning("brapi %s %s: %s", tipo, asset.symbol, exc)
            continue
        _ok(estado, agora)
        feitos[tipo] = feitos.get(tipo, 0) + 1
    if feitos:
        log.info("brapi | %s", feitos)
    return feitos


# --- 4. Focus ---------------------------------------------------------------------------------------


def focus_passo(agora: dt.datetime, *, forcar: bool = False) -> dict:
    if _modo_demo():
        return {}
    local = timezone.localtime(agora)
    estado, _ = ScheduleState.objects.get_or_create(task="FOCUS")
    if not forcar and (estado.last_close_date == local.date() or local.hour < 9):
        return {}
    if not forcar and estado.last_attempt_at and agora - estado.last_attempt_at < dt.timedelta(hours=1):
        return {}
    estado.last_attempt_at = agora
    adaptador = criar_adaptador("focus_bcb")
    try:
        itens = adaptador.expectativas(("Câmbio", "Selic", "IPCA"), 200)
    except AdapterError as exc:
        estado.last_status, estado.last_error = "falha", str(exc)[:300]
        estado.save()
        return {"erro": str(exc)}
    for e in itens:
        FocusExpectativa.objects.update_or_create(
            indicador=e.indicador,
            data=e.data,
            data_referencia=e.data_referencia,
            base_calculo=e.base_calculo,
            defaults={
                "media": e.media,
                "mediana": e.mediana,
                "minimo": e.minimo,
                "maximo": e.maximo,
                "desvio": e.desvio,
                "respondentes": e.respondentes,
                "collected_at": agora,
            },
        )
    estado.last_success_at, estado.last_close_date = agora, local.date()
    estado.last_status, estado.last_error = "completo", ""
    estado.save()
    return {"focus": len(itens)}


# --- 6. Macro (SGS) -------------------------------------------------------------------------------------


def macro_passo(agora: dt.datetime, *, forcar: bool = False) -> dict:
    """Selic meta e IPCA (mensal e 12 meses) do SGS/BCB, uma vez por dia a partir das 9h."""
    if _modo_demo():
        return {}
    from apps.marketdata.adapters.inteligencia import SgsBcbAdapter

    local = timezone.localtime(agora)
    estado, _ = ScheduleState.objects.get_or_create(task="MACRO")
    if not forcar and (estado.last_close_date == local.date() or local.hour < 9):
        return {}
    if not forcar and estado.last_attempt_at and agora - estado.last_attempt_at < dt.timedelta(hours=1):
        return {}
    estado.last_attempt_at = agora
    adaptador = criar_adaptador("sgs_bcb")
    gravados, erros = 0, []
    for codigo, nome in SgsBcbAdapter.SERIES.items():
        try:
            pontos = adaptador.serie(codigo, 20)
        except AdapterError as exc:
            erros.append(f"{codigo}: {exc}")
            continue
        for data, valor in pontos:
            MacroSerie.objects.update_or_create(
                codigo=codigo, data=data, defaults={"nome": nome, "valor": valor, "collected_at": agora}
            )
            gravados += 1
    if erros and not gravados:
        estado.last_status, estado.last_error = "falha", "; ".join(erros)[:300]
    else:
        estado.last_success_at, estado.last_close_date = agora, local.date()
        estado.last_status, estado.last_error = "completo", "; ".join(erros)[:300]
    estado.save()
    return {"macro": gravados}
