from django.urls import path

from . import views
from . import quick_purchase, sales

urlpatterns = [
    path("vendas/registrar/", sales.sale_page, name="record_sale"),
    path('compras/registrar/', quick_purchase.purchase_page, name='quick_purchase'),
    path('compras/previa.json', quick_purchase.purchase_preview, name='purchase_preview'),
    path("ativos/", views.asset_list, name="asset_list"),
    path("ativos/novo/", views.asset_create, name="asset_create"),
    path("ativos/importar/", views.importar_compras, name="importar_compras"),
    path("ativos/<int:pk>/", views.asset_detail, name="asset_detail"),
    path("ativos/<int:pk>/historico.json", views.historico_json, name="historico_json"),
    path("ativos/<int:pk>/painel/", views.asset_painel, name="asset_painel"),
    path("comparar/", views.comparar, name="comparar"),
    path("ativos/<int:pk>/editar/", views.asset_update, name="asset_update"),
    path("ativos/<int:pk>/compras/nova/", views.lot_create, name="lot_create"),
    path("compras/<int:pk>/editar/", views.lot_update, name="lot_update"),
    path("compras/<int:pk>/cancelar/", views.lot_cancel, name="lot_cancel"),
]
