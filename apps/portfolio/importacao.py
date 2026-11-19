"""
Importação de compras por CSV, em duas etapas: pré-visualização (nada gravado) → confirmação.

Formatos reconhecidos pelo cabeçalho:
- Genérico (pt-BR): simbolo, data, quantidade, preco, [moeda], [cambio], [mercado], [custodia], [observacao]
  data em AAAA-MM-DD ou DD/MM/AAAA; números com vírgula ou ponto.
- Extrato de ordens da Binance (Trade History): Date(UTC), Pair, Side, Price, Executed, Amount, Fee.
  Só ordens BUY viram compras; SELL é listada como ignorada (o portal não registra vendas).
  Pares em USDT/USDC/BUSD → preço em USD (USDT ≈ USD, rotulado); pares em BRL → preço em BRL
  (ativo deve ter moeda nativa BRL, o que não ocorre para cripto → linha com erro).

Ativos: se não existir, pode ser criado na confirmação quando a linha informa mercado (ou é Binance → Cripto).
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django.db import transaction

from . import services
from .models import MARKET_CURRENCY, Asset, Custody, Market

COLUNAS_GENERICO = {"simbolo", "data", "quantidade", "preco"}
COLUNAS_BINANCE = {"Date(UTC)", "Pair", "Side", "Price", "Executed", "Amount"}
MOEDAS_COTACAO_USD = ("USDT", "USDC", "BUSD", "FDUSD", "USD")
MAX_LINHAS = 2000


@dataclass
class LinhaImportada:
    n: int
    simbolo: str = ""
    mercado: str = ""
    custodia: str = Custody.OTHER
    data: dt.date | None = None
    quantidade: Decimal | None = None
    preco: Decimal | None = None
    moeda: str = ""
    cambio: Decimal | None = None
    observacao: str = ""
    ativo_existente: bool = False
    criar_ativo: bool = False
    erro: str = ""
    ignorada: str = ""

    @property
    def valida(self) -> bool:
        return not self.erro and not self.ignorada


@dataclass
class Previa:
    formato: str
    linhas: list[LinhaImportada] = field(default_factory=list)
    erro_geral: str = ""

    @property
    def validas(self) -> list[LinhaImportada]:
        return [li for li in self.linhas if li.valida]

    @property
    def com_erro(self) -> list[LinhaImportada]:
        return [li for li in self.linhas if li.erro]

    @property
    def ignoradas(self) -> list[LinhaImportada]:
        return [li for li in self.linhas if li.ignorada]


def _numero(texto: str) -> Decimal:
    s = re.sub(r"[^\d,.\-]", "", texto or "")
    if not s:
        raise InvalidOperation
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".") if s.count(",") == 1 else s.replace(",", "")
    return Decimal(s)


def _data(texto: str) -> dt.date:
    t = (texto or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            return dt.datetime.strptime(t[: len(fmt) + 4], fmt).date()
        except ValueError:
            continue
    raise ValueError(f"data inválida: {t!r}")


def _ler_csv(conteudo: bytes) -> tuple[list[str], list[dict]]:
    texto = conteudo.decode("utf-8-sig", errors="replace")
    amostra = texto[:2048]
    try:
        dialeto = csv.Sniffer().sniff(amostra, delimiters=",;\t")
    except csv.Error:
        dialeto = csv.excel
    leitor = csv.DictReader(io.StringIO(texto), dialect=dialeto)
    campos = [c.strip() for c in (leitor.fieldnames or [])]
    linhas = []
    for reg in leitor:
        linhas.append({(k or "").strip(): (v or "").strip() for k, v in reg.items() if k is not None})
        if len(linhas) > MAX_LINHAS:
            break
    return campos, linhas


def detectar_formato(campos: list[str]) -> str | None:
    nomes = {c.lower() for c in campos}
    if COLUNAS_GENERICO <= nomes:
        return "generico"
    if COLUNAS_BINANCE <= set(campos):
        return "binance"
    return None


def _separar_par(par: str) -> tuple[str, str] | None:
    par = par.replace("/", "").upper()
    for moeda in MOEDAS_COTACAO_USD + ("BRL", "BTC", "ETH", "EUR"):
        if par.endswith(moeda) and len(par) > len(moeda):
            return par[: -len(moeda)], moeda
    return None


def _quantidade_com_sufixo(texto: str) -> tuple[Decimal, str]:
    """'0.00123BTC' → (0.00123, 'BTC'); '73.5USDT' → (73.5, 'USDT')."""
    m = re.match(r"^\s*([\d.,]+)\s*([A-Za-z]*)\s*$", texto or "")
    if not m:
        raise InvalidOperation
    return _numero(m.group(1)), m.group(2).upper()


def pre_visualizar(conteudo: bytes, custodia_padrao: str = Custody.OTHER) -> Previa:
    campos, registros = _ler_csv(conteudo)
    formato = detectar_formato(campos)
    if formato is None:
        return Previa(
            formato="desconhecido",
            erro_geral=(
                "Cabeçalho não reconhecido. Use as colunas simbolo, data, quantidade, preco (genérico) "
                "ou o extrato de ordens da Binance (Date(UTC), Pair, Side, Price, Executed, Amount)."
            ),
        )
    previa = Previa(formato=formato)
    existentes = {a.symbol.upper(): a for a in Asset.objects.all()}
    for i, reg in enumerate(registros, start=2):
        li = LinhaImportada(n=i)
        try:
            if formato == "generico":
                reg = {k.lower(): v for k, v in reg.items()}
                li.simbolo = reg.get("simbolo", "").upper()
                li.data = _data(reg.get("data", ""))
                li.quantidade = _numero(reg.get("quantidade", ""))
                li.preco = _numero(reg.get("preco", ""))
                li.moeda = (reg.get("moeda") or "").upper()
                li.cambio = _numero(reg["cambio"]) if reg.get("cambio") else None
                li.mercado = (reg.get("mercado") or "").upper()
                li.custodia = (reg.get("custodia") or custodia_padrao).upper()
                li.observacao = reg.get("observacao", "")[:200]
            else:
                lado = reg.get("Side", "").upper()
                if lado != "BUY":
                    li.ignorada = f"ordem {lado or '?'} não é compra"
                    previa.linhas.append(li)
                    continue
                par = _separar_par(reg.get("Pair", ""))
                if par is None:
                    raise ValueError(f"par não reconhecido: {reg.get('Pair')!r}")
                li.simbolo, moeda_cotacao = par
                li.data = _data(reg.get("Date(UTC)", ""))
                li.quantidade, suf = _quantidade_com_sufixo(reg.get("Executed", ""))
                if suf and suf != li.simbolo:
                    raise ValueError(f"quantidade em {suf}, esperado {li.simbolo}")
                li.preco = _numero(reg.get("Price", ""))
                li.moeda = "USD" if moeda_cotacao in MOEDAS_COTACAO_USD else moeda_cotacao
                if moeda_cotacao in MOEDAS_COTACAO_USD and moeda_cotacao != "USD":
                    li.observacao = f"preço em {moeda_cotacao} (tratado como USD)"
                li.mercado = Market.CRYPTO
                li.custodia = Custody.BINANCE
                taxa = reg.get("Fee", "")
                try:
                    taxa_valor = _quantidade_com_sufixo(taxa)[0] if taxa else Decimal(0)
                except InvalidOperation:
                    taxa_valor = Decimal(0)
                if taxa_valor > 0:
                    li.observacao = (li.observacao + f"; taxa {taxa} (não entra no preço)").strip("; ")
            if li.quantidade is None or li.quantidade <= 0:
                raise ValueError("quantidade deve ser positiva")
            if li.preco is None or li.preco <= 0:
                raise ValueError("preço deve ser positivo")
            if li.cambio is not None and li.cambio <= 0:
                raise ValueError("câmbio deve ser positivo")
            if li.data > dt.date.today():
                raise ValueError("data no futuro")
            ativo = existentes.get(li.simbolo)
            if ativo:
                li.ativo_existente = True
                li.mercado = ativo.market
                if li.moeda and li.moeda != ativo.native_currency:
                    raise ValueError(f"moeda {li.moeda} ≠ moeda nativa do ativo ({ativo.native_currency})")
                li.moeda = ativo.native_currency
                if ativo.market == Market.B3 and li.quantidade != li.quantidade.to_integral_value():
                    raise ValueError("B3 exige quantidade inteira")
            else:
                if li.mercado not in Market.values:
                    raise ValueError(
                        "ativo não cadastrado; informe a coluna mercado (NYSE, NASDAQ, B3, CRIPTO)"
                    )
                if li.custodia not in Custody.values:
                    li.custodia = Custody.OTHER
                moeda_mercado = MARKET_CURRENCY[li.mercado]
                if li.moeda and li.moeda != moeda_mercado:
                    raise ValueError(f"moeda {li.moeda} ≠ moeda do mercado {li.mercado} ({moeda_mercado})")
                li.moeda = moeda_mercado
                if li.mercado == Market.B3 and li.quantidade != li.quantidade.to_integral_value():
                    raise ValueError("B3 exige quantidade inteira")
                li.criar_ativo = True
        except (InvalidOperation, ValueError, KeyError) as exc:
            li.erro = str(exc) if str(exc) else "valor inválido"
        previa.linhas.append(li)
    return previa


def serializar(previa: Previa) -> dict:
    """Guarda a pré-visualização na sessão (só linhas válidas)."""
    return {
        "formato": previa.formato,
        "linhas": [
            {
                "simbolo": li.simbolo,
                "mercado": li.mercado,
                "custodia": li.custodia,
                "data": li.data.isoformat(),
                "quantidade": str(li.quantidade),
                "preco": str(li.preco),
                "cambio": str(li.cambio) if li.cambio is not None else None,
                "observacao": li.observacao,
                "criar_ativo": li.criar_ativo,
            }
            for li in previa.validas
        ],
    }


@transaction.atomic
def confirmar(dados: dict, user) -> tuple[int, int]:
    """Grava as compras (e cria ativos quando indicado). Devolve (compras criadas, ativos criados)."""
    compras = ativos = 0
    for d in dados.get("linhas", []):
        asset = Asset.objects.filter(symbol=d["simbolo"]).first()
        if asset is None:
            if not d.get("criar_ativo"):
                continue
            asset = Asset.objects.create(
                symbol=d["simbolo"], name=d["simbolo"], market=d["mercado"], custody=d["custodia"]
            )
            ativos += 1
        elif asset.custody in (Custody.NONE, Custody.OTHER) and d.get("custodia") in (
            Custody.AVENUE,
            Custody.BTG,
            Custody.BINANCE,
        ):
            # ativo criado para acompanhamento ou com custódia genérica: a compra informa a corretora real
            asset.custody = d["custodia"]
            asset.save(update_fields=["custody", "updated_at"])
        extras = {}
        if d.get("cambio") and asset.native_currency == "USD":
            extras["fx_rate"] = Decimal(d["cambio"])
        services.criar_lote(
            asset,
            user,
            trade_date=dt.date.fromisoformat(d["data"]),
            quantity=Decimal(d["quantidade"]),
            unit_price=Decimal(d["preco"]),
            notes=(d.get("observacao") or "importado por CSV")[:200],
            **extras,
        )
        compras += 1
    return compras, ativos
