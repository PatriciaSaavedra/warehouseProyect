from django.urls import path
from .views import auditoria_view

urlpatterns = [
    path(
        '',
        auditoria_view,
        name='auditoria'
    ),
]