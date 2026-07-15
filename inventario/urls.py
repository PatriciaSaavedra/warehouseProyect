from django.urls import path
from . import views

urlpatterns = [
    path('', views.inventario_view, name='inventario'),
    path('movimientos/', views.movimientos, name='movimientos'),
    path('kardex/<int:id>/', views.kardex, name='kardex'),
    path('entrada/', views.entrada_inventario, name='entrada_inventario'),
    path('nuevo/', views.nuevo_material, name='nuevo_material'),
    path('editar/<int:id>/', views.editar_material, name='editar_material'),
    path('eliminar/<int:id>/', views.eliminar_material, name='eliminar_material'),
    path('reporte/pdf/', views.reporte_inventario, name='reporte_inventario'),
    path('kardex/pdf/<int:id>/', views.kardex_pdf, name='kardex_pdf'),
    path('salida/', views.salida_inventario, name='salida_inventario'),
    path('saldo-inicial/<int:id>/', views.establecer_saldo_inicial, name='establecer_saldo_inicial'),
    path('baja/', views.registrar_baja, name='registrar_baja'),

    path('proveedores/', views.proveedores_list, name='proveedores_list'),
    path('proveedores/crear/', views.crear_proveedor, name='crear_proveedor'),
    path('proveedores/editar/<int:id>/', views.editar_proveedor, name='editar_proveedor'),
    path('reporte/consumo/', views.reporte_consumo_unidades, name='reporte_consumo_unidades'),
]