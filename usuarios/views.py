from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import render, redirect
from django.contrib.auth.models import User
from django.contrib import messages
from .models import PerfilUsuario, ROLES
from organizacion.models import UnidadOrganizacional

from django.contrib.auth.decorators import login_required
from usuarios.utils import rol_requerido
@login_required
@rol_requerido(['ADMINISTRADOR'])
def usuarios_view(request):

    query = request.GET.get('q')

    usuarios = User.objects.select_related(
        'perfilusuario'
    ).all()

    # BUSQUEDA
    if query:

        usuarios = usuarios.filter(

            Q(first_name__icontains=query) |
            Q(last_name__icontains=query) |
            Q(username__icontains=query) |
            Q(email__icontains=query) |
            Q(perfilusuario__rol__icontains=query)

        )

    usuarios = usuarios.order_by('first_name')

    # PAGINACION
    paginator = Paginator(
        usuarios,
        10
    )

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

        first_name = request.POST.get('first_name')
        last_name = request.POST.get('last_name')
        username = request.POST.get('username')
        email = request.POST.get('email')
        password = request.POST.get('password')
        rol = request.POST.get('rol')
        unidad_id = request.POST.get('unidad')

        # VALIDAR USUARIO
        if User.objects.filter(username=username).exists():

            messages.error(request, 'El usuario ya existe')

            return redirect('crear_usuario')

        # CREAR USUARIO
        user = User.objects.create_user(
            username=username,
            password=password,
            first_name=first_name,
            last_name=last_name,
            email=email
        )

        # CREAR PERFIL
        PerfilUsuario.objects.create(
            user=user,
            rol=rol,
            unidad_id=unidad_id if unidad_id else None
        )

        messages.success(
            request,
            'Usuario creado correctamente'
        )

        return redirect('/usuarios/')

    return render(
        request,
        'usuarios/crear.html',
        {
            'roles': ROLES,
            'unidades': unidades
        }
    )
from django.shortcuts import get_object_or_404


@login_required
@rol_requerido(['ADMINISTRADOR'])
def editar_usuario_view(request, user_id):

    usuario = get_object_or_404(
        User,
        id=user_id
    )

    perfil = usuario.perfilusuario

    unidades = UnidadOrganizacional.objects.all()

    if request.method == 'POST':

        usuario.first_name = request.POST.get('first_name')
        usuario.last_name = request.POST.get('last_name')
        usuario.email = request.POST.get('email')
        usuario.username = request.POST.get('username')

        usuario.save()

        perfil.rol = request.POST.get('rol')
        perfil.unidad_id = request.POST.get('unidad')

        perfil.save()

        messages.success(
            request,
            'Usuario actualizado correctamente'
        )

        return redirect('usuarios')

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

    usuario = get_object_or_404(
        User,
        id=user_id
    )

    # EVITAR DESACTIVARSE A SI MISMO
    if usuario == request.user:

        messages.error(
            request,
            'No puedes desactivar tu propio usuario'
        )

        return redirect('usuarios')

    usuario.is_active = not usuario.is_active

    usuario.save()

    if usuario.is_active:

        messages.success(
            request,
            'Usuario activado correctamente'
        )

    else:

        messages.success(
            request,
            'Usuario desactivado correctamente'
        )

    return redirect('usuarios')
@login_required
@rol_requerido(['ADMINISTRADOR'])
def reset_password_view(request, user_id):

    usuario = get_object_or_404(
        User,
        id=user_id
    )

    if request.method == 'POST':

        nueva_password = request.POST.get('password')

        usuario.set_password(nueva_password)

        usuario.save()

        messages.success(
            request,
            'Contraseña actualizada correctamente'
        )

        return redirect('usuarios')

    return render(
        request,
        'usuarios/reset_password.html',
        {
            'usuario_obj': usuario
        }
    )