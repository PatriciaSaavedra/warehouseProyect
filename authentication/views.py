from django.contrib.auth import authenticate, login, logout
from django.shortcuts import render, redirect
from django.views.decorators.http import require_POST
from auditoria.models import Bitacora


def login_view(request):

    if request.user.is_authenticated:
        return redirect('/dashboard/')

    error = None

    if request.method == 'POST':

        username = request.POST.get('username')
        password = request.POST.get('password')

        user = authenticate(
            request,
            username=username,
            password=password
        )

        if user is not None:

            login(request, user)
            Bitacora.objects.create(
                usuario=user,
                modulo='Autenticación',
                accion='Inicio de sesión',
                descripcion='Acceso al sistema'
            )

            return redirect('/dashboard/')

        else:
            error = 'Usuario o contraseña incorrectos'

    return render(
        request,
        'auth/login.html',
        {
            'error': error
        }
    )


@require_POST
def logout_view(request):
    Bitacora.objects.create(
        usuario=request.user,
        modulo='Autenticación',
        accion='Cierre de sesión',
        descripcion='Salida del sistema'
    )

    logout(request)

    return redirect('login')