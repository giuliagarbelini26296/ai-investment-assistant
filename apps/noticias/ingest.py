"""Notícias públicas, sem buscar a íntegra nem executar conteúdo recebido das fontes."""

import datetime as dt
import hashlib
import html
import logging
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from django.conf import settings
from django.db import connection
from django.db.models import Q
from django.utils import timezone
from django.utils.html import strip_tags

from apps.marketdata import budget
from apps.marketdata.adapters import chave_ausente, criar_adaptador
from apps.mercado.models import NewsItem
from apps.portfolio.models import Asset

from .models import Article, SourceState
from .translation import MODEL_NAME, translate

log = logging.getLogger("noticias")
FEEDS = (
    (
        "agencia_brasil",
        "Agência Brasil · Economia",
        "https://agenciabrasil.ebc.com.br/rss/economia/feed.xml",
        "pt",
        "brasil",
    ),
    ("fed", "Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml", "en", "internacional"),
)
TOPIC_WORDS = (
    ("cripto", r"\b(bitcoin|ethereum|crypto|cripto\w*|blockchain|stablecoin\w*)\b"),
    (
        "juros",
        r"\b(inflation|inflationary|infla\w*|interest rates?|selic|juros|monetary|monetária|fomc|copom)\b",
    ),
    ("cambio", r"\b(forex|currency|currencies|dollar|dólar|câmbio|exchange rate)\b"),
)


def clean_text(text, limit=2400):
    text = html.unescape(str(text or ""))
    # Executáveis e estilos nunca entram no texto visível ou no tradutor.
    text = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", "", text, flags=re.I | re.S)
    return re.sub(r"\s+", " ", html.unescape(strip_tags(text))).strip()[:limit]


def canonical_url(value):
    try:
        p = urlsplit(str(value).strip())
        if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
            return ""
        if len(value) > 1200:
            return ""
        query = [
            (k, v)
            for k, v in parse_qsl(p.query, keep_blank_values=True)
            if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}
        ]
        return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or "/", urlencode(sorted(query)), ""))
    except (ValueError, TypeError):
        return ""


def topic_for(text, has_asset=False):
    for topic, pattern in TOPIC_WORDS:
        if re.search(pattern, text, re.I):
            return topic
    return "empresas" if has_asset else "economia"


def store(
    *,
    url,
    headline,
    summary,
    published_at,
    source,
    publisher,
    language="en",
    region="internacional",
    assets=(),
    now=None,
):
    now = now or timezone.now()
    url = canonical_url(url)
    headline = clean_text(headline, 600)
    if not url or not headline or published_at is None:
        return None, False
    if timezone.is_naive(published_at):
        published_at = timezone.make_aware(published_at, dt.UTC)
    if published_at > now + dt.timedelta(minutes=10):
        return None, False
    summary = clean_text(summary)
    article, created = Article.objects.get_or_create(
        url_hash=hashlib.sha256(url.encode()).hexdigest(),
        defaults={
            "url": url,
            "headline": headline,
            "summary": summary,
            "language": language,
            "region": region,
            "source": source,
            "publisher": clean_text(publisher, 120),
            "published_at": published_at,
            "collected_at": now,
            "topic": topic_for(headline + " " + summary, bool(assets)),
            "translation_status": Article.Translation.ORIGINAL
            if language.startswith("pt")
            else Article.Translation.PENDING,
        },
    )
    if assets:
        article.assets.add(*assets)
    return article, created


def sync_existing(now=None):
    now = now or timezone.now()
    n = 0
    qs = NewsItem.objects.filter(published_at__gte=now - dt.timedelta(days=30)).order_by("published_at", "pk")
    for item in qs.iterator(chunk_size=300):
        _, created = store(
            url=item.url,
            headline=item.headline,
            summary=item.summary,
            published_at=item.published_at,
            source=item.source,
            publisher=item.publisher or "Finnhub",
            assets=[item.asset_id] if item.asset_id else [],
            now=now,
        )
        n += int(created)
    return n


def _date(value):
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except (ValueError, TypeError):
        try:
            return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None


def parse_feed(payload):
    if len(payload) > 2_000_000 or b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper():
        raise ValueError("Feed fora do formato permitido")
    root = ET.fromstring(payload)
    items = root.findall(".//item")
    atom = "{http://www.w3.org/2005/Atom}"
    if not items:
        items = root.findall(f"{atom}entry")
    result = []
    for item in items[:100]:
        link = item.findtext("link")
        if not link:
            links = item.findall(f"{atom}link")
            link = next((x.get("href", "") for x in links if x.get("rel", "alternate") == "alternate"), "")
        result.append(
            {
                "url": link,
                "headline": item.findtext("title") or item.findtext(f"{atom}title") or "",
                "summary": item.findtext("description") or item.findtext(f"{atom}summary") or "",
                "published_at": _date(
                    item.findtext("pubDate")
                    or item.findtext(f"{atom}published")
                    or item.findtext(f"{atom}updated")
                ),
            }
        )
    if root.tag not in {"rss", f"{atom}feed", "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}RDF"}:
        raise ValueError("Resposta não é um feed de notícias")
    return result


def _asset_matches(text):
    """Relaciona somente menções explícitas; não interpreta notícias como sinal de compra/venda."""
    found = []
    for asset in Asset.objects.filter(is_active=True).exclude(market="CAMBIO"):
        symbol = asset.symbol
        name = asset.name.strip()
        prefix = r"\$" if len(symbol) < 3 else r"\$?"
        symbol_pattern = rf"(?<![A-Za-z0-9]){prefix}{re.escape(symbol)}(?![A-Za-z0-9])"
        name_match = len(name) >= 5 and name.casefold() in text.casefold()
        # Tickers curtos são ambíguos em texto comum; exigimos caixa alta ou cifrão.
        if re.search(symbol_pattern, text) or name_match:
            found.append(asset.pk)
    return found


def fetch_feed(spec, state, client, now):
    key, name, url, language, region = spec
    headers = {
        "User-Agent": "PortalInvestimentos/1.0 (personal RSS reader)",
        "Accept": "application/rss+xml, application/xml",
    }
    if state.etag:
        headers["If-None-Match"] = state.etag
    if state.last_modified:
        headers["If-Modified-Since"] = state.last_modified
    with client.stream("GET", url, headers=headers, timeout=12, follow_redirects=False) as response:
        if response.status_code == 304:
            return state.article_count
        response.raise_for_status()
        parts, size = [], 0
        for part in response.iter_bytes():
            size += len(part)
            if size > 2_000_000:
                raise ValueError("Feed maior que o limite permitido")
            parts.append(part)
        items = parse_feed(b"".join(parts))
        state.etag = response.headers.get("etag", "")[:400]
        state.last_modified = response.headers.get("last-modified", "")[:120]
    accepted = 0
    for item in items:
        assets = _asset_matches(item["headline"] + " " + clean_text(item["summary"]))
        article, _ = store(
            **item, source=key, publisher=name, language=language, region=region, assets=assets, now=now
        )
        accepted += int(article is not None)
    return accepted


def fetch_finnhub(now):
    if chave_ausente("finnhub"):
        raise RuntimeError("Fonte internacional não conectada")
    adapter = criar_adaptador("finnhub")
    try:
        budget.reservar(adapter.info, 1, now)
        items = adapter.noticias_gerais("general")
    finally:
        adapter.client.close()
    n = 0
    for item in items[:100]:
        article, _ = store(
            url=item.url,
            headline=item.titulo,
            summary=item.resumo,
            published_at=item.publicada_em,
            source="finnhub",
            publisher=item.veiculo or "Finnhub",
            now=now,
            assets=_asset_matches(item.titulo + " " + item.resumo),
        )
        n += int(article is not None)
    return n


def translate_pending(limit=24, now=None, translator=None):
    now = now or timezone.now()
    translator = translator or translate
    due = Q(translation_attempt_at__isnull=True) | Q(translation_attempt_at__lt=now - dt.timedelta(hours=1))
    items = Article.objects.filter(
        due, language="en", translation_status__in=[Article.Translation.PENDING, Article.Translation.FAILED]
    ).order_by("-published_at", "pk")[:limit]
    done = 0
    for article in items:
        article.translation_attempt_at = now
        try:
            headline = translator(article.headline)
            summary = translator(article.summary) if article.summary else ""
            if not headline.strip():
                raise ValueError("Tradução vazia")
        except Exception as exc:  # falha de tradução não bloqueia a coleta nem apaga o original
            article.translation_status = Article.Translation.FAILED
            article.save(update_fields=["translation_status", "translation_attempt_at"])
            log.warning("tradução de notícia indisponível: %s", type(exc).__name__)
            break  # evita repetir a mesma falha para todo o lote
        article.headline_pt, article.summary_pt = headline[:800], summary
        article.translation_status = Article.Translation.DONE
        article.translation_model = MODEL_NAME
        article.translated_at = now
        article.save(
            update_fields=[
                "headline_pt",
                "summary_pt",
                "translation_status",
                "translation_model",
                "translated_at",
                "translation_attempt_at",
            ]
        )
        done += 1
    return done


def run_once(now=None, client=None, translate_limit=24):
    """Chamado por um trabalhador separado: nunca pelas requisições das páginas."""
    now = now or timezone.now()
    interval = dt.timedelta(minutes=settings.NOTICIAS["INTERVALO_MIN"])
    states = []
    own_client = client is None
    client = client or httpx.Client(timeout=12)
    try:
        for key, name in [
            ("carteira", "Empresas acompanhadas · Finnhub"),
            ("finnhub", "Finnhub · Mercado internacional"),
            *[(s[0], s[1]) for s in FEEDS],
        ]:
            state, _ = SourceState.objects.get_or_create(key=key, defaults={"name": name})
            if state.next_check_at and state.next_check_at > now and not state.requested:
                continue
            if state.last_attempt_at and now - state.last_attempt_at < dt.timedelta(minutes=2):
                continue
            state.last_attempt_at, state.requested = now, False
            try:
                if key == "carteira":
                    count = sync_existing(now)
                elif key == "finnhub":
                    count = fetch_finnhub(now)
                else:
                    count = fetch_feed(next(s for s in FEEDS if s[0] == key), state, client, now)
                state.article_count = count
                state.last_success_at, state.last_error = now, ""
                state.next_check_at = now + interval
            except Exception as exc:
                state.last_error = "Atualização temporariamente indisponível (" + type(exc).__name__ + ")"
                state.next_check_at = now + dt.timedelta(minutes=5)
                log.warning("fonte %s indisponível: %s", key, type(exc).__name__)
            state.save()
            states.append(state.key)
        translated = translate_pending(limit=translate_limit, now=now)
        return {"fontes": states, "traduzidas": translated}
    finally:
        if own_client:
            client.close()


def acquire_worker_lock():
    """Uma única rotina de notícias por banco; trava liberada quando a conexão termina."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [840031103])
        return cursor.fetchone()[0]
