# FILE: presupuestos/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path('', views.poa_list, name='poa_list'),
    path('crear/', views.crear_poa, name='crear_poa'),
    path('editar/<int:id>/', views.editar_poa, name='editar_poa'),
    path('reporte-ejecucion/', views.reporte_presupuestos, name='reporte_presupuestos'),
    path('registrar-modificacion/<int:poa_id>/', views.registrar_modificacion, name='registrar_modificacion'), 
]