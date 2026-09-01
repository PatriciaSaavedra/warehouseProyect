# FILE: usuarios/urls.py
from django.urls import path
from .views import (
    usuarios_view,
    crear_usuario_view,
    editar_usuario_view,
    toggle_usuario_view,
    reset_password_view,
    perfil_usuario_view,
    
    # CRUD de Unidades
    unidades_list_view,   
    crear_unidad_view,    
    editar_unidad_view,   
    toggle_unidad_view,     # <-- NUEVA VISTA (Dar de baja)
    
    # CRUD de Secretarías (NUEVO)
    secretarias_list_view,
    crear_secretaria_view,
    editar_secretaria_view,
    toggle_secretaria_view,
)

urlpatterns = [
    path('', usuarios_view, name='usuarios'),
    path('perfil/', perfil_usuario_view, name='perfil'),
    path('crear/', crear_usuario_view, name='crear_usuario'),
    path('editar/<int:user_id>/', editar_usuario_view, name='editar_usuario'),
    path('toggle/<int:user_id>/', toggle_usuario_view, name='toggle_usuario'),
    path('reset-password/<int:user_id>/', reset_password_view, name='reset_password'),
    
    # --- CRUD UNIDADES ---
    path('unidades/', unidades_list_view, name='unidades_list'),
    path('unidades/crear/', crear_unidad_view, name='crear_unidad'),
    path('unidades/editar/<int:id>/', editar_unidad_view, name='editar_unidad'),
    path('unidades/toggle/<int:id>/', toggle_unidad_view, name='toggle_unidad'), # Dar de baja
    
    # --- CRUD SECRETARÍAS ---
    path('secretarias/', secretarias_list_view, name='secretarias_list'),
    path('secretarias/crear/', crear_secretaria_view, name='crear_secretaria'),
    path('secretarias/editar/<int:id>/', editar_secretaria_view, name='editar_secretaria'),
    path('secretarias/toggle/<int:id>/', toggle_secretaria_view, name='toggle_secretaria'), # Dar de baja
]