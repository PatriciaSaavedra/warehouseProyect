# FILE: presupuestos/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path('', views.poa_list, name='poa_list'),
    path('crear/', views.crear_poa, name='crear_poa'),
    path('editar/<int:id>/', views.editar_poa, name='editar_poa'),
]