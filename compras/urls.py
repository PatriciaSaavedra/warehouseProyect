from django.urls import path
from . import views

urlpatterns = [
    path('', views.compras_list, name='compras_list'),
    path('crear/', views.crear_compra_menor, name='crear_compra_menor'),
]