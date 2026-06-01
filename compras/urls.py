from django.urls import path
from . import views

urlpatterns = [
    path('', views.compras_view, name='compras'),
]