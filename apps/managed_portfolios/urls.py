from django.urls import path
from . import views

urlpatterns=[
    path('<slug:slug>/compras/registrar/',views.purchase,name='managed_purchase'),
    path('<slug:slug>/compras/previa.json',views.preview,name='managed_purchase_preview'),
]
