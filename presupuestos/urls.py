# FILE: presupuestos/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path('', views.poa_list, name='poa_list'),
    path('crear/', views.crear_poa, name='crear_poa'),
    path('editar/<int:id>/', views.editar_poa, name='editar_poa'),
    path('reporte-ejecucion/', views.reporte_presupuestos, name='reporte_presupuestos'),
    path('registrar-modificacion/<int:poa_id>/', views.registrar_modificacion, name='registrar_modificacion'), 
    path('traspaso/', views.traspaso_entre_partidas, name='traspaso_entre_partidas'),
    path('api/partidas-unidad/<int:unidad_id>/', views.api_partidas_poa_unidad, name='api_partidas_poa_unidad'),
    path('api/materiales-partida/<int:partida_id>/', views.api_materiales_por_partida, name='api_materiales_por_partida'),
    path('detalle/<int:id>/', views.detalle_poa, name='detalle_poa'),
]