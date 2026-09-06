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

    path('almacenes/', views.almacen_list, name='almacen_list'),
    path('almacenes/nuevo/', views.crear_almacen, name='crear_almacen'),
    path('almacenes/editar/<int:id>/', views.editar_almacen, name='editar_almacen'),
    path('almacenes/toggle/<int:id>/', views.toggle_almacen, name='toggle_almacen'), 
    path('reporte/consumo/', views.reporte_consumo_unidades, name='reporte_consumo_unidades'),
    path('entrada/obtener-items-compra/', views.obtener_items_compra_view, name='obtener_items_compra'),
    path('existencias/', views.inventario_por_almacen, name='inventario_por_almacen'),
    path('entradas/', views.nota_ingreso_list, name='nota_ingreso_list'), 
    path('salidas/', views.nota_salida_list, name='nota_salida_list'),   
    path('unidades/crear-ajax/', views.crear_unidad_medida_ajax, name='crear_unidad_medida_ajax'),
    path('entradas/detalle/<int:id>/', views.detalle_nota_ingreso, name='detalle_nota_ingreso'),
    path('salidas/detalle/<int:id>/', views.detalle_nota_salida, name='detalle_nota_salida'),
    
]