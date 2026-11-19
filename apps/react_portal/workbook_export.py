"""Application export: saved tracking inputs plus current read-only positions.

All formulas include computed caches. Text fields are never interpreted as formulas.
"""
from io import BytesIO
from decimal import Decimal
import xlsxwriter

def export(p):
    stream=BytesIO();book=xlsxwriter.Workbook(stream,{'in_memory':True,'strings_to_formulas':False,'strings_to_urls':False})
    title=book.add_format({'bold':True,'font_size':22,'font_color':'#173052'})
    head=book.add_format({'bold':True,'bg_color':'#173052','font_color':'white','text_wrap':True})
    plain=book.add_format({'font_color':'#34445C','font_size':11,'valign':'top'})
    money=book.add_format({'num_format':'#,##0.00;[Red]-#,##0.00','font_color':'#34445C'})
    qty=book.add_format({'num_format':'0.##########','font_color':'#34445C'})
    pct=book.add_format({'num_format':'0.00%;[Red]-0.00%'})
    sheets={}
    def sheet(name,headers):
        w=book.add_worksheet(name);sheets[name]=w;w.hide_gridlines(2);w.set_tab_color('#4269D8');w.set_column(0,len(headers)-1,23);w.set_column(0,0,19);w.set_row(0,36);w.merge_range(0,0,0,max(1,len(headers)-1),'ID3  /  '+p['owner']+'  /  '+name,title)
        w.merge_range(1,0,1,max(1,len(headers)-1),'Cópia da carteira e campos salvos no portal. Atualize e edite no portal; este arquivo não sincroniza edições de volta.',plain)
        w.set_row(1,32);w.freeze_panes(4,1);w.set_row(3,32)
        for c,h in enumerate(headers):w.write_string(3,c,h,head)
        return w
    def val(w,r,c,value,fmt=money):
        if value is None:w.write_blank(r,c,None,fmt)
        elif isinstance(value,(Decimal,int,float)):w.write_number(r,c,float(value),fmt)
        else:w.write_string(r,c,str(value),plain)
    w=sheet('Resumo',['Moeda','Investido','Valor de mercado','Resultado em aberto','Retorno','Proventos líquidos registrados','Posições cotadas'])
    for i,t in enumerate(p['totals'],4):
        for c,k in enumerate(['currency','invested','value','gain']):val(w,i,c,t[k])
        w.write_formula(i,4,f'=IF(B{i+1}>0,D{i+1}/B{i+1},"")',pct,float(t['returnPct']/100) if t['returnPct'] is not None else '')
        val(w,i,5,p['calculated']['received'][t['currency']]);val(w,i,6,f"{t['quoted']}/{t['count']}")
    w.write_string(8,0,'Resultados em aberto excluem taxas e impostos não informados. Proventos são somente os recebimentos confirmados nesta planilha.',plain)
    w=sheet('Carteira',['Ativo','Moeda','Quantidade','Preço médio','Cotação','Investido','Valor atual','Resultado','Retorno','Fonte','Horário da cotação'])
    for i,row in enumerate(p['positions'],4):
        for c,k in enumerate(['symbol','currency','quantity','averagePrice','price','costBasis','value']):val(w,i,c,row[k],qty if c==2 else money)
        w.write_formula(i,7,f'=IF(G{i+1}="","",G{i+1}-F{i+1})',money,float(row['totalGain']) if row['totalGain'] is not None else '')
        w.write_formula(i,8,f'=IF(OR(F{i+1}=0,H{i+1}=""),"",H{i+1}/F{i+1})',pct,float(row['totalReturn']/100) if row['totalReturn'] is not None else '')
        val(w,i,9,row.get('quoteSource') or 'Não disponível');val(w,i,10,row.get('quoteAt') or 'Não informado')
    if p['positions']:w.autofilter(3,0,3+len(p['positions']),10)
    w=sheet('Simulações',['Ativo','Moeda','Qtd adicional','Preço','Taxas','Qtd anterior','Custo anterior','Custo adicional','Qtd simulada','Custo simulado','PM simulado','Conferência'])
    for i,row in enumerate(p['calculated']['scenarios'],4):
        for c,k in enumerate(['symbol','currency','quantity','price','fees','previous_quantity','previous_cost']):val(w,i,c,Decimal(row[k]) if row[k] is not None and k not in ('symbol','currency') else row[k],qty if c in (2,5) else money)
        n=i+1
        for c,formula,key in [(7,f'=C{n}*D{n}+E{n}','purchase_cost'),(8,f'=C{n}+F{n}','new_quantity'),(9,f'=G{n}+H{n}','new_cost'),(10,f'=J{n}/I{n}','new_average')]:w.write_formula(i,c,formula,qty if c==8 else money,float(row[key]))
        val(w,i,11,row['check'])
    w=sheet('Radar',['Ativo','Moeda','Cotação','Orçamento','Quantidade máxima','Situação dos dados','Sinal observado','Fonte'])
    for i,row in enumerate(p['calculated']['radar'],4):
        for c,k in enumerate(['symbol','currency','price','budget','quantity','check','signal','source']):val(w,i,c,row.get(k),qty if c==4 else money)
    research=p.get('researchRadar')
    if research:
        i=6+len(p['calculated']['radar'])
        w.merge_range(i,0,i,7,'Radar de notícias · pesquisa de '+research['reviewedAt'],head)
        w.merge_range(i+1,0,i+2,7,research['notice'],book.add_format({'text_wrap':True,'valign':'top'}))
        for c,label in enumerate(['Empresa','Código EUA','Moeda','Notícia / fato','Publicado em','O que acompanhar / leitura','Riscos / leitura','Próximo evento / fontes']):w.write_string(i+3,c,label,head)
        for j,row in enumerate(research['entries'],i+4):
            w.set_row(j,135)
            text_format=book.add_format({'text_wrap':True,'valign':'top','font_color':'#34445C'})
            for c,key in enumerate(['name','symbol','currency','fact','publishedAt','watch','risk']):w.write_string(j,c,row[key],text_format)
            w.write_string(j,7,row['event']+'\n'+'\n'.join(src['label']+': '+src['url'] for src in row['sources']),text_format)
    w=sheet('Notas',['Anotações','Referência'])
    w.set_column(0,0,100);w.set_row(4,220);w.write_string(4,0,p['data']['notes'],book.add_format({'text_wrap':True,'valign':'top'}));val(w,4,1,str(p['generatedAt']))
    # Quantitative readings are separate from broker evidence and never forecasts.
    w=sheet('Sensibilidade',['Ativo','Moeda','Peso na moeda','Custo bruto por unidade','Cotação','Alta para custo bruto','Impacto de ±1%'])
    reading={r['symbol']:r for g in p.get('insights',[]) for r in g['rows']}
    end=4+len(p['positions'])
    for i,row in enumerate(p['positions'],4):
        n=i+1;r=reading.get(row['symbol'],{})
        val(w,i,0,row['symbol']);val(w,i,1,row['currency'])
        denom=f"SUMIF('Carteira'!$B$5:$B${end},B{n},'Carteira'!$G$5:$G${end})"
        missing=f"COUNTIFS('Carteira'!$B$5:$B${end},B{n},'Carteira'!$G$5:$G${end},\"\")"
        w.write_formula(i,2,f'=IF(OR({denom}=0,{missing}>0),"",\'Carteira\'!G{n}/{denom})',pct,float(r['weight']/100) if r.get('weight') is not None else '')
        w.write_formula(i,3,f'=IF(\'Carteira\'!C{n}=0,"",\'Carteira\'!F{n}/\'Carteira\'!C{n})',money,float(r['cost_price']) if r.get('cost_price') is not None else '')
        val(w,i,4,row['price'])
        w.write_formula(i,5,f'=IF(OR(E{n}="",E{n}<=0),"",MAX(0,D{n}/E{n}-1))',pct,float(r['recovery_percent']/100) if r.get('recovery_percent') is not None else '')
        w.write_formula(i,6,f'=IF(\'Carteira\'!G{n}="","",\'Carteira\'!G{n}/100)',money,float(r['impact_one_percent']) if r.get('impact_one_percent') is not None else '')
    w.write_string(end+2,0,'Cenário matemático com quantidades constantes; não inclui taxas/impostos nem indica compra ou venda.',plain)
    snap=p.get('brokerSnapshot')
    if snap:
        d=snap['data'];c=d['currency'];t=d['totals']
        if snap['detailed']:
            fields=['symbol','weight_percent','quantity','average','average_with_dividends','invested','price','value','gain','return_percent','daily_gain','daily_return_percent','dividends','blocked','available','d0']
            w=sheet('Conferencia BTG',['Ativo','Peso BTG','Quantidade','Preço médio','PM com proventos','Investido','Cotação do print','Valor do print','Resultado','Retorno','Dia BTG','Dia BTG (%)','Proventos','Bloqueada','Disponível','D+0'])
            for i,row in enumerate(d['rows'],4):
                for col,k in enumerate(fields):
                    v=row.get(k)
                    if k=='symbol':val(w,i,col,v)
                    elif v is not None:val(w,i,col,Decimal(v)/100 if k.endswith('percent') else Decimal(v),pct if k.endswith('percent') else qty if k in ('quantity','blocked','available','d0') else money)
            i=5+len(d['rows']);val(w,i,0,'Total BRL')
            for col,k in [(5,'invested'),(7,'value'),(8,'gain'),(10,'daily_gain'),(12,'dividends')]:val(w,i,col,Decimal(t[k]))
            w.autofilter(3,0,3+len(d['rows']),len(fields)-1)
        else:
            w=sheet('Conferencia BTG',['Moeda','Investido BTG','Valor BTG','Resultado','Retorno','Dia BTG','Dia BTG (%)','Proventos'])
            for col,k in enumerate(['currency','invested','value','gain','return_percent','daily_gain','daily_return_percent','dividends']):
                v=d['currency'] if k=='currency' else Decimal(t[k])/100 if k.endswith('percent') else Decimal(t[k])
                val(w,4,col,v,pct if k.endswith('percent') else money)
            i=6
        w.write_string(2,0,d['capture_window']+' · '+d['scope'],plain)
        val(w,i+2,0,'Investido no portal');val(w,i+2,1,snap['portal_cost']);val(w,i+3,0,'Diferença BTG − portal');val(w,i+3,1,snap['cost_difference'])
        w.write_string(i+5,0,'Referência fixa: as cotações do print não substituem os preços atuais da aba Carteira.',plain)
        for j,note in enumerate(d['notes'],i+6):w.write_string(j,0,note,plain)
    book.close();return stream.getvalue()
