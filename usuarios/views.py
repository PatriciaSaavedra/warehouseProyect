import re
from django.core.paginator import Paginator
from django.db import transaction, IntegrityError  # <-- Control de integridad
from django.db.models import Q
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.models import User
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.validators import validate_email
from django.core.exceptions import ValidationError

from .models import PerfilUsuario, ROLES
from organizacion.models import UnidadOrganizacional
from auditoria.models import Bitacora
from usuarios.decorators import rol_requerido


# ==========================================
# FUNCIONES AUXILIARES DE VALIDACIÓN Y NORMALIZACIÓN
# ==========================================

def normalizar_espacios(texto):
    if not texto:
        return ""
    return re.sub(r'\s+', ' ', texto.strip())


def capitalizar_nombre_propio(texto):
    """
    Convierte nombres a formato "Title Case" manteniendo minúsculas 
    en preposiciones intermedias del español (ej: "Juan de la Cruz").
    """
    if not texto:
        return ""
    palabras = texto.split()
    palabras_formateadas = []
    particulas_bajas = ["de", "la", "del", "y", "los", "las", "e"]
    
    for i, pal in enumerate(palabras):
        pal_lower = pal.lower()
        if pal_lower in particulas_bajas and i > 0:
            palabras_formateadas.append(pal_lower)
        else:
            palabras_formateadas.append(pal.capitalize())
    return " ".join(palabras_formateadas)


def validar_datos_usuario(datos, es_creacion=False, user_id=None):
    """
    Valida y normaliza exhaustivamente los datos del usuario.
    Controla longitud, formato, unicidad y repetición de caracteres.
    """
    errores = []

    # 1. Normalización inicial (Username se pasa a minúsculas)
    username = datos.get('username', '').strip().lower()
    first_name = capitalizar_nombre_propio(normalizar_espacios(datos.get('first_name', '')))
    last_name = capitalizar_nombre_propio(normalizar_espacios(datos.get('last_name', '')))
    email = normalizar_espacios(datos.get('email', '')).lower()
    rol = datos.get('rol', '').strip()
    unidad_id = datos.get('unidad', '').strip()

    # Expresión regular anti-spam (Detecta más de 3 letras idénticas seguidas)
    regex_spam_letras = r"(.)\1{3,}"

    # 2. VALIDACIÓN DEL USERNAME
    if not username:
        errores.append("El nombre de usuario (username) es obligatorio.")
    else:
        # Validación de espacios
        if " " in username:
            errores.append("El nombre de usuario no puede contener espacios en blanco.")
        # Validación de longitud (4 a 30 caracteres)
        elif len(username) < 4 or len(username) > 30:
            errores.append("El nombre de usuario debe tener entre 4 y 30 caracteres.")
        # Caracteres permitidos (letras minúsculas, números, guiones y guión bajo)
        elif not re.match(r"^[a-z0-9_\-]+$", username):
            errores.append("El nombre de usuario solo puede contener letras, números, guiones (-) y guiones bajos (_).")
        
        # Comprobación de unicidad insensible a mayúsculas
        query_username = User.objects.filter(username__iexact=username)
        if not es_creacion and user_id:
            query_username = query_username.exclude(id=user_id)
        if query_username.exists():
            errores.append("El nombre de usuario ya está registrado por otra cuenta.")

    # 3. VALIDACIÓN DE NOMBRES Y APELLIDOS (Límites: 2 a 40 caracteres + Anti-Spam)
    if not first_name:
        errores.append("El nombre es obligatorio y no puede contener únicamente espacios.")
    else:
        if len(first_name) < 2 or len(first_name) > 40:
            errores.append("El nombre debe tener entre 2 y 40 caracteres.")
        if first_name.isdigit():
            errores.append("El nombre no puede ser únicamente numérico.")
        elif not re.match(r"^[a-zA-ZáéíóúüñÁÉÍÓÚÜÑ\s\-\']+$", first_name):
            errores.append("El nombre contiene caracteres no permitidos.")
        elif re.search(regex_spam_letras, first_name):
            errores.append("El nombre ingresado no es válido debido a una repetición excesiva de caracteres (anti-spam).")

    if not last_name:
        errores.append("El apellido es obligatorio y no puede contener únicamente espacios.")
    else:
        if len(last_name) < 2 or len(last_name) > 40:
            errores.append("El apellido debe tener entre 2 y 40 caracteres.")
        if last_name.isdigit():
            errores.append("El apellido no puede ser únicamente numérico.")
        elif not re.match(r"^[a-zA-ZáéíóúüñÁÉÍÓÚÜÑ\s\-\']+$", last_name):
            errores.append("El apellido contiene caracteres no permitidos.")
        elif re.search(regex_spam_letras, last_name):
            errores.append("El apellido ingresado no es válido debido a una repetición excesiva de caracteres (anti-spam).")

# 4. VALIDACIÓN DE EMAIL (Campo obligatorio, formato, longitud y unicidad)
    if not email:
        errores.append("El correo electrónico es obligatorio para el registro institucional.")
    else:
        # Validación de longitud (Mínimo 5 caracteres ej: a@b.c, Máximo 100 por diseño)
        if len(email) < 5 or len(email) > 100:
            errores.append("El correo electrónico debe tener entre 5 y 100 caracteres.")
        
        # Validación de estructura/formato (Usa el validador nativo de Django)
        else:
            try:
                validate_email(email)
            except ValidationError:
                errores.append("El formato del correo electrónico no es válido (ejemplo: usuario@institucion.gob.bo).")
        
        # Evitar correos duplicados (Garantiza unicidad en toda la base de datos)
        query_email = User.objects.filter(email__iexact=email)
        if not es_creacion and user_id:
            query_email = query_email.exclude(id=user_id)
        if query_email.exists():
            errores.append("El correo electrónico ya está registrado por otro funcionario en el sistema.")
            
    # 5. VALIDACIÓN DE CONTRASEÑA (Solo creación)
    password = ""
    if es_creacion:
        password = datos.get('password', '')
        password_confirm = datos.get('password_confirm', '')

        if not password:
            errores.append("La contraseña es obligatoria.")
        else:
            if len(password) < 6 or len(password) > 128:
                errores.append("La contraseña debe tener entre 6 y 128 caracteres.")
            if password.strip() != password:
                errores.append("La contraseña no debe comenzar ni terminar con espacios en blanco.")

        if password != password_confirm:
            errores.append("La contraseña y su confirmación no coinciden.")

    if not rol:
        errores.append("El rol del usuario es obligatorio.")

    datos_normalizados = {
        'username': username,
        'first_name': first_name,
        'last_name': last_name,
        'email': email,
        'rol': rol,
        'unidad_id': unidad_id if unidad_id else None,
    }
    if es_creacion:
        datos_normalizados['password'] = password

    return datos_normalizados, errores


# ==========================================
# VISTAS DE USUARIOS
# ==========================================

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
        datos_normalizados, errores = validar_datos_usuario(request.POST, es_creacion=True)

        if errores:
            for error in errores:
                messages.error(request, error)
            return render(
                request,
                'usuarios/crear.html',
                {
                    'roles': ROLES,
                    'unidades': unidades,
                    'valores': request.POST
                }
            )

        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    username=datos_normalizados['username'],
                    password=datos_normalizados['password'],
                    first_name=datos_normalizados['first_name'],
                    last_name=datos_normalizados['last_name'],
                    email=datos_normalizados['email']
                )

                PerfilUsuario.objects.create(
                    user=user,
                    rol=datos_normalizados['rol'],
                    unidad_id=datos_normalizados['unidad_id']
                )

                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Usuarios',
                    accion='Crear usuario',
                    descripcion=f'Se creó el usuario {datos_normalizados["username"]} con el rol {datos_normalizados["rol"]}'
                )

            messages.success(request, 'Usuario creado correctamente.')
            return redirect('usuarios')

        except IntegrityError:
            messages.error(request, 'Error de integridad: El nombre de usuario o correo ya está siendo utilizado.')
            return render(
                request,
                'usuarios/crear.html',
                {
                    'roles': ROLES,
                    'unidades': unidades,
                    'valores': request.POST
                }
            )
        except Exception:
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
        datos_normalizados, errores = validar_datos_usuario(request.POST, es_creacion=False, user_id=user_id)

        if errores:
            for error in errores:
                messages.error(request, error)
            return render(
                request,
                'usuarios/editar.html',
                {
                    'usuario_obj': usuario,
                    'perfil': perfil,
                    'roles': ROLES,
                    'unidades': unidades,
                    'valores': request.POST
                }
            )

        try:
            with transaction.atomic():
                usuario.first_name = datos_normalizados['first_name']
                usuario.last_name = datos_normalizados['last_name']
                usuario.email = datos_normalizados['email']
                usuario.username = datos_normalizados['username']
                usuario.save()

                perfil.rol = datos_normalizados['rol']
                perfil.unidad_id = datos_normalizados['unidad_id']
                perfil.save()

                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Usuarios',
                    accion='Editar usuario',
                    descripcion=f'Se modificó el usuario {usuario.username}'
                )

            messages.success(request, 'Usuario actualizado correctamente.')
            return redirect('usuarios')

        except IntegrityError:
            messages.error(request, 'Error de integridad: No se pudo guardar porque el nombre de usuario o correo ya existe.')
            return render(
                request,
                'usuarios/editar.html',
                {
                    'usuario_obj': usuario,
                    'perfil': perfil,
                    'roles': ROLES,
                    'unidades': unidades,
                    'valores': request.POST
                }
            )
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
        confirmar_password = request.POST.get('password_confirm', '')

        errores = []
        if not nueva_password:
            errores.append('La nueva contraseña es obligatoria.')
        else:
            if len(nueva_password) < 6 or len(nueva_password) > 128:
                errores.append('La nueva contraseña debe tener entre 6 y 128 caracteres.')
            if nueva_password.strip() != nueva_password:
                errores.append('La contraseña no debe iniciar ni finalizar con espacios en blanco.')

        if nueva_password != confirmar_password:
            errores.append('La contraseña y su confirmación no coinciden.')

        if errores:
            for error in errores:
                messages.error(request, error)
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


@login_required
def perfil_usuario_view(request):
    usuario = request.user
    perfil = getattr(usuario, 'perfilusuario', None)

    if request.method == 'POST':
        first_name = capitalizar_nombre_propio(normalizar_espacios(request.POST.get('first_name', '')))
        last_name = capitalizar_nombre_propio(normalizar_espacios(request.POST.get('last_name', '')))
        email = normalizar_espacios(request.POST.get('email', '')).lower()

        errores = []
        regex_spam_letras = r"(.)\1{3,}"

        if not first_name:
            errores.append("El nombre es obligatorio.")
        else:
            if len(first_name) < 2 or len(first_name) > 40:
                errores.append("El nombre debe tener entre 2 y 40 caracteres.")
            if first_name.isdigit():
                errores.append("El nombre no puede ser únicamente numérico.")
            elif not re.match(r"^[a-zA-ZáéíóüñÁÉÍÓÚÜÑ\s\-\']+$", first_name):
                errores.append("El nombre contiene caracteres no permitidos.")
            elif re.search(regex_spam_letras, first_name):
                errores.append("El nombre contiene una repetición excesiva de caracteres (spam).")

        if not last_name:
            errores.append("El apellido es obligatorio.")
        else:
            if len(last_name) < 2 or len(last_name) > 40:
                errores.append("El apellido debe tener entre 2 y 40 caracteres.")
            if last_name.isdigit():
                errores.append("El apellido no puede ser únicamente numérico.")
            elif not re.match(r"^[a-zA-ZáéíóüñÁÉÍÓÚÜÑ\s\-\']+$", last_name):
                errores.append("El apellido contiene caracteres no permitidos.")
            elif re.search(regex_spam_letras, last_name):
                errores.append("El apellido contiene una repetición excesiva de caracteres (spam).")

        if email:
            try:
                validate_email(email)
            except ValidationError:
                errores.append("El formato del correo electrónico no es válido.")
            if User.objects.filter(email__iexact=email).exclude(id=usuario.id).exists():
                errores.append("El correo electrónico ya está registrado por otra cuenta.")

        if errores:
            for error in errores:
                messages.error(request, error)
            return redirect('perfil')

        try:
            with transaction.atomic():
                usuario.first_name = first_name
                usuario.last_name = last_name
                usuario.email = email
                usuario.save()

                Bitacora.objects.create(
                    usuario=usuario,
                    modulo='Usuarios',
                    accion='Editar perfil propio',
                    descripcion=f'El usuario {usuario.username} actualizó sus datos de perfil.'
                )

            messages.success(request, 'Perfil actualizado correctamente.')
            return redirect('perfil')

        except Exception:
            messages.error(request, 'Ocurrió un error al intentar actualizar el perfil.')
            return redirect('perfil')

    return render(
        request,
        'usuarios/perfil.html',
        {
            'usuario_obj': usuario,
            'perfil': perfil
        }
    )


# ==========================================
# VISTAS DE UNIDADES ORGANIZACIONALES
# ==========================================

@login_required
@rol_requerido(['ADMINISTRADOR'])
def unidades_list_view(request):
    query = request.GET.get('q', '').strip()
    unidades = UnidadOrganizacional.objects.all()

    if query:
        unidades = unidades.filter(
            Q(nombre__icontains=query) |
            Q(codigo_sigep__icontains=query)
        )

    unidades = unidades.order_by('nombre')

    paginator = Paginator(unidades, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        'usuarios/unidades_list.html',
        {
            'page_obj': page_obj,
            'query': query
        }
    )


@login_required
@rol_requerido(['ADMINISTRADOR'])
def crear_unidad_view(request):
    if request.method == 'POST':
        nombre = normalizar_espacios(request.POST.get('nombre', ''))
        codigo_sigep = normalizar_espacios(request.POST.get('codigo_sigep', ''))

        if not nombre:
            messages.error(request, 'El nombre de la unidad es obligatorio.')
            return redirect('crear_unidad')

        if UnidadOrganizacional.objects.filter(nombre__iexact=nombre).exists():
            messages.error(request, 'Ya existe una Unidad Organizacional registrada con este nombre.')
            return redirect('crear_unidad')

        try:
            with transaction.atomic():
                UnidadOrganizacional.objects.create(
                    nombre=nombre,
                    codigo_sigep=codigo_sigep if codigo_sigep else None
                )
                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Organización',
                    accion='Crear Unidad',
                    descripcion=f'Se creó la Unidad Organizacional: {nombre} (SIGEP: {codigo_sigep})'
                )

            messages.success(request, 'Unidad Organizacional creada correctamente.')
            return redirect('unidades_list')

        except Exception as e:
            messages.error(request, f'Error al registrar la unidad: {str(e)}')
            return redirect('crear_unidad')

    return render(request, 'usuarios/crear_unidad.html')


@login_required
@rol_requerido(['ADMINISTRADOR'])
def editar_unidad_view(request, id):
    unidad = get_object_or_404(UnidadOrganizacional, id=id)

    if request.method == 'POST':
        nombre = normalizar_espacios(request.POST.get('nombre', ''))
        codigo_sigep = normalizar_espacios(request.POST.get('codigo_sigep', ''))

        if not nombre:
            messages.error(request, 'El nombre de la unidad es un campo obligatorio.')
            return redirect('editar_unidad', id=id)

        if UnidadOrganizacional.objects.filter(nombre__iexact=nombre).exclude(id=id).exists():
            messages.error(request, 'Ya existe otra Unidad Organizacional con ese nombre en el sistema.')
            return redirect('editar_unidad', id=id)

        try:
            with transaction.atomic():
                unidad.nombre = nombre
                unidad.codigo_sigep = codigo_sigep if codigo_sigep else None
                unidad.save()

                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Organización',
                    accion='Editar Unidad',
                    descripcion=f'Se actualizaron los datos de la unidad: {nombre}'
                )

            messages.success(request, 'Unidad Organizacional actualizada correctamente.')
            return redirect('unidades_list')

        except Exception as e:
            messages.error(request, f'Error al actualizar la unidad: {str(e)}')
            return redirect('editar_unidad', id=id)

    return render(request, 'usuarios/editar_unidad.html', {'unidad': unidad})