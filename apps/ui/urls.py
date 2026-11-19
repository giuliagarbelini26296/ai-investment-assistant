from django.urls import path

from . import views

urlpatterns = [
    path("classico/", views.home, name="home_classico"),
    path("moeda/", views.trocar_moeda, name="trocar_moeda"),
    path("carteira/fragmento/", views.carteira_fragmento, name="carteira_fragmento"),
    path("carteira/evolucao.json", views.evolucao_json, name="evolucao_json"),
    path("coletar-agora/", views.coletar_agora, name="coletar_agora"),
    path("sobre-os-dados/", views.sobre_dados, name="sobre_dados"),
    path("saude/", views.health, name="health"),
]
