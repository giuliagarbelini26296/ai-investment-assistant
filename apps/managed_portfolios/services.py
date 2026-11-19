from decimal import Decimal as D
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.valuation import core
from .models import Balance, Portfolio, Purchase


def latest_balance(portfolio, asset):
    return Balance.objects.filter(portfolio=portfolio, asset=asset).first()


def position(portfolio, asset):
    balance = latest_balance(portfolio, asset)
    purchases = list(Purchase.objects.filter(portfolio=portfolio, asset=asset).order_by('pk'))
    covered = set(balance.covered_purchases) if balance else set()
    purchases = [p for p in purchases if p.pk not in covered]
    currencies = {p.currency for p in purchases}
    if balance:
        currencies.add(balance.currency)
    if len(currencies) > 1:
        raise ValidationError('Moedas diferentes para a mesma posição. Concilie antes de continuar.')
    quantity = (balance.quantity if balance else D(0)) + sum((p.quantity for p in purchases), D(0))
    cost = (balance.invested_cost if balance else D(0)) + sum((p.quantity * p.unit_price for p in purchases), D(0))
    pos = core.Posicao(quantity, cost, cost / quantity, len(purchases)) if quantity else None
    return pos, next(iter(currencies), asset.native_currency), balance


@transaction.atomic
def reconcile(portfolio, asset, **values):
    portfolio = Portfolio.objects.select_for_update().get(pk=portfolio.pk)
    existing = Balance.objects.filter(portfolio=portfolio, asset=asset, source_key=values['source_key']).first()
    if existing:
        if any(getattr(existing, key) != val for key, val in values.items()):
            raise ValidationError('Fonte já conciliada com outros dados.')
        return existing, False
    balance = Balance(portfolio=portfolio, asset=asset,
        covered_purchases=list(Purchase.objects.filter(portfolio=portfolio, asset=asset).values_list('pk', flat=True)), **values)
    balance.full_clean()
    balance.save()
    return balance, True


def purchase_preview(portfolio, asset, currency, quantity, price, trade_date):
    before, current_currency, balance = position(portfolio, asset)
    if before and current_currency != currency:
        raise ValidationError('Use a mesma moeda da posição existente.')
    if balance and trade_date < balance.reference_date:
        raise ValidationError('A compra é anterior ao saldo conciliado e pode já estar incluída nele.')
    after = core.adicionar_compra(before, core.Lote(quantity, price))
    return {'symbol': asset.symbol, 'currency': currency, 'broker':f'Carteira {portfolio.name}',
        'portfolio': portfolio.slug,
        'before': {'quantity':before.quantidade if before else D(0),
                   'averagePrice':before.preco_medio if before else None, 'cost':before.investido if before else D(0)},
        'purchase': {'quantity':quantity, 'unitPrice':price, 'date':trade_date, 'total':quantity*price},
        'after': {'quantity':after.quantidade, 'averagePrice':after.preco_medio, 'cost':after.investido}}


@transaction.atomic
def record_purchase(portfolio, asset, user, *, quantity, unit_price, currency, trade_date, submission_key, reviewed_before):
    portfolio = Portfolio.objects.select_for_update().get(pk=portfolio.pk)
    existing = Purchase.objects.filter(submission_key=submission_key).first()
    if existing:
        if (existing.portfolio_id, existing.asset_id, existing.created_by_id, existing.quantity, existing.unit_price,
            existing.currency, existing.trade_date) != (portfolio.pk, asset.pk, user.pk, quantity, unit_price, currency, trade_date):
            raise ValidationError('Este registro já foi usado com outros dados ou em outra carteira.')
        return existing, False
    preview = purchase_preview(portfolio, asset, currency, quantity, unit_price, trade_date)
    current = [format(preview['before'][key].normalize(), 'f') for key in ('quantity', 'cost')]
    if current != reviewed_before:
        raise ValidationError('A posição mudou desde a conferência. Confira novamente antes de registrar.')
    purchase = Purchase(portfolio=portfolio, asset=asset, created_by=user, quantity=quantity,
        unit_price=unit_price, currency=currency, trade_date=trade_date, submission_key=submission_key)
    purchase.full_clean()
    purchase.save()
    return purchase, True
