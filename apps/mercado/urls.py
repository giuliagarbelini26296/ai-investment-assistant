from django.urls import path

from . import views

urlpatterns = [
    path("mercado/", views.visao_geral, name="mercado"),
    path("mercado/rankings/", views.rankings, name="mercado_rankings"),
    path("mercado/oportunidades/", views.oportunidades, name="mercado_oportunidades"),
    path("mercado/dolar/", views.painel_dolar, name="mercado_dolar"),
    path("mercado/cripto/", views.criptomoedas, name="mercado_cripto"),
    path("mercado/eua/", views.acoes_eua, name="mercado_eua"),
    path("mercado/brasil/", views.acoes_brasil, name="mercado_brasil"),
    path("mercado/comparar/", views.comparar_mercados, name="mercado_comparar"),
    path("mercado/iniciais/<str:mercado>/", views.semear_iniciais, name="mercado_iniciais"),
    path("mercado/universo/", views.universo_lista, name="mercado_universo"),
    path("mercado/universo/semear/", views.semear, name="mercado_semear"),
    path("mercado/universo/adicionar/", views.universo_adicionar, name="mercado_universo_adicionar"),
    path("mercado/universo/<int:pk>/remover/", views.universo_remover, name="mercado_universo_remover"),
    path("mercado/atualizar/", views.atualizar_leituras, name="mercado_atualizar"),
    path("favoritos/", views.favoritos, name="favoritos"),
    path("favoritos/adicionar/", views.adicionar_favorito, name="favorito_adicionar"),
    path("favoritos/alternar/<int:pk>/", views.alternar_favorito, name="favorito_alternar"),
    path("favoritos/grupos/novo/", views.grupo_criar, name="favorito_grupo_criar"),
    path("favoritos/grupos/<int:pk>/excluir/", views.grupo_excluir, name="favorito_grupo_excluir"),
    path("favoritos/<int:pk>/grupo/", views.favorito_grupo, name="favorito_grupo"),
    path("favoritos/<int:pk>/mover/", views.favorito_mover, name="favorito_mover"),
]
