from django.urls import path
from . import views

urlpatterns = [
    path('', views.compras_list, name='compras_list'),
    path('crear/', views.crear_compra_menor, name='crear_compra_menor'),
    path('proveedores/', views.proveedores_list, name='proveedores_list'),
    path('proveedores/crear/', views.crear_proveedor, name='crear_proveedor'),
    path('proveedores/editar/<int:id>/', views.editar_proveedor, name='editar_proveedor'),
    path('detalle/<int:id>/', views.detalle_compra, name='detalle_compra'),
    path('pdf/<int:id>/', views.compra_pdf, name='compra_pdf'),
]