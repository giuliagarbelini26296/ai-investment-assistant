from django.urls import path

from . import views

app_name = "noticias"
urlpatterns = [
    path("", views.index, name="index"),
    path("atualizar/", views.refresh, name="refresh"),
]
