"""Filtros de apresentação pt-BR. Arredondamento só aqui; o cálculo interno é Decimal exato."""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django import template

register = template.Library()

SIMBOLO = {"USD": "US$", "BRL": "R$", "USDT": "USDT"}


@register.simple_tag
def totais_tabela(linhas):
    from apps.portfolio.services import totais_das_linhas

    return totais_das_linhas(linhas)


def _ptbr(valor: Decimal, casas: int) -> str:
    q = Decimal(1).scaleb(-casas) if casas else Decimal(1)
    texto = f"{valor.quantize(q, rounding=ROUND_HALF_UP):,.{casas}f}"
    return texto.replace(",", "\u0000").replace(".", ",").replace("\u0000", ".")


@register.filter
def moeda(valor, codigo="BRL"):
    """Decimal → 'US$ 1.234,56'. None → 'indisponível'."""
    if valor is None or valor == "":
        return "indisponível"
    try:
        d = Decimal(str(valor))
    except InvalidOperation:
        return "indisponível"
    sinal = "-" if d < 0 else ""
    return f"{sinal}{SIMBOLO.get(codigo, codigo)} {_ptbr(abs(d), 2)}"


@register.filter
def pct(valor, casas=2):
    """Decimal → '+9,09%' / '-3,10%'. None → 'indisponível'."""
    if valor is None or valor == "":
        return "indisponível"
    d = Decimal(str(valor))
    sinal = "+" if d > 0 else ""
    return f"{sinal}{_ptbr(d, int(casas))}%"


@register.filter
def normalize_decimal(valor):
    """Remove zeros à direita: 10.0000000000 → '10'; 0.12500000 → '0,125'."""
    if valor is None:
        return "—"
    d = Decimal(str(valor)).normalize()
    if d == d.to_integral_value():
        return _ptbr(d, 0)
    texto = format(d, "f")
    inteiro, _, frac = texto.partition(".")
    return f"{_ptbr(Decimal(inteiro), 0)},{frac}"


@register.filter
def multiplicar(valor, fator):
    """Decimal × Decimal (para mostrar o equivalente na outra moeda). None → None."""
    if valor is None or fator is None:
        return None
    try:
        return Decimal(str(valor)) * Decimal(str(fator))
    except InvalidOperation:
        return None


@register.filter
def safe_json(valor):
    """Serializa para JSON seguro dentro de <script type="application/json">."""
    import json

    from django.utils.safestring import mark_safe

    texto = json.dumps(valor, ensure_ascii=False, default=str)
    return mark_safe(texto.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


@register.filter
def split(valor, sep=","):
    return str(valor).split(sep)


@register.filter
def bruto(valor):
    """Número cru para atributos data-* (ponto decimal, sem separador de milhar). None → ''."""
    if valor is None or valor == "":
        return ""
    try:
        return format(Decimal(str(valor)).normalize(), "f")
    except InvalidOperation:
        return ""


@register.filter
def preco(valor, codigo="USD"):
    """Preço com casas conforme a grandeza (cripto barata não vira 'US$ 0,00'). None → 'indisponível'."""
    if valor is None or valor == "":
        return "indisponível"
    try:
        d = Decimal(str(valor))
    except InvalidOperation:
        return "indisponível"
    casas = 2 if abs(d) >= 1 else 4 if abs(d) >= Decimal("0.01") else 6
    sinal = "-" if d < 0 else ""
    return f"{sinal}{SIMBOLO.get(codigo, codigo)} {_ptbr(abs(d), casas)}"


@register.filter
def numero(valor, casas=1):
    """Float/Decimal → '1.234,5'. None → '—'."""
    if valor is None or valor == "":
        return "—"
    try:
        return _ptbr(Decimal(str(valor)), int(casas))
    except InvalidOperation:
        return "—"


@register.filter
def compacto(valor):
    """Volume/valor grande → '12,3 mi' / '4,5 bi' / '980 mil'. None → '—'."""
    if valor is None or valor == "":
        return "—"
    try:
        d = Decimal(str(valor))
    except InvalidOperation:
        return "—"
    for limite, sufixo in (
        (Decimal("1e12"), "tri"),
        (Decimal("1e9"), "bi"),
        (Decimal("1e6"), "mi"),
        (Decimal("1e3"), "mil"),
    ):
        if abs(d) >= limite:
            return f"{_ptbr(d / limite, 1)} {sufixo}"
    return _ptbr(d, 0)


@register.filter
def sinal_classe(valor):
    """'alta' / 'queda' / '' para colorir números."""
    try:
        d = Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError):
        return ""
    return "alta" if d > 0 else "queda" if d < 0 else ""


@register.filter
def obter(dicionario, chave):
    """{{ d|obter:k }} — acesso a dicionário por variável no template."""
    try:
        return dicionario.get(chave)
    except AttributeError:
        return None


@register.filter
def obter_variacao(leitura, periodo):
    """{{ x|obter_variacao:'1m' }} — variação de uma leitura de mercado no período."""
    try:
        return leitura.variacao(periodo)
    except AttributeError:
        return None


@register.filter
def obter_attr(obj, nome):
    """{{ obj|obter_attr:'campo' }} — atributo por variável (None se ausente)."""
    if obj is None:
        return None
    return getattr(obj, str(nome), None)
