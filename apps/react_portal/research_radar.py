"""Dated editorial research, independent of holdings, orders and quote feeds."""
RESEARCH_RADAR = {
    'reviewedAt': '2026-11-12',
    'notice': 'Pesquisa pontual de notícias oficiais em 12/11/2026. Não é um feed automático, recomendação de compra ou previsão de retorno. As leituras e riscos são nossa interpretação para acompanhamento.',
    'entries': [
        {
            'symbol': 'GOOGL', 'name': 'Google / Alphabet', 'currency': 'USD',
            'publishedAt': '2026-10-28',
            'fact': 'No 3T26, a receita cresceu 25% e o Google Cloud cresceu 103% em relação ao mesmo trimestre do ano anterior.',
            'watch': 'Acompanhar se a expansão de Cloud e IA se traduz em lucro e caixa. Comparar a recuperação da cotação com resultados, sem presumir reversão da queda.',
            'risk': 'O lucro por ação caiu de US$ 2,87 para US$ 2,12. Crescimento de receita não garante valorização; considerar custos de IA e pressão competitiva.',
            'event': 'Próximo balanço: data não confirmada nesta pesquisa.',
            'sources': [{'label': 'Resultado oficial · SEC', 'url': 'https://www.sec.gov/Archives/edgar/data/1652044/000165204426000080/googexhibit991q32026.htm'}],
        },
        {
            'symbol': 'CRM', 'name': 'Salesforce', 'currency': 'USD',
            'publishedAt': '2026-11-10',
            'fact': 'Balanço do 3T fiscal de 2027 confirmado para 02/12/2026 após o fechamento nos EUA. No 2T fiscal, a receita cresceu 11%.',
            'watch': 'Acompanhar o crescimento orgânico, a adoção de Agentforce e as projeções do próximo balanço; conferir cotação e liquidez antes de simular uma entrada.',
            'risk': 'O resultado pode trazer oscilações fortes. Parte do crescimento inclui Informatica; adoção de IA não assegura retorno ao acionista.',
            'event': '02/12/2026 · após o fechamento dos EUA · confirmado pela empresa.',
            'sources': [
                {'label': 'Agenda oficial · 10/11', 'url': 'https://investor.salesforce.com/news/news-details/2026/Salesforce-Announces-Date-of-Third-Quarter-Fiscal-2027-Earnings-Release-and-Webcast/default.aspx'},
                {'label': 'Resultado anterior · 26/08', 'url': 'https://investor.salesforce.com/news/news-details/2026/Salesforce-Delivers-Record-Second-Quarter-Fiscal-2027-Results/default.aspx'},
            ],
        },
        {
            'symbol': 'AMZN', 'name': 'Amazon', 'currency': 'USD',
            'publishedAt': '2026-10-29',
            'fact': 'No 3T26, a receita cresceu 12%, a AWS cresceu 39,3% e a margem operacional atingiu 13,9%.',
            'watch': 'Comparar a expansão da AWS e a temporada de vendas do 4T com geração de caixa e investimentos. Avaliar o preço frente aos resultados, não apenas o valor de uma ação.',
            'risk': 'Investimentos elevados podem consumir caixa. O desempenho do varejo e as expectativas já embutidas no preço podem limitar a valorização.',
            'event': 'Acompanhar o 4T26; próxima data de balanço não confirmada nesta pesquisa.',
            'sources': [{'label': 'Resultado oficial · Amazon', 'url': 'https://ir.aboutamazon.com/news-release/news-release-details/2026/Amazon-com-Announces-Third-Quarter-Results/default.aspx'}],
        },
    ],
}
