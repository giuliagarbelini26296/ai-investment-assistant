from django.urls import path

from . import views

urlpatterns = [
    path("alertas/", views.painel, name="alertas"),
    path("alertas/vistos/", views.marcar_vistos, name="alertas_vistos"),
    path("alertas/fragmento/", views.painel_fragmento, name="alertas_fragmento"),
    path("alertas/<int:pk>/silenciar/", views.silenciar, name="alerta_silenciar"),
    path("alertas/regras/<int:pk>/limite/", views.regra_limite, name="regra_limite"),
    path("alertas/regras/nova/", views.regra_nova, name="regra_nova"),
    path("alertas/regras/previa.json", views.previa_regra, name="regra_previa"),
    path("alertas/regras/<int:pk>/editar/", views.regra_editar, name="regra_editar"),
    path("alertas/regras/<int:pk>/desativar/", views.regra_desativar, name="regra_desativar"),
    path("alertas/regras/<int:pk>/reativar/", views.regra_reativar, name="regra_reativar"),
]
urlpatterns += [
    path("alertas/feed/", views.feed_mac, name="alertas_feed"),
    path("alertas/feed/confirmar/", views.feed_mac_confirmar, name="alertas_feed_confirmar"),
]
