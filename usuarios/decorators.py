from django.shortcuts import redirect
from django.contrib import messages
from functools import wraps

def rol_requerido(roles_permitidos):
    """
    Decorador centralizado para validar roles de usuario.
    Maneja de forma segura la ausencia de perfil y muestra mensajes de advertencia.
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                messages.warning(request, 'Debe iniciar sesión para acceder.')
                return redirect('login')

            try:
                perfil = request.user.perfilusuario
                rol = perfil.rol
            except AttributeError:
                messages.error(request, 'Su cuenta no dispone de un perfil de almacenes configurado.')
                return redirect('login')

            if rol not in roles_permitidos:
                messages.error(request, 'No tiene permisos para acceder a esta sección.')
                return redirect('login')  # Redirigir a una página segura o login

            return view_func(request, *args, **kwargs)
        return wrapper
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