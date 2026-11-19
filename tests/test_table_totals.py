"""Totais ponderados, moedas distintas e rodapés nas tabelas de posições."""
from decimal import Decimal as D

import pytest
from django.urls import reverse

from apps.portfolio.models import Asset
from apps.portfolio.services import LinhaCarteira, totais_das_linhas
from apps.valuation import core, fx

from .test_custody_filters import positions  # noqa: F401


def linha(symbol, currency, invested, current, previous=None):
    asset = Asset(symbol=symbol, market='B3' if currency == 'BRL' else 'NASDAQ', native_currency=currency)
    pos = core.posicao([core.Lote(D(1), D(invested))])
    aval = core.avaliar(pos, D(current), D(previous) if previous else None) if current else None
    return LinhaCarteira(asset=asset, posicao=pos, avaliacao=aval)


def test_retorno_total_ponderado_e_variacao_diaria():
    rows = [linha('A', 'BRL', '100', '110', '100'), linha('B', 'BRL', '900', '945', '900')]
    total = totais_das_linhas(rows)[0]
    assert total['resultado'] == D('55')
    assert total['investido'] == D('1000')
    assert total['valor'] == D('1055')
    assert total['retorno'] == D('5.5')
    assert total['dia'] == D('55')
    assert total['dia_pct'] == D('5.5')


def test_sem_base_cambial_mantem_totais_separados():
    br = linha('BR', 'BRL', '100', '110')
    us = linha('US', 'USD', '200', '180')
    us.sem_base_cambial = True
    total = {t['moeda']: t for t in totais_das_linhas([br, us])}
    assert total['BRL']['resultado'] == D('10')
    assert total['BRL']['retorno'] == D('10')
    assert total['USD']['resultado'] == D('-20')
    assert total['USD']['retorno'] == D('-10')
    assert us.resultado_visao is None


def test_cotacao_ausente_nao_dilui_percentual_e_cobertura_e_parcial():
    rows = [linha('A', 'BRL', '100', '110'), linha('B', 'BRL', '900', None)]
    total = totais_das_linhas(rows)[0]
    assert total['posicoes'] == 2 and total['avaliadas'] == 1
    assert total['investido'] == D('1000')
    assert total['investido_avaliado'] == D('100')
    assert total['retorno'] == D('10')
    assert total['dia'] is None and total['dia_pct'] is None
    missing = totais_das_linhas([rows[1]])[0]
    assert missing['resultado'] is None and missing['retorno'] is None and missing['valor'] is None


def test_conversao_com_base_real_e_incluida_na_moeda_da_visao():
    us = linha('US', 'USD', '100', '110', '100')
    us.conversao = fx.converter([fx.LoteComCambio(core.Lote(D('1'), D('100')), D('5'))],
        us.posicao, us.avaliacao, 'USD', 'BRL', D('6'))
    total = totais_das_linhas([us, linha('BR', 'BRL', '100', '110', '100')])[0]
    assert total['moeda'] == 'BRL'
    assert total['investido'] == D('600')
    assert total['valor'] == D('770')
    assert total['resultado'] == D('170')
    assert total['dia'].quantize(D('.01')) == D('70')


def test_sem_posicao_nao_entra_nos_totais():
    asset = Asset(symbol='VAZIO', native_currency='BRL')
    assert totais_das_linhas([LinhaCarteira(asset=asset, posicao=None, avaliacao=None)]) == []


@pytest.mark.parametrize('name', ['home_classico', 'carteira_fragmento', 'asset_list', 'comparar'])
def test_tabelas_de_posicoes_tem_totais_e_preco_medio(cliente_logado, positions, name):
    params = {'ativos': [str(a.pk) for a in positions]} if name == 'comparar' else {}
    response = cliente_logado.get(reverse(name), params)
    assert response.status_code == 200
    html = response.content.decode()
    assert '<tfoot' in html
    assert 'Total em BRL' in html or 'Total dos selecionados em BRL' in html
    assert 'Total em USD' in html or 'Total dos selecionados em USD' in html
    if name in ['home_classico', 'carteira_fragmento']:
        assert '<th class="num" data-sort="num">Preço médio</th>' in html
        assert 'data-total-resultado="-39.16"' in html
        assert 'data-total-resultado="0.48"' in html


def test_lista_sem_posicao_oculta_colunas_da_carteira(cliente_logado, db):
    asset = Asset.objects.create(symbol='WATCH', name='Somente acompanhamento', market='B3', custody='NENHUMA')
    html = cliente_logado.get(reverse('asset_list')).content.decode()
    assert 'WATCH' in html
    assert '>Preço médio</th>' not in html
    assert '>Quantidade</th>' not in html
    assert '>Resultado</th>' not in html
    assert '>Retorno</th>' not in html
    assert '<tfoot' not in html
    assert asset.lots.count() == 0


def test_comparacao_sem_posicoes_oculta_indicadores_da_carteira(cliente_logado, db):
    assets = [Asset.objects.create(symbol=s, name=s, market='B3', custody='NENHUMA') for s in ['WATCH1', 'WATCH2']]
    html = cliente_logado.get(reverse('comparar'), {'ativos': [a.pk for a in assets]}).content.decode()
    assert 'WATCH1' in html and 'WATCH2' in html
    assert '<td>Preço médio (nativo)</td>' not in html
    assert '<td>Investido (' not in html
    assert '<td>Resultado (' not in html
    assert '<tfoot' not in html


def test_posicoes_sem_base_cambial_preservam_colunas_e_moeda(cliente_logado, positions):
    import re

    html = cliente_logado.get(reverse('carteira_fragmento')).content.decode()
    row = re.search(r'<tr class="linha-ativo"[^>]+data-simbolo="TTWO".*?</tr>', html, re.S).group()
    assert 'colspan="4"' not in row
    assert 'Moeda original' in row
    assert 'US$ 389,98' in row
    assert '-US$ 39,16' in row
    assert len(re.findall(r'<td(?:\s|>)', row)) == 12
