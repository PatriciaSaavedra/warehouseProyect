from django.urls import path
from django.views.generic import RedirectView
from . import views

urlpatterns = [
    path('', views.inventario_view, name='inventario'),
    path('movimientos/', views.movimientos, name='movimientos'),
    path('kardex/<int:id>/', views.kardex, name='kardex'),
    path('entrada/', views.entrada_inventario, name='entrada_inventario'),
    path('nuevo/', views.nuevo_material, name='nuevo_material'),
    path('editar/<int:id>/', views.editar_material, name='editar_material'),
    path('toggle-material/<int:id>/', views.toggle_material, name='toggle_material'),
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
    path('reporte-consumo/', views.reporte_consumo_unidades, name='reporte_consumo_unidades'),
    path('entrada/obtener-items-compra/', views.obtener_items_compra_view, name='obtener_items_compra'),
    path('api/items-compra/', views.obtener_items_compra_view, name='api_items_compra'),

    path('existencias/', RedirectView.as_view(pattern_name='inventario', permanent=False), name='inventario_por_almacen'),
    path('entradas/', views.nota_ingreso_list, name='nota_ingreso_list'), 
    path('salidas/', views.nota_salida_list, name='nota_salida_list'),   
    path('unidades/crear-ajax/', views.crear_unidad_medida_ajax, name='crear_unidad_medida_ajax'),
    path('entradas/detalle/<int:id>/', views.detalle_nota_ingreso, name='detalle_nota_ingreso'),
    path('salidas/detalle/<int:id>/', views.detalle_nota_salida, name='detalle_nota_salida'),
    path('lotes/', views.lotes_list, name='lotes_list'),
    path('entradas/pdf/<int:id>/', views.nota_recepcion_pdf, name='nota_recepcion_pdf'),       
    path('kardex/fisico/pdf/<int:id>/', views.kardex_fisico_pdf, name='kardex_fisico_pdf'),   
    path('reporte/inventario/pdf/', views.reporte_inventario_oficial_pdf, name='reporte_inventario_oficial_pdf'),
    path('transferencias/', views.transferencia_list, name='transferencia_list'),
    path('transferencias/enviar/', views.enviar_transferencia, name='enviar_transferencia'),
    path('transferencias/<int:id>/recibir/', views.recibir_transferencia, name='recibir_transferencia'),
    path('kardex/<int:id>/', views.kardex, name='kardex'),  
    path('gestion/cierre/', views.cierre_conciliacion_view, name='cierre_conciliacion'),
    path('stock-unidad/', views.inventario_por_unidad, name='inventario_por_unidad'),

]