from django.urls import path
from . import views

urlpatterns = [
    path('', views.auditoria_list, name='auditoria_list'),
    path('exportar-excel/', views.exportar_logs_excel, name='exportar_logs_excel'),
    path('purgar/', views.purgar_logs_antiguos, name='purgar_logs_antiguos'),
]