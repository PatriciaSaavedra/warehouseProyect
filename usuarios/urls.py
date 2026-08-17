# FILE: usuarios/urls.py (Código completo y corregido)
from django.urls import path

# Importaciones locales de tu aplicación de usuarios (Con las vistas de unidades integradas) [28]
from .views import (
    usuarios_view,
    crear_usuario_view,
    editar_usuario_view,
    toggle_usuario_view,
    reset_password_view,
    perfil_usuario_view,
    unidades_list_view,   
    crear_unidad_view,    
    editar_unidad_view,   
)

urlpatterns = [
    path('', usuarios_view, name='usuarios'),
    path('perfil/', perfil_usuario_view, name='perfil'),
    path('crear/', crear_usuario_view, name='crear_usuario'),
    path('editar/<int:user_id>/', editar_usuario_view, name='editar_usuario'),
    path('toggle/<int:user_id>/', toggle_usuario_view, name='toggle_usuario'),
    path('reset-password/<int:user_id>/', reset_password_view, name='reset_password'),
    
    # --- RUTAS DE UNIDADES ORGANIZACIONALES (CORREGIDAS SIN PREFIJO 'views.') [28] ---
    path('unidades/', unidades_list_view, name='unidades_list'),
    path('unidades/crear/', crear_unidad_view, name='crear_unidad'),
    path('unidades/editar/<int:id>/', editar_unidad_view, name='editar_unidad'),
]