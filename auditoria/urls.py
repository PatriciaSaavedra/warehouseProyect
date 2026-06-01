from django.urls import path
from . import views

urlpatterns = [
    path('', views.auditoria_view, name='auditoria'),
]