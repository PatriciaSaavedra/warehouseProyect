from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.models import User
from django.contrib import messages
from django.contrib.auth.decorators import login_required

from .models import PerfilUsuario, ROLES
from organizacion.models import UnidadOrganizacional
from auditoria.models import Bitacora
from usuarios.decorators import rol_requerido

@login_required
@rol_requerido(['ADMINISTRADOR'])
def usuarios_view(request):
    query = request.GET.get('q', '').strip()
    usuarios = User.objects.select_related('perfilusuario').all()

    if query:
        usuarios = usuarios.filter(
            Q(first_name__icontains=query) |
            Q(last_name__icontains=query) |
            Q(username__icontains=query) |
            Q(email__icontains=query) |
            Q(perfilusuario__rol__icontains=query)
        )

    usuarios = usuarios.order_by('first_name')

    paginator = Paginator(usuarios, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        'usuarios/index.html',
        {
            'page_obj': page_obj,
            'query': query
        }
    )

@login_required
@rol_requerido(['ADMINISTRADOR'])
def crear_usuario_view(request):
    unidades = UnidadOrganizacional.objects.all()

    if request.method == 'POST':
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        username = request.POST.get('username', '').strip()
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')
        rol = request.POST.get('rol')
        unidad_id = request.POST.get('unidad')

        # Validaciones de campos obligatorios
        if not username or not password or not rol:
            messages.error(request, 'Los campos Usuario, Contraseña y Rol son obligatorios.')
            return redirect('crear_usuario')

        if len(password) < 6:
            messages.error(request, 'La contraseña debe tener al menos 6 caracteres.')
            return redirect('crear_usuario')

        # Validar unicidad
        if User.objects.filter(username__iexact=username).exists():
            messages.error(request, 'El nombre de usuario ya está registrado en el sistema.')
            return redirect('crear_usuario')

        if email and User.objects.filter(email__iexact=email).exists():
            messages.error(request, 'El correo electrónico ya está registrado por otro usuario.')
            return redirect('crear_usuario')

        try:
            # Atomicidad: se crea el usuario y el perfil juntos o no se crea nada
            with transaction.atomic():
                user = User.objects.create_user(
                    username=username,
                    password=password,
                    first_name=first_name,
                    last_name=last_name,
                    email=email
                )

                PerfilUsuario.objects.create(
                    user=user,
                    rol=rol,
                    unidad_id=unidad_id if unidad_id else None
                )

                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Usuarios',
                    accion='Crear usuario',
                    descripcion=f'Se creó el usuario {username} con el rol {rol}'
                )

            messages.success(request, 'Usuario creado correctamente.')
            return redirect('usuarios')

        except Exception as e:
            messages.error(request, 'Ocurrió un error inesperado al registrar el usuario en el sistema.')
            return redirect('crear_usuario')

    return render(
        request,
        'usuarios/crear.html',
        {
            'roles': ROLES,
            'unidades': unidades
        }
    )

@login_required
@rol_requerido(['ADMINISTRADOR'])
def editar_usuario_view(request, user_id):
    usuario = get_object_or_404(User, id=user_id)
    perfil = usuario.perfilusuario
    unidades = UnidadOrganizacional.objects.all()

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        email = request.POST.get('email', '').strip()

        if not username:
            messages.error(request, 'El nombre de usuario no puede estar vacío.')
            return redirect('editar_usuario', user_id=user_id)

        # Validar duplicados excluyendo al usuario actual
        if User.objects.filter(username__iexact=username).exclude(id=user_id).exists():
            messages.error(request, 'El nombre de usuario ya está registrado por otra cuenta.')
            return redirect('editar_usuario', user_id=user_id)

        if email and User.objects.filter(email__iexact=email).exclude(id=user_id).exists():
            messages.error(request, 'El correo electrónico ya está registrado por otra cuenta.')
            return redirect('editar_usuario', user_id=user_id)

        try:
            with transaction.atomic():
                usuario.first_name = request.POST.get('first_name', '').strip()
                usuario.last_name = request.POST.get('last_name', '').strip()
                usuario.email = email
                usuario.username = username
                usuario.save()

                perfil.rol = request.POST.get('rol')
                perfil.unidad_id = request.POST.get('unidad') or None
                perfil.save()

                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Usuarios',
                    accion='Editar usuario',
                    descripcion=f'Se modificó el usuario {usuario.username}'
                )

            messages.success(request, 'Usuario actualizado correctamente.')
            return redirect('usuarios')

        except Exception:
            messages.error(request, 'Error al actualizar la información del usuario.')
            return redirect('editar_usuario', user_id=user_id)

    return render(
        request,
        'usuarios/editar.html',
        {
            'usuario_obj': usuario,
            'perfil': perfil,
            'roles': ROLES,
            'unidades': unidades
        }
    )

@login_required
@rol_requerido(['ADMINISTRADOR'])
def toggle_usuario_view(request, user_id):
    usuario = get_object_or_404(User, id=user_id)

    if usuario == request.user:
        messages.error(request, 'No puedes desactivar tu propio usuario de sesión.')
        return redirect('usuarios')

    usuario.is_active = not usuario.is_active
    usuario.save()

    estado = 'activado' if usuario.is_active else 'desactivado'

    Bitacora.objects.create(
        usuario=request.user,
        modulo='Usuarios',
        accion='Cambio de estado',
        descripcion=f'Se {estado} la cuenta del usuario: {usuario.username}'
    )

    messages.success(request, f'Usuario {estado} correctamente.')
    return redirect('usuarios')

@login_required
@rol_requerido(['ADMINISTRADOR'])
def reset_password_view(request, user_id):
    usuario = get_object_or_404(User, id=user_id)

    if request.method == 'POST':
        nueva_password = request.POST.get('password', '')

        if len(nueva_password) < 6:
            messages.error(request, 'La nueva contraseña debe tener al menos 6 caracteres.')
            return redirect('reset_password', user_id=user_id)

        usuario.set_password(nueva_password)
        usuario.save()

        Bitacora.objects.create(
            usuario=request.user,
            modulo='Usuarios',
            accion='Reset contraseña',
            descripcion=f'Se restableció la contraseña del usuario {usuario.username}'
        )

        messages.success(request, 'Contraseña restablecida correctamente.')
        return redirect('usuarios')

    return render(
        request,
        'usuarios/reset_password.html',
        {
            'usuario_obj': usuario
        }
    )