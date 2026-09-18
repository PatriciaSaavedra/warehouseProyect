import re
from django.core.paginator import Paginator
from django.db import transaction, IntegrityError
from django.db.models import Q
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.models import User
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.validators import validate_email
from django.core.exceptions import ValidationError

from .models import PerfilUsuario, ROLES
from organizacion.models import Secretaria, UnidadAdministrativa, UnidadOrganizacional
from auditoria.models import Bitacora
from usuarios.decorators import rol_requerido
from inventario.models import Almacen



# ==========================================
# FUNCIONES AUXILIARES DE VALIDACIÓN Y NORMALIZACIÓN
# ==========================================

def normalizar_espacios(texto):
    if not texto:
        return ""
    return re.sub(r'\s+', ' ', texto.strip())


def capitalizar_nombre_propio(texto):
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

def validar_datos_usuario(datos, es_creacion=False, user_id=None, almacenes=None):
    """
    Valida y normaliza de forma exhaustiva todos los campos de un usuario.
    
    Asegura:
    - Normalización de espacios y formato Tipo Título (Title Case) en nombres [11].
    - Username obligatorio, único, sin espacios y con límites estrictos [11, 28].
    - Email obligatorio, de formato válido, único y con protección anti-spam [11, 28].
    - Fuerza estricta de contraseña (complejidad, longitud mínima de 8, sin coincidir con username) [11].
    - Validación de rol existente y consistente en el sistema [11].
    - Consistencia jerárquica obligatoria (Secretaría -> Unidad Administrativa) [28].
    """
    errores = []

    # ==========================================
    # 1. NORMALIZACIÓN DE CAMPOS INICIALES
    # ==========================================
    username = datos.get('username', '').strip().lower()
    first_name = capitalizar_nombre_propio(normalizar_espacios(datos.get('first_name', '')))
    last_name = capitalizar_nombre_propio(normalizar_espacios(datos.get('last_name', '')))
    email = normalizar_espacios(datos.get('email', '')).lower()
    rol = datos.get('rol', '').strip()
    
    # Rescatamos los IDs de la estructura orgánica
    secretaria_id = datos.get('secretaria', '').strip()
    unidad_id = datos.get('unidad', '').strip()

    # Expresión regular anti-spam (Detecta más de 3 letras idénticas seguidas, ej: "ssss")
    regex_spam_letras = r"(.)\1{3,}"


    # ==========================================
    # 2. VALIDACIÓN DEL USERNAME
    # ==========================================
    if not username:
        errores.append("El nombre de usuario (username) es obligatorio.")
    else:
        # Evitar espacios intermedios
        if " " in username:
            errores.append("El nombre de usuario no puede contener espacios en blanco.")
        # Longitud estricta (4 a 30 caracteres)
        elif len(username) < 4 or len(username) > 30:
            errores.append("El nombre de usuario debe tener entre 4 y 30 caracteres.")
        # Caracteres válidos (letras minúsculas, números, guiones y guión bajo)
        elif not re.match(r"^[a-z0-9_\-]+$", username):
            errores.append("El nombre de usuario solo puede contener letras, números, guiones (-) y guiones bajos (_).")
        
        # Comprobar unicidad (Evitar duplicaciones, insensible a mayúsculas)
        query_username = User.objects.filter(username__iexact=username)
        if not es_creacion and user_id:
            query_username = query_username.exclude(id=user_id)
        if query_username.exists():
            errores.append("El nombre de usuario ya está registrado por otra cuenta.")


    # ==========================================
    # 3. VALIDACIÓN DE NOMBRES Y APELLIDOS
    # ==========================================
    # --- NOMBRES ---
    if not first_name:
        errores.append("El nombre es obligatorio y no puede contener únicamente espacios.")
    else:
        # Longitud (2 a 40 caracteres)
        if len(first_name) < 2 or len(first_name) > 40:
            errores.append("El nombre debe tener entre 2 y 40 caracteres.")
        # Evitar que sea puramente numérico
        if first_name.isdigit():
            errores.append("El nombre no puede ser únicamente numérico.")
        # Caracteres permitidos (letras, tildes, guión y comilla simple)
        elif not re.match(r"^[a-zA-ZáéíóúüñÁÉÍÓÚÜÑ\s\-\']+$", first_name):
            errores.append("El nombre contiene caracteres no permitidos.")
        # Filtro Anti-Spam
        elif re.search(regex_spam_letras, first_name):
            errores.append("El nombre ingresado no es válido debido a una repetición excesiva de caracteres (anti-spam).")

    # --- APELLIDOS ---
    if not last_name:
        errores.append("El apellido es obligatorio y no puede contener únicamente espacios.")
    else:
        # Longitud (2 a 40 caracteres)
        if len(last_name) < 2 or len(last_name) > 40:
            errores.append("El apellido debe tener entre 2 y 40 caracteres.")
        # Evitar que sea puramente numérico
        if last_name.isdigit():
            errores.append("El apellido no puede ser únicamente numérico.")
        # Caracteres permitidos
        elif not re.match(r"^[a-zA-ZáéíóúüñÁÉÍÓÚÜÑ\s\-\']+$", last_name):
            errores.append("El apellido contiene caracteres no permitidos.")
        # Filtro Anti-Spam
        elif re.search(regex_spam_letras, last_name):
            errores.append("El apellido ingresado no es válido debido a una repetición excesiva de caracteres (anti-spam).")


    # ==========================================
    # 4. VALIDACIÓN DE EMAIL
    # ==========================================
    if not email:
        errores.append("El correo electrónico es obligatorio para el registro institucional.")
    else:
        # Longitud (5 a 100 caracteres)
        if len(email) < 5 or len(email) > 100:
            errores.append("El correo electrónico debe tener entre 5 y 100 caracteres.")
        else:
            # Estructura del correo electrónico
            try:
                validate_email(email)
            except ValidationError:
                errores.append("El formato del correo electrónico no es válido (ejemplo: usuario@institucion.gob.bo).")
        
        # Filtro Anti-Spam
        if re.search(regex_spam_letras, email):
            errores.append("El correo electrónico ingresado no es válido debido a una repetición excesiva de caracteres (anti-spam).")

        # Evitar correos duplicados
        query_email = User.objects.filter(email__iexact=email)
        if not es_creacion and user_id:
            query_email = query_email.exclude(id=user_id)
        if query_email.exists():
            errores.append("El correo electrónico ya está registrado por otro funcionario en el sistema.")


    # ==========================================
    # 5. VALIDACIÓN DE CONTRASEÑA (Solo Creación)
    # ==========================================
    password = ""
    if es_creacion:
        password = datos.get('password', '')
        password_confirm = datos.get('password_confirm', '')

        if not password:
            errores.append("La contraseña es obligatoria.")
        else:
            # Longitud mínima de 8 caracteres y máxima de 128
            if len(password) < 8 or len(password) > 128:
                errores.append("La contraseña debe tener entre 8 y 128 caracteres.")
            
            # Espacios en los extremos
            if password.strip() != password:
                errores.append("La contraseña no debe comenzar ni terminar con espacios en blanco.")
            
            # Evitar contraseñas únicamente numéricas o alfabéticas
            if password.isdigit():
                errores.append("La contraseña es demasiado simple. No puede contener únicamente números.")
            elif password.isalpha():
                errores.append("La contraseña es demasiado simple. No puede contener únicamente letras.")
            
            # Evaluar complejidad (Al menos una mayúscula, una minúscula y un número de forma independiente)
            if not re.search(r"[A-Z]", password) or not re.search(r"[a-z]", password) or not re.search(r"[0-9]", password):
                errores.append("La contraseña es débil. Debe incluir al menos una letra mayúscula, una letra minúscula y un número.")
            
            # Evitar que la contraseña contenga el username
            if username and username in password.lower():
                errores.append("La contraseña no es segura porque contiene su nombre de usuario.")

        if password != password_confirm:
            errores.append("La contraseña y su confirmación no coinciden.")


    # ==========================================
    # 6. VALIDACIÓN DEL ROL DE SISTEMA
    # ==========================================
    roles_validos = [r[0] for r in ROLES]
    if not rol:
        errores.append("El rol del usuario es obligatorio.")
    elif rol not in roles_validos:
        errores.append("El rol seleccionado no es válido en el sistema.")

    # ==========================================
    # 6.1. VALIDACIÓN DE ALMACENES SEGÚN EL ROL
    # ==========================================
    almacenes = almacenes or []
    if rol == 'ALMACENERO' and not almacenes:
        errores.append(
            "Un usuario con rol Almacenero debe tener al menos un almacén o subalmacén asignado."
        )


    # ==========================================
    # 7. VALIDACIÓN DE RELACIÓN SECRETARÍA -> UNIDAD
    # ==========================================
    if not secretaria_id:
        errores.append("La selección de la Secretaría Departamental es obligatoria para registrar al funcionario.")
    
    if not unidad_id:
        errores.append("La selección de la Unidad Administrativa es obligatoria para registrar al funcionario.")

    secretaria_obj = None
    unidad_obj = None

    if secretaria_id:
        try:
            secretaria_obj = Secretaria.objects.get(id=secretaria_id)
        except (Secretaria.DoesNotExist, ValueError):
            errores.append("La Secretaría seleccionada no existe en el sistema.")

    if unidad_id:
        try:
            unidad_obj = UnidadAdministrativa.objects.get(id=unidad_id)
        except (UnidadAdministrativa.DoesNotExist, ValueError):
            errores.append("La Unidad Administrativa seleccionada no existe en el sistema.")

    # Evitar asociaciones inexistentes o incoherentes
    if secretaria_obj and unidad_obj:
        if unidad_obj.secretaria != secretaria_obj:
            errores.append("La Unidad Administrativa seleccionada no pertenece a la Secretaría indicada.")


    # ==========================================
    # 8. ESTRUCTURA DE RETORNO CONSOLIDADA
    # ==========================================
    datos_normalizados = {
        'username': username,
        'first_name': first_name,
        'last_name': last_name,
        'email': email,
        'rol': rol,
        'secretaria_id': secretaria_id if secretaria_id else None,
        'unidad_id': unidad_id if unidad_id else None,
        'almacenes': almacenes,
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
    """
    Lista y filtra de manera avanzada los funcionarios de la Gobernación.
    Ordenamiento predeterminado: Último creado primero (Descendente) [28].
    """
    # 1. Rescatar parámetros de búsqueda y filtros
    query = request.GET.get('q', '').strip()
    rol_filter = request.GET.get('rol', '').strip()
    secretaria_filter = request.GET.get('secretaria', '').strip()
    estado_filter = request.GET.get('estado', '').strip()

    # Optimizamos la consulta con select_related para evitar el problema de N+1 consultas [28]
    usuarios = User.objects.select_related('perfilusuario', 'perfilusuario__secretaria', 'perfilusuario__unidad').all()

    # 2. Aplicar Búsqueda por Texto (Username, Nombre, Apellido, Correo) [11, 28]
    if query:
        usuarios = usuarios.filter(
            Q(first_name__icontains=query) |
            Q(last_name__icontains=query) |
            Q(username__icontains=query) |
            Q(email__icontains=query)
        )

    # 3. Filtrar por Rol del sistema
    if rol_filter:
        usuarios = usuarios.filter(perfilusuario__rol=rol_filter)

    # 4. Filtrar por Secretaría Departamental de pertenencia
    if secretaria_filter:
        usuarios = usuarios.filter(perfilusuario__secretaria_id=secretaria_filter)

    # 5. Filtrar por Estado (Activo / Inactivo)
    if estado_filter == 'activo':
        usuarios = usuarios.filter(is_active=True)
    elif estado_filter == 'inactivo':
        usuarios = usuarios.filter(is_active=False)

    # 6. ORDENAMIENTO: Lo último creado primero (Descendente por fecha de unión) [28]
    usuarios = usuarios.order_by('-date_joined')

    # 7. Paginación de resultados (10 por página) [28]
    paginator = Paginator(usuarios, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        'usuarios/index.html',
        {
            'page_obj': page_obj,
            'query': query,
            'rol_filter': rol_filter,
            'secretaria_filter': secretaria_filter,
            'estado_filter': estado_filter,
            'roles': ROLES,
            'secretarias': Secretaria.objects.all(),
        }
    )


@login_required
@rol_requerido(['ADMINISTRADOR'])
def crear_usuario_view(request):
    unidades = UnidadAdministrativa.objects.all()
    secretarias = Secretaria.objects.all()
    almacenes = Almacen.objects.filter(is_active=True).order_by('nombre')  # <--- Obtener almacenes activos

    if request.method == 'POST':
        almacenes_seleccionados = request.POST.getlist('almacenes')  # <--- Capturar IDs de almacenes
        datos_normalizados, errores = validar_datos_usuario(request.POST, es_creacion=True, almacenes=almacenes_seleccionados)

        if errores:
            for error in errores:
                messages.error(request, error)
            return render(
                request,
                'usuarios/crear.html',
                {
                    'roles': ROLES,
                    'unidades': unidades,
                    'secretarias': secretarias,
                    'almacenes': almacenes,  # <--- Pasar al contexto de error
                    'almacenes_autorizados_ids': [int(x) for x in almacenes_seleccionados],
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

                perfil = PerfilUsuario.objects.create(
                    user=user,
                    rol=datos_normalizados['rol'],
                    secretaria_id=datos_normalizados['secretaria_id'],
                    unidad_id=datos_normalizados['unidad_id']
                )

                # Asignar los almacenes autorizados al perfil recién creado
                if almacenes_seleccionados:
                    perfil.almacenes_autorizados.set(almacenes_seleccionados)

                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Usuarios',
                    accion='Crear usuario',
                    descripcion=f'Se creó el usuario {datos_normalizados["username"]} con rol {datos_normalizados["rol"]} y almacenes autorizados.'
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
                    'secretarias': secretarias,
                    'almacenes': almacenes,
                    'almacenes_autorizados_ids': [int(x) for x in almacenes_seleccionados],
                    'valores': request.POST
                }
            )
        except Exception:
            messages.error(request, 'Ocurrió un error inesperado al registrar el usuario.')
            return redirect('crear_usuario')

    return render(
        request,
        'usuarios/crear.html',
        {
            'roles': ROLES,
            'unidades': unidades,
            'secretarias': secretarias,
            'almacenes': almacenes,  # <--- Pasar al formulario GET
            'almacenes_autorizados_ids': []
        }
    )
# VISTA DE EDICIÓN
@login_required
@rol_requerido(['ADMINISTRADOR'])
def editar_usuario_view(request, user_id):
    usuario = get_object_or_404(User, id=user_id)
    # Asegurar que el usuario siempre tenga un perfil asociado
    try:
        perfil = usuario.perfilusuario
    except PerfilUsuario.DoesNotExist:
        perfil = PerfilUsuario.objects.create(user=usuario)
    unidades = UnidadAdministrativa.objects.all()
    secretarias = Secretaria.objects.all()
    almacenes = Almacen.objects.filter(is_active=True).order_by('nombre')

    if request.method == 'POST':
        almacenes_seleccionados = request.POST.getlist('almacenes')
        datos_normalizados, errores = validar_datos_usuario(
            request.POST, es_creacion=False, user_id=user_id,
            almacenes=almacenes_seleccionados
        )

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
                    'secretarias': secretarias,
                    'almacenes': almacenes,
                    'almacenes_autorizados_ids': [int(x) for x in almacenes_seleccionados],
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
                perfil.secretaria_id = datos_normalizados['secretaria_id']
                perfil.unidad_id = datos_normalizados['unidad_id']
                perfil.save()

                # Guardar los almacenes autorizados seleccionados.
                # Para ADMINISTRADOR/superusuario se conservan los existentes (acceso global).
                if datos_normalizados['rol'] != 'ADMINISTRADOR' and not usuario.is_superuser:
                    perfil.almacenes_autorizados.set(almacenes_seleccionados)

                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Usuarios',
                    accion='Editar usuario',
                    descripcion=f'Se modificó el usuario {usuario.username}'
                )

            messages.success(request, 'Usuario actualizado correctamente.')
            return redirect('usuarios')

        except IntegrityError:
            messages.error(request, 'Error de integridad: El nombre de usuario o correo ya existe.')
            return render(
                request,
                'usuarios/editar.html',
                {
                    'usuario_obj': usuario,
                    'perfil': perfil,
                    'roles': ROLES,
                    'unidades': unidades,
                    'secretarias': secretarias,
                    'almacenes': almacenes,
                    'almacenes_autorizados_ids': [int(x) for x in almacenes_seleccionados],
                    'valores': request.POST
                }
            )
        except Exception:
            messages.error(request, 'Error al actualizar el usuario.')
            return redirect('editar_usuario', user_id=user_id)

    return render(
        request,
        'usuarios/editar.html',
        {
            'usuario_obj': usuario,
            'perfil': perfil,
            'roles': ROLES,
            'unidades': unidades,
            'secretarias': secretarias,
            'almacenes': almacenes,
            'almacenes_autorizados_ids': list(perfil.almacenes_autorizados.values_list('id', flat=True))
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
            # Longitud mínima de 8 caracteres y máxima de 128
            if len(nueva_password) < 8 or len(nueva_password) > 128:
                errores.append('La nueva contraseña debe tener entre 8 y 128 caracteres.')
            
            if nueva_password.strip() != nueva_password:
                errores.append('La contraseña no debe iniciar ni finalizar con espacios en blanco.')
            # Complejidad
            if nueva_password.isdigit():
                errores.append("La contraseña es demasiado simple. No puede contener únicamente números.")
            elif nueva_password.isalpha():
                errores.append("La contraseña es demasiado simple. No puede contener únicamente letras.")
            
            # Cambiado de "elif" a "if" para evaluar de forma independiente
            if not re.search(r"[A-Z]", nueva_password) or not re.search(r"[a-z]", nueva_password) or not re.search(r"[0-9]", nueva_password):
                errores.append("La contraseña es débil. Debe incluir al menos una letra mayúscula, una letra minúscula y un número.")
            
            if usuario.username and usuario.username.lower() in nueva_password.lower():
                errores.append("La contraseña no puede contener el nombre de usuario de la cuenta.")

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
    try:
        perfil = usuario.perfilusuario
    except PerfilUsuario.DoesNotExist:
        perfil = None

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
            elif not re.match(r"^[a-zA-ZáéíóúüñÁÉÍÓÚÜÑ\s\-\']+$", first_name):
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
            elif not re.match(r"^[a-zA-ZáéíóúüñÁÉÍÓÚÜÑ\s\-\']+$", last_name):
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
    unidades = UnidadOrganizacional.objects.prefetch_related('almacenes_que_atienden')

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
    """
    Registra una nueva Unidad de forma atómica asociándola a una Secretaría activa.
    """
    # MODIFICADO: Solo cargamos secretarías activas para evitar asociar unidades a áreas dadas de baja
    secretarias = Secretaria.objects.filter(is_active=True).order_by('nombre')

    if request.method == 'POST':
        nombre = normalizar_espacios(request.POST.get('nombre', ''))
        codigo_sigep = normalizar_espacios(request.POST.get('codigo_sigep', ''))
        secretaria_id = request.POST.get('secretaria', '').strip()

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
                    codigo_sigep=codigo_sigep if codigo_sigep else None,
                    secretaria_id=secretaria_id if secretaria_id else None
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

    return render(request, 'usuarios/crear_unidad.html', {'secretarias': secretarias})
@login_required
@rol_requerido(['ADMINISTRADOR'])
def editar_unidad_view(request, id):
    """
    Modifica los datos de una Unidad Organizacional existente.
    """
    unidad = get_object_or_404(UnidadOrganizacional, id=id)
    # MODIFICADO: Solo cargamos secretarías activas
    secretarias = Secretaria.objects.filter(is_active=True).order_by('nombre')

    if request.method == 'POST':
        nombre = normalizar_espacios(request.POST.get('nombre', ''))
        codigo_sigep = normalizar_espacios(request.POST.get('codigo_sigep', ''))
        secretaria_id = request.POST.get('secretaria', '').strip()

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
                unidad.secretaria_id = secretaria_id if secretaria_id else None
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

    return render(request, 'usuarios/editar_unidad.html', {'unidad': unidad, 'secretarias': secretarias})
# ==========================================
# CRUD DE SECRETARÍAS (NUEVAS VISTAS)
# ==========================================

@login_required
@rol_requerido(['ADMINISTRADOR'])
def secretarias_list_view(request):
    """
    Lista y busca las Secretarías Departamentales registradas en el sistema.
    """
    query = request.GET.get('q', '').strip()
    secretarias = Secretaria.objects.all()

    if query:
        secretarias = secretarias.filter(
            Q(nombre__icontains=query) |
            Q(codigo__icontains=query)
        )

    secretarias = secretarias.order_by('nombre')

    paginator = Paginator(secretarias, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(
        request,
        'usuarios/secretarias_list.html',
        {
            'page_obj': page_obj,
            'query': query
        }
    )


@login_required
@rol_requerido(['ADMINISTRADOR'])
def crear_secretaria_view(request):
    """
    Registra una nueva Secretaría Departamental de manera atómica.
    """
    if request.method == 'POST':
        nombre = normalizar_espacios(request.POST.get('nombre', ''))
        codigo = normalizar_espacios(request.POST.get('codigo', '')).upper()

        if not nombre:
            messages.error(request, 'El nombre de la secretaría es obligatorio.')
            return redirect('crear_secretaria')

        if Secretaria.objects.filter(nombre__iexact=nombre).exists():
            messages.error(request, 'Ya existe una Secretaría registrada con este nombre.')
            return redirect('crear_secretaria')

        try:
            with transaction.atomic():
                Secretaria.objects.create(
                    nombre=nombre,
                    codigo=codigo if codigo else None
                )
                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Organización',
                    accion='Crear Secretaría',
                    descripcion=f'Se creó la Secretaría Departamental: {nombre}'
                )

            messages.success(request, 'Secretaría Departamental creada correctamente.')
            return redirect('secretarias_list')

        except Exception as e:
            messages.error(request, f'Error al registrar la secretaría: {str(e)}')
            return redirect('crear_secretaria')

    return render(request, 'usuarios/crear_secretaria.html')


@login_required
@rol_requerido(['ADMINISTRADOR'])
def editar_secretaria_view(request, id):
    """
    Modifica los datos de una Secretaría Departamental existente.
    Recupera los datos de forma segura tanto en GET como ante fallos en POST [11, 28].
    """
    secretaria_real = get_object_or_404(Secretaria, id=id)

    if request.method == 'POST':
        nombre = normalizar_espacios(request.POST.get('nombre', ''))
        codigo = normalizar_espacios(request.POST.get('codigo', '')).upper()

        # Si el nombre viene vacío, mostramos error conservando lo que se escribió
        if not nombre:
            messages.error(request, 'El nombre de la secretaría es un campo obligatorio.')
            return render(request, 'usuarios/editar_secretaria.html', {
                'secretaria': {'id': id, 'nombre': nombre, 'codigo': codigo}
            })

        # Si el nombre ya existe en otra secretaría, mostramos error conservando los datos
        if Secretaria.objects.filter(nombre__iexact=nombre).exclude(id=id).exists():
            messages.error(request, 'Ya existe otra Secretaría registrada con ese nombre.')
            return render(request, 'usuarios/editar_secretaria.html', {
                'secretaria': {'id': id, 'nombre': nombre, 'codigo': codigo}
            })

        try:
            with transaction.atomic():
                secretaria_real.nombre = nombre
                secretaria_real.codigo = codigo if codigo else None
                secretaria_real.save()

                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Organización',
                    accion='Editar Secretaría',
                    descripcion=f'Se actualizaron los datos de la secretaría: {nombre}'
                )

            messages.success(request, 'Secretaría actualizada correctamente.')
            return redirect('secretarias_list')

        except Exception as e:
            messages.error(request, f'Error al actualizar la secretaría: {str(e)}')
            return render(request, 'usuarios/editar_secretaria.html', {
                'secretaria': {'id': id, 'nombre': nombre, 'codigo': codigo}
            })

    # Al cargar la página por primera vez (GET), pasamos el objeto real de la Base de Datos
    return render(request, 'usuarios/editar_secretaria.html', {'secretaria': secretaria_real})
@login_required
@rol_requerido(['ADMINISTRADOR'])
def toggle_secretaria_view(request, id):
    """
    Da de baja (Desactiva) o reactiva de manera lógica una Secretaría Departamental.
    Si se desactiva, de forma automática se desactivan todas sus unidades dependientes.
    """
    secretaria = get_object_or_404(Secretaria, id=id)
    nuevo_estado = not secretaria.is_active

    try:
        with transaction.atomic():
            secretaria.is_active = nuevo_estado
            secretaria.save()

            # MODIFICADO: Baja lógica en cascada para evitar unidades activas de secretarias inactivas
            detalle_unidades = ""
            if not nuevo_estado:
                unidades_activas = secretaria.unidades_organizacionales.filter(is_active=True)
                cantidad_unidades = unidades_activas.count()
                unidades_activas.update(is_active=False)
                if cantidad_unidades > 0:
                    detalle_unidades = f" y se desactivaron {cantidad_unidades} unidades dependientes"

            estado_texto = 'reactivada' if nuevo_estado else 'desactivada (dada de baja)'

            Bitacora.objects.create(
                usuario=request.user,
                modulo='Organización',
                accion='Cambio de estado Secretaría',
                descripcion=f'Se cambió el estado a {estado_texto} de la secretaría: {secretaria.nombre}{detalle_unidades}.'
            )

        messages.success(request, f'La Secretaría ha sido {estado_texto} correctamente.')
    except Exception as e:
        messages.error(request, f'Error al cambiar el estado de la secretaría: {str(e)}')

    return redirect('secretarias_list')

# ==========================================
# BAJA LÓGICA DE UNIDADES (NUEVA VISTA)
# ==========================================
@login_required
@rol_requerido(['ADMINISTRADOR'])
def toggle_unidad_view(request, id):
    """
    Da de baja (Desactiva) o reactiva de manera lógica una Unidad Administrativa.
    Evita reactivar una unidad si su secretaría de pertenencia está desactivada.
    """
    unidad = get_object_or_404(UnidadOrganizacional, id=id)
    nuevo_estado = not unidad.is_active

    # MODIFICADO: Validación de consistencia jerárquica
    if nuevo_estado and unidad.secretaria and not unidad.secretaria.is_active:
        messages.error(
            request, 
            f"No es posible activar la unidad '{unidad.nombre}' porque la Secretaría '{unidad.secretaria.nombre}' está desactivada."
        )
        return redirect('unidades_list')

    try:
        with transaction.atomic():
            unidad.is_active = nuevo_estado
            unidad.save()

            estado = 'reactivada' if nuevo_estado else 'desactivada (dada de baja)'

            Bitacora.objects.create(
                usuario=request.user,
                modulo='Organización',
                accion='Cambio de estado Unidad',
                descripcion=f'Se cambió el estado a {estado} de la unidad: {unidad.nombre}'
            )

        messages.success(request, f'La Unidad Organizacional ha sido {estado} correctamente.')
    except Exception as e:
        messages.error(request, f'Error al cambiar el estado de la unidad: {str(e)}')

    return redirect('unidades_list')