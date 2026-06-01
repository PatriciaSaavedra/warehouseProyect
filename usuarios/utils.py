from django.shortcuts import redirect
from functools import wraps


def rol_requerido(roles_permitidos):

    def decorator(view_func):

        @wraps(view_func)
        def wrapper(request, *args, **kwargs):

            if not request.user.is_authenticated:

                return redirect('login')

            perfil = request.user.perfilusuario

            if perfil.rol not in roles_permitidos:

                return redirect('dashboard')

            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator
def tiene_rol(usuario, roles):

    if not usuario.is_authenticated:
        return False

    try:

        return usuario.perfilusuario.rol in roles

    except:
        return False