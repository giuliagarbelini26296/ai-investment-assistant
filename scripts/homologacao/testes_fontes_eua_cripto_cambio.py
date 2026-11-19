"""
Sondagem de fontes de dados — ações EUA, cripto e câmbio USD/BRL (portal de ações).

Uso (Python 3.9+, sem dependências externas):
    python3 testes_fontes_eua_cripto_cambio.py saida.json

O script faz UMA chamada HTTPS por endpoint, sem credenciais, e grava status HTTP,
cabeçalhos relevantes, tempo de resposta e o corpo (JSON ou texto) em saida.json.
Não cria contas, não usa chaves privadas e não repete chamadas (sem risco de 429).
Execute a partir de uma máquina com acesso à internet; o sandbox da sessão Claude
tem bloqueio de política para estes hosts.
"""
import json, time, datetime, sys, os
import urllib.request, urllib.error, ssl

ctx = ssl.create_default_context()

TESTS = [
    # --- Ações EUA ---
    ("yahoo_ttwo_chart", "https://query1.finance.yahoo.com/v8/finance/chart/TTWO?range=1mo&interval=1d"),
    ("yahoo_aapl_quote_chart", "https://query1.finance.yahoo.com/v8/finance/chart/AAPL?range=1d&interval=1d"),
    ("stooq_ttwo_csv", "https://stooq.com/q/l/?s=ttwo.us&f=sd2t2ohlcv&h&e=csv"),
    ("twelvedata_aapl_demo", "https://api.twelvedata.com/quote?symbol=AAPL&apikey=demo"),
    ("twelvedata_ttwo_demo", "https://api.twelvedata.com/quote?symbol=TTWO&apikey=demo"),
    ("alpha_ibm_demo", "https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol=IBM&apikey=demo"),
    ("alpha_ttwo_demo", "https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol=TTWO&apikey=demo"),
    ("finnhub_ttwo_notoken", "https://finnhub.io/api/v1/quote?symbol=TTWO"),
    # --- Cripto ---
    ("yahoo_btcusd_chart", "https://query1.finance.yahoo.com/v8/finance/chart/BTC-USD?range=1mo&interval=1d"),
    ("coingecko_btc", "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd,brl&include_last_updated_at=true&include_24hr_change=true"),
    ("coinbase_btcusd_spot", "https://api.coinbase.com/v2/prices/BTC-USD/spot"),
    ("binance_btcusdt", "https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT"),
    ("brapi_crypto_btc", "https://brapi.dev/api/v2/crypto?coin=BTC&currency=USD"),
    ("twelvedata_btcusd_demo", "https://api.twelvedata.com/price?symbol=BTC/USD&apikey=demo"),
    ("coinbase_btcbrl_spot", "https://api.coinbase.com/v2/prices/BTC-BRL/spot"),
    ("awesomeapi_btcbrl", "https://economia.awesomeapi.com.br/last/BTC-BRL"),
    ("coingecko_btc_history_30d", "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=usd&days=30&interval=daily"),
    # --- Câmbio USD/BRL ---
    ("bcb_ptax_dia", "https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/CotacaoDolarDia(dataCotacao=@dataCotacao)?@dataCotacao='10-28-2026'&$format=json"),
    ("bcb_ptax_periodo", "https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/CotacaoDolarPeriodo(dataInicial=@dataInicial,dataFinalCotacao=@dataFinalCotacao)?@dataInicial='10-01-2026'&@dataFinalCotacao='10-29-2026'&$top=100&$format=json"),
    ("awesomeapi_usdbrl", "https://economia.awesomeapi.com.br/last/USD-BRL"),
    ("yahoo_usdbrl_chart", "https://query1.finance.yahoo.com/v8/finance/chart/BRL=X?range=5d&interval=1d"),
    ("frankfurter_usdbrl", "https://api.frankfurter.app/latest?from=USD&to=BRL"),
    ("brapi_currency_usdbrl", "https://brapi.dev/api/v2/currency?currency=USD-BRL"),
    ("awesomeapi_usdbrlptax", "https://economia.awesomeapi.com.br/last/USD-BRLPTAX"),
    ("alpha_fx_usdbrl_demo", "https://www.alphavantage.co/query?function=CURRENCY_EXCHANGE_RATE&from_currency=USD&to_currency=BRL&apikey=demo"),
]

HEADERS = {"User-Agent": "Mozilla/5.0 (portal-acoes-avaliacao-fontes/0.1)", "Accept": "application/json,text/csv,*/*"}

out = []
for name, url in TESTS:
    rec = {"test": name, "url": url, "requested_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
            body = r.read()
            rec["http_status"] = r.status
            rec["headers"] = {k: v for k, v in r.headers.items() if k.lower() in ("date", "content-type", "cache-control", "age", "x-ratelimit-remaining", "x-ratelimit-limit", "retry-after")}
    except urllib.error.HTTPError as e:
        body = e.read()
        rec["http_status"] = e.code
        rec["headers"] = {k: v for k, v in e.headers.items() if k.lower() in ("date", "content-type", "cache-control", "retry-after")}
    except Exception as e:
        body = b""
        rec["http_status"] = None
        rec["error"] = f"{type(e).__name__}: {e}"
    rec["elapsed_seconds"] = round(time.time() - t0, 3)
    txt = body.decode("utf-8", "replace")
    try:
        rec["payload"] = json.loads(txt)
    except Exception:
        rec["payload_text"] = txt[:4000]
    out.append(rec)
    print(name, rec.get("http_status"), rec["elapsed_seconds"], rec.get("error", ""), file=sys.stderr)

json.dump(out, open(sys.argv[1], "w"), ensure_ascii=False, indent=2)
