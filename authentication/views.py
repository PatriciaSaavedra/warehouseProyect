# FILE: authentication/views.py
from django.contrib.auth import authenticate, login, logout
from django.shortcuts import render, redirect
from django.views.decorators.http import require_POST
from django.contrib import messages
from auditoria.models import Bitacora

def login_view(request):
    if request.user.is_authenticated:
        return redirect('/dashboard/')

    error = None

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')

        if not username or not password:
            error = 'Debe introducir el usuario y la contraseña'
        else:
            user = authenticate(request, username=username, password=password)

            if user is not None:
                if not user.is_active:
                    error = 'Esta cuenta ha sido desactivada por el administrador'
                else:
                    login(request, user)
                    Bitacora.objects.create(
                        usuario=user,
                        modulo='Autenticación',
                        accion='Inicio de sesión',
                        descripcion=f'Acceso exitoso al sistema por el usuario: {username}'
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
    # Validamos que el usuario esté autenticado antes de intentar registrar la bitácora
    if request.user.is_authenticated:
        Bitacora.objects.create(
            usuario=request.user,
            modulo='Autenticación',
            accion='Cierre de sesión',
            descripcion=f'Cierre de sesión del usuario: {request.user.username}'
        )
    
    logout(request)
    messages.success(request, 'Sesión cerrada correctamente.')
    return redirect('login')