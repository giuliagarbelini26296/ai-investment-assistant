from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='planning'),
    path('agenda.ics', views.calendar_export, name='planning_calendar'),
    path('lancamentos/<uuid:key>/cancelar/', views.cancel_entry, name='planning_cancel'),
]
