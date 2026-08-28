from django.shortcuts import redirect
from django.contrib import messages
from django.contrib.auth import logout
from functools import wraps

def rol_requerido(roles_permitidos):
    """
    Decorador para restringir el acceso a vistas según el rol del perfil de usuario [11].
    Verifica también que el usuario esté activo y de lo contrario lo desconecta de inmediato.
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            # 1. Verificar autenticación básica
            if not request.user.is_authenticated:
                return redirect('login')  # O la url de login de su sistema

            # 2. Verificar comportamiento de usuarios inactivos (Desactivación)
            if not request.user.is_active:
                logout(request)  # Cerrar la sesión del usuario desactivado de inmediato
                messages.error(request, "Su cuenta ha sido desactivada. Comuníquese con el administrador del sistema.")
                return redirect('/')

            # 3. Obtener el perfil y validar el rol del sistema
            perfil = getattr(request.user, 'perfilusuario', None)
            if not perfil:
                messages.error(request, "Su cuenta no posee un perfil de usuario asignado.")
                return redirect('/')

            # 4. Verificar acceso al sistema según rol (Evitar acceso a funciones de otro rol)
            if perfil.rol not in roles_permitidos:
                messages.error(request, "No tiene los permisos necesarios para acceder a esta sección del sistema.")
                return redirect('dashboard')  # Redirigir a una sección segura y común

            return view_func(request, *args, **kwargs)
        return _wrapped_view
    return decorator

def tiene_rol(usuario, roles):
    """
    Función auxiliar para evaluar permisos en plantillas HTML o flujos condicionales.
    """
    if not usuario or not usuario.is_authenticated:
        return False
    try:
        return usuario.perfilusuario.rol in roles
    except AttributeError:
        return False