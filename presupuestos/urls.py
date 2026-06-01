from django.urls import path
from . import views

urlpatterns = [
    path('', views.presupuestos_view, name='presupuestos'),
]