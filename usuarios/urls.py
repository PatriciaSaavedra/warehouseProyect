from django.urls import path
from .views import usuarios_view
from .views import crear_usuario_view
from .views import editar_usuario_view
from .views import toggle_usuario_view
from .views import reset_password_view
urlpatterns = [

    path(
        '',
        usuarios_view,
        name='usuarios'
    ),

    path(
        'crear/',
        crear_usuario_view,
        name='crear_usuario'
    ),
    path(
        'editar/<int:user_id>/',
        editar_usuario_view,
        name='editar_usuario'
       ),
    path(
        'toggle/<int:user_id>/',
        toggle_usuario_view,
        name='toggle_usuario'
    ),
    path(
        'reset-password/<int:user_id>/',
        reset_password_view,
        name='reset_password'
    ),

]