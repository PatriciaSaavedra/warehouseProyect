import json
import html
from decimal import Decimal, InvalidOperation
import datetime
from django.urls import reverse
from django.utils import timezone
from django.http import JsonResponse, HttpResponse, HttpResponseForbidden, request
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.db import transaction, DatabaseError
from django.contrib import messages
from django.db.models import Q, Sum, F
from django.core.paginator import Paginator
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction, DatabaseError
import uuid
from django.conf import settings

from datetime import date

from django.db import transaction, DatabaseError, IntegrityError
from django.utils.dateparse import parse_date

from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, landscape
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics import renderPDF
from inventario.services import calcular_desglose_peps
from django.contrib.auth.decorators import login_required

from solicitudes.models import Solicitud

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter, landscape
from reportlab.platypus import Table, TableStyle
from reportlab.lib import colors
from reportlab.graphics.shapes import Drawing
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from django.views.decorators.http import require_POST
from organizacion.models import UnidadOrganizacional
from presupuestos.models import POA
from auditoria.models import Bitacora

from .models import Solicitud, DetalleSolicitud, ESTADOS_SOLICITUD, FLUJOS_ATENCION
from inventario.models import (
    InventarioAlmacen, Material, MovimientoInventario,
    PartidaPresupuestaria, UnidadMedida, Almacen,
    AsignacionMaterialUnidad, AsignacionEntradaUnidad
)
try:
    from inventario.models import AsignacionMaterialUnidad, AsignacionEntradaUnidad
except ImportError:
    AsignacionMaterialUnidad = None
    AsignacionEntradaUnidad = None
from inventario.services import registrar_salida_valorada_peps
from solicitudes.services import liberar_reservas_solicitud
from usuarios.decorators import tiene_rol, rol_requerido

GESTION_ACTUAL = timezone.now().year

# ========================================================
# REDIRECCIÓN INTELIGENTE
# ========================================================
def redirigir_despues_de_accion(request, solicitud):
    perfil = getattr(request.user, 'perfilusuario', None)
    rol = perfil.rol if perfil else 'UNIDAD_SOLICITANTE'

    if rol == 'UNIDAD_SOLICITANTE':
        destino_default = 'solicitudes'
    else:
        destino_default = 'solicitudes_general'

    referer = request.META.get('HTTP_REFERER', '')
    if 'detalle' in referer or 'revisar' in referer or 'rechazar' in referer:
        return redirect('detalle_solicitud', id=solicitud.id)

    return redirect(referer if referer else destino_default)


# ========================================================
# VISTAS DE SOLICITUDES
# ========================================================

@login_required
def solicitudes(request):
    """
    Pestaña Personal: Muestra ÚNICAMENTE las solicitudes creadas por el usuario autenticado
    (tanto Pedidos de Almacén como Nuevas Adquisiciones propias).
    """
    perfil = getattr(request.user, 'perfilusuario', None)
    rol = perfil.rol if perfil else 'UNIDAD_SOLICITANTE'

    tab_activa = request.GET.get('tab', 'pedidos')
    query = request.GET.get('q', '').strip()

    # 1. Flujo 1 PROPIO: Solo lo creado por este usuario
    pedidos_qs = Solicitud.objects.filter(solicitante=request.user)

    # 2. Flujo 2 PROPIO: Solo lo creado por este usuario (¡SIN EXCEPCIÓN DE ROL!)
    from compras.models import ProcesoAdquisicion
    adquisiciones_qs = ProcesoAdquisicion.objects.filter(solicitante=request.user)

    if query:
        pedidos_qs = pedidos_qs.filter(
            Q(codigo__icontains=query) | Q(justificacion__icontains=query)
        )
        adquisiciones_qs = adquisiciones_qs.filter(
            Q(codigo__icontains=query) | Q(objeto_contratacion__icontains=query)
        )

    pedidos_qs = pedidos_qs.order_by('-id')
    adquisiciones_qs = adquisiciones_qs.order_by('-id')

    paginator_ped = Paginator(pedidos_qs, 10)
    page_pedidos = paginator_ped.get_page(request.GET.get('page_ped'))

    paginator_adq = Paginator(adquisiciones_qs, 10)
    page_adq = paginator_adq.get_page(request.GET.get('page_adq'))

    return render(request, 'solicitudes/index_propias.html', {
        'page_pedidos': page_pedidos,
        'page_adq': page_adq,
        'tab_activa': tab_activa,
        'query': query,
        'total_pedidos': pedidos_qs.count(),
        'total_adquisiciones': adquisiciones_qs.count(),
        'rol': rol,
    })
@login_required
@rol_requerido([
    'JEFE_INMEDIATO', 'SECRETARIO_SAF', 'PRESUPUESTOS', 'RPA',
    'JEFE_ADMINISTRATIVO', 'ALMACENERO', 'KARDISTA', 'ADMINISTRADOR',
    'ADMIN_ALMACENES', 'BIENES_SERVICIOS'
])
def solicitudes_general(request):
    """
    Bandeja de Gestión Institucional:
    AISLAMIENTO ESTRICTO: Los Subalmacenes (ej. SIGEPRO, UNASBA) SOLO ven y despachan
    solicitudes de las unidades organizacionales autorizadas bajo su custodia.
    """
    perfil = request.user.perfilusuario
    rol = perfil.rol
    unidad = perfil.unidad

    # 1. Captura de filtros desde la URL
    query = request.GET.get('q', '').strip()
    filtro_estado = request.GET.get('estado', '').strip()
    desde_str = request.GET.get('desde', '').strip()
    hasta_str = request.GET.get('hasta', '').strip()

    # 2. Control de pestañas según competencia de Rol
    roles_solo_compras = ['RPA', 'BIENES_SERVICIOS', 'PRESUPUESTOS', 'SECRETARIO_SAF']
    roles_almacen = ['ALMACENERO', 'KARDISTA']

    mostrar_tab_pedidos = True
    mostrar_tab_adquisiciones = True

    if rol in roles_solo_compras:
        mostrar_tab_pedidos = False
        tab_defecto = 'adquisiciones'
    elif rol in roles_almacen:
        tab_defecto = 'pedidos'
    else:
        tab_defecto = 'pedidos'

    tab_activa = request.GET.get('tab', tab_defecto)
    if not mostrar_tab_pedidos and tab_activa == 'pedidos':
        tab_activa = 'adquisiciones'

    # Roles con supervisión administrativa general
    roles_supervisores = [
        'ADMINISTRADOR', 'ADMIN_ALMACENES', 'RPA',
        'SECRETARIO_SAF', 'PRESUPUESTOS', 'BIENES_SERVICIOS'
    ]

    # =========================================================
    # 3. DETERMINAR UNIDADES ATENDIDAS POR EL ALMACÉN DEL USUARIO
    # =========================================================
    almacenes_usuario = perfil.almacenes_autorizados.filter(is_active=True)
    if not almacenes_usuario.exists():
        almacenes_usuario = Almacen.objects.filter(responsable=request.user, is_active=True)
    if perfil.unidad and not almacenes_usuario.exists():
        almacenes_usuario = Almacen.objects.filter(unidad_organizacional=perfil.unidad, is_active=True)

    es_central = (
        almacenes_usuario.filter(tipo='CENTRAL').exists() or
        rol in ['ADMINISTRADOR', 'ADMIN_ALMACENES']
    )

    unidades_subalmacen_ids = set()
    if not es_central and rol in roles_almacen:
        for alm in almacenes_usuario:
            unidades_subalmacen_ids.update(alm.unidades_atendidas.filter(is_active=True).values_list('id', flat=True))
            if alm.unidad_organizacional_id:
                unidades_subalmacen_ids.add(alm.unidad_organizacional_id)

    # =========================================================
    # 4. CONSULTA FLUJO 1 (PEDIDOS DE ALMACÉN) - AISLADO
    # =========================================================
    if rol in roles_supervisores or (rol in roles_almacen and es_central):
        # Administradores y Almacén Central ven el universo general
        pedidos_base = Solicitud.objects.all()
    elif rol in roles_almacen and not es_central:
        # SUBALMACÉN (SIGEPRO, UNASBA): SOLO solicitudes de SUS unidades atendidas
        pedidos_base = Solicitud.objects.filter(unidad_solicitante_id__in=unidades_subalmacen_ids)
    else:
        # Jefe inmediato u oficina solicitante: solo su unidad
        pedidos_base = Solicitud.objects.filter(unidad_solicitante=unidad)

    pedidos_qs = pedidos_base

    if query:
        pedidos_qs = pedidos_qs.filter(
            Q(codigo__icontains=query) |
            Q(solicitante__username__icontains=query) |
            Q(unidad_solicitante__nombre__icontains=query) |
            Q(justificacion__icontains=query)
        )
    if filtro_estado:
        pedidos_qs = pedidos_qs.filter(estado=filtro_estado)
    if desde_str:
        pedidos_qs = pedidos_qs.filter(fecha__gte=desde_str)
    if hasta_str:
        pedidos_qs = pedidos_qs.filter(fecha__lte=hasta_str)

    pedidos_qs = pedidos_qs.select_related('solicitante', 'unidad_solicitante').order_by('-id')

    # =========================================================
    # 5. CONSULTA FLUJO 2 (NUEVAS ADQUISICIONES SABS) - AISLADO
    # =========================================================
    from compras.models import ProcesoAdquisicion

    if rol in roles_supervisores or (rol in roles_almacen and es_central):
        adquisiciones_base = ProcesoAdquisicion.objects.all()
    elif rol in roles_almacen and not es_central:
        adquisiciones_base = ProcesoAdquisicion.objects.filter(unidad_solicitante_id__in=unidades_subalmacen_ids)
    else:
        adquisiciones_base = ProcesoAdquisicion.objects.filter(unidad_solicitante=unidad)

    adquisiciones_qs = adquisiciones_base

    if query:
        adquisiciones_qs = adquisiciones_qs.filter(
            Q(codigo__icontains=query) |
            Q(objeto_contratacion__icontains=query) |
            Q(cite_solicitud__icontains=query) |
            Q(unidad_solicitante__nombre__icontains=query)
        )
    if filtro_estado:
        adquisiciones_qs = adquisiciones_qs.filter(estado=filtro_estado)
    if desde_str:
        adquisiciones_qs = adquisiciones_qs.filter(fecha_solicitud__gte=desde_str)
    if hasta_str:
        adquisiciones_qs = adquisiciones_qs.filter(fecha_solicitud__lte=hasta_str)

    adquisiciones_qs = adquisiciones_qs.select_related('solicitante', 'unidad_solicitante', 'autoridad_rpa').prefetch_related('items').order_by('-id')

    # =========================================================
    # 6. KPIS CONTEXTUALES ACOTADOS A LA JURISDICCIÓN DEL ROL
    # =========================================================
    hoy = timezone.now().date()

    if rol == 'RPA':
        pendientes_accion = ProcesoAdquisicion.objects.filter(estado='CERTIFICADA').count()
        accion_label = "Esperando su Aprobación RPA"
        en_curso = ProcesoAdquisicion.objects.filter(estado__in=['CHECKLIST_VERIFICADO', 'NOTA_SAF_EMITIDA', 'CERTIFICADA', 'APROBADA_RPA', 'ORDEN_EMITIDA']).count()
        concluidos_mes = ProcesoAdquisicion.objects.filter(estado='CONCLUIDA', fecha_recepcion__month=hoy.month).count()
        total_gestion = adquisiciones_qs.count()

    elif rol == 'PRESUPUESTOS':
        pendientes_accion = ProcesoAdquisicion.objects.filter(estado='NOTA_SAF_EMITIDA').count()
        accion_label = "Pendientes de Certificación"
        en_curso = ProcesoAdquisicion.objects.filter(estado__in=['NOTA_SAF_EMITIDA', 'CERTIFICADA']).count()
        concluidos_mes = ProcesoAdquisicion.objects.filter(estado='CONCLUIDA', fecha_recepcion__month=hoy.month).count()
        total_gestion = adquisiciones_qs.count()

    elif rol == 'SECRETARIO_SAF':
        pendientes_accion = ProcesoAdquisicion.objects.filter(estado='CHECKLIST_VERIFICADO').count()
        accion_label = "Pendientes Revisión SAF"
        en_curso = ProcesoAdquisicion.objects.filter(estado__in=['CHECKLIST_VERIFICADO', 'NOTA_SAF_EMITIDA']).count()
        concluidos_mes = ProcesoAdquisicion.objects.filter(estado='CONCLUIDA', fecha_recepcion__month=hoy.month).count()
        total_gestion = adquisiciones_qs.count()

    elif rol == 'BIENES_SERVICIOS':
        pendientes_accion = ProcesoAdquisicion.objects.filter(estado='APROBADA_RPA').count()
        accion_label = "Listos para Emitir Orden"
        en_curso = ProcesoAdquisicion.objects.filter(estado__in=['APROBADA_RPA', 'ORDEN_EMITIDA']).count()
        concluidos_mes = ProcesoAdquisicion.objects.filter(estado='CONCLUIDA', fecha_recepcion__month=hoy.month).count()
        total_gestion = adquisiciones_qs.count()

    elif rol in roles_almacen:
        # Aislado al depósito del almacenero/kardista
        pendientes_accion = pedidos_base.filter(estado='PREPARADA').count() + adquisiciones_base.filter(estado='ORDEN_EMITIDA').count()
        accion_label = "Listos para Entrega/Recepción"
        en_curso = pedidos_base.filter(estado__in=['REVISADA', 'PREPARADA']).count()
        concluidos_mes = pedidos_base.filter(estado='ENTREGADA', fecha_entrega__month=hoy.month).count()
        total_gestion = pedidos_qs.count()

    else:
        pendientes_accion = pedidos_base.filter(estado='REGISTRADA').count()
        accion_label = "Requieren Revisión Inicial"
        en_curso = pedidos_base.filter(estado__in=['REVISADA', 'PREPARADA']).count()
        concluidos_mes = pedidos_base.filter(estado='ENTREGADA', fecha_entrega__month=hoy.month).count()
        total_gestion = pedidos_qs.count() + adquisiciones_qs.count()

    # 7. Paginación
    paginator_ped = Paginator(pedidos_qs, 10)
    page_pedidos = paginator_ped.get_page(request.GET.get('page_ped'))

    paginator_adq = Paginator(adquisiciones_qs, 10)
    page_adq = paginator_adq.get_page(request.GET.get('page_adq'))

    return render(request, 'solicitudes/index_general.html', {
        'page_pedidos': page_pedidos,
        'page_adq': page_adq,
        'tab_activa': tab_activa,
        'mostrar_tab_pedidos': mostrar_tab_pedidos,
        'mostrar_tab_adquisiciones': mostrar_tab_adquisiciones,
        'rol': rol,
        'query': query,
        'filtro_estado': filtro_estado,
        'desde': desde_str,
        'hasta': hasta_str,
        'kpi_pendientes': pendientes_accion,
        'accion_label': accion_label,
        'kpi_en_curso': en_curso,
        'kpi_concluidos': concluidos_mes,
        'kpi_total': total_gestion,
        'total_pedidos': pedidos_qs.count(),
        'total_adquisiciones': adquisiciones_qs.count(),
    })
@login_required
def buscar_materiales(request):
    """
    API de búsqueda estricta (Control Férreo):
    Solo retorna materiales que posean Asignación Directa con saldo disponible
    para la unidad solicitante. Si no tiene asignación (> 0), retorna vacío.
    """
    q = request.GET.get('q', '').strip()
    unidad_id = request.GET.get('unidad_id')

    if not q:
        return JsonResponse([], safe=False)

    perfil = getattr(request.user, 'perfilusuario', None)
    if unidad_id and perfil and perfil.rol in ['ADMINISTRADOR', 'ADMIN_ALMACENES']:
        unidad = UnidadOrganizacional.objects.filter(id=unidad_id).first()
    else:
        unidad = perfil.unidad if perfil else None

    if not unidad:
        return JsonResponse([], safe=False)
    almacenes_autorizados = Almacen.objects.filter(
        unidades_atendidas=unidad,
        is_active=True
    )
    from solicitudes.services import (
        resolver_almacen_para_unidad,
        AlmacenNoDisponible,
        ConfiguracionAlmacenAmbigua,
    )

    try:
        almacen_origen = resolver_almacen_para_unidad(unidad)
    except (AlmacenNoDisponible, ConfiguracionAlmacenAmbigua):
        return JsonResponse([], safe=False)

    if not almacenes_autorizados.exists():
        almacenes_autorizados = Almacen.objects.filter(
            tipo='CENTRAL',
            is_active=True
        )
        # Búsqueda estrictamente acotada a las asignaciones directas de la unidad
    asignaciones = AsignacionEntradaUnidad.objects.filter(
        unidad_organizacional=unidad,
        cantidad_asignada__gt=F('cantidad_retirada'),
        nota_ingreso_detalle__material__is_active=True,
        nota_ingreso_detalle__nota_ingreso__almacen_destino=almacen_origen
    ).filter(
        Q(nota_ingreso_detalle__material__nombre__icontains=q) |
        Q(nota_ingreso_detalle__material__codigo__icontains=q)
    ).select_related(
        'nota_ingreso_detalle__material',
        'nota_ingreso_detalle__material__unidad_medida_fk'
    )
    materiales_dict = {}
    for asig in asignaciones:
        mat = asig.nota_ingreso_detalle.material
        saldo_asig = asig.saldo_disponible

        if saldo_asig > 0:
            if mat.id not in materiales_dict:
                u_med = mat.unidad_medida_fk.codigo if mat.unidad_medida_fk else (mat.unidad_medida or 'UND')
                materiales_dict[mat.id] = {
                    'id': mat.id,
                    'nombre': f"{mat.codigo} - {mat.nombre} (Cupo Asignado: {saldo_asig} {u_med})",
                    'stock': saldo_asig
                }
            else:
                materiales_dict[mat.id]['stock'] += saldo_asig

                u_med = (
                    mat.unidad_medida_fk.codigo
                    if mat.unidad_medida_fk
                    else (mat.unidad_medida or 'UND')
                )

                materiales_dict[mat.id]['nombre'] = (
                    f"{mat.codigo} - {mat.nombre} "
                    f"(Cupo Asignado: {materiales_dict[mat.id]['stock']} {u_med})"
                )
    data = list(materiales_dict.values())[:10]
    return JsonResponse(data, safe=False)
@login_required
def nueva_solicitud(request):
    if request.method == 'POST':
        fecha = request.POST.get('fecha')
        payload_raw = request.POST.get("payload")
        tipo_requerimiento = request.POST.get('tipo_requerimiento', '').strip()

        if not tipo_requerimiento or tipo_requerimiento not in ['BIEN', 'SERVICIO']:
            messages.error(request, "Debe seleccionar un Tipo de Requerimiento válido (Bien o Servicio).")
            return redirect('nueva_solicitud')

        if not payload_raw:
            messages.error(request, "No se recibió información de materiales.")
            return redirect('nueva_solicitud')

        payload_raw = html.unescape(payload_raw)
        try:
            payload = json.loads(payload_raw)
        except json.JSONDecodeError:
            messages.error(request, "Error en el formato de los datos enviados.")
            return redirect('nueva_solicitud')

        if not payload:
            messages.error(request, "Debe agregar al menos un material.")
            return redirect('nueva_solicitud')

        justificacion = request.POST.get('justificacion', '').strip()
        perfil = getattr(request.user, 'perfilusuario', None)
        unidad_solicitante = perfil.unidad if perfil else None

        if not unidad_solicitante:
            messages.error(request, "Su usuario no tiene asignada una Unidad Organizacional.")
            return redirect('nueva_solicitud')

        try:
            with transaction.atomic():
                ultima = Solicitud.objects.select_for_update().order_by('id').last()
                numero = (ultima.id + 1) if ultima else 1
                codigo = f'SOL-{numero:05d}'

                solicitud = Solicitud.objects.create(
                    codigo=codigo,
                    unidad_solicitante=unidad_solicitante,
                    solicitante=request.user,
                    fecha=fecha,
                    justificacion=justificacion,
                    tipo_requerimiento=tipo_requerimiento,
                    estado='REGISTRADA'
                )

                for item_key, item_data in payload.items():
                    cantidad = int(item_data.get('cantidad', 1))
                    es_nuevo = item_data.get('es_nuevo', False)
                    precio_ref_raw = item_data.get('precio_referencial', 0)
                    try:
                        precio_ref = Decimal(str(precio_ref_raw))
                    except (ValueError, TypeError, InvalidOperation):
                        precio_ref = Decimal('0.00')

                    if cantidad <= 0:
                        raise ValueError("La cantidad solicitada debe ser mayor a cero.")
                    if precio_ref < 0:
                        raise ValueError("El precio unitario referencial debe ser mayor o igual a 0.")

                    if es_nuevo:
                        DetalleSolicitud.objects.create(
                            solicitud=solicitud,
                            material=None,
                            es_nueva_adquisicion=True,
                            descripcion_material_no_catalogado=item_data.get('nombre'),
                            cantidad_solicitada=cantidad,
                            precio_unitario_referencial=precio_ref
                        )

                    else:
                        material = Material.objects.get(id=item_key)
                        DetalleSolicitud.objects.create(
                            solicitud=solicitud,
                            material=material,
                            cantidad_solicitada=cantidad,
                            precio_unitario_referencial=precio_ref
                        )

                solicitud.determinar_y_asignar_flujo()

                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Solicitudes',
                    accion='Registrar Solicitud',
                    descripcion=f'Se registró la Solicitud {codigo} con flujo asignado: {solicitud.get_flujo_atencion_display()}'
                )

            messages.success(request, f"Solicitud {codigo} registrada correctamente.")
            return redirect('solicitudes')

        except ValueError as e:
            messages.error(request, str(e))
            return redirect('nueva_solicitud')
        except DatabaseError:
            messages.error(request, "Hubo un error al procesar el guardado en la base de datos.")
            return redirect('nueva_solicitud')

    materiales = Material.objects.all().values('id', 'nombre', 'stock_actual')
    return render(request, 'solicitudes/nueva.html', {'materiales': materiales})



@login_required
def nueva_solicitud_compra(request):
    """
    SIGAL - Flujo 2: Nueva Adquisición de Bienes.

    Registra requerimientos documentales de bienes catalogados
    y no catalogados.

    No genera movimientos de inventario, lotes ni stock.
    """

    if request.method == 'POST':

        # -----------------------------------------
        # 1. VALIDACIÓN DEL TIPO DE REQUERIMIENTO
        # -----------------------------------------
        tipo_requerimiento = request.POST.get(
            'tipo_requerimiento', ''
        ).strip()

        if tipo_requerimiento != 'BIEN':
            messages.error(
                request,
                "Solo se permite registrar nuevas adquisiciones de bienes."
            )
            return redirect('nueva_solicitud_compra')

        # -----------------------------------------
        # 2. VALIDACIÓN DE UNIDAD SOLICITANTE
        # -----------------------------------------
        perfil = getattr(request.user, 'perfilusuario', None)
        unidad_solicitante = perfil.unidad if perfil else None

        if not unidad_solicitante:
            messages.error(
                request,
                "Su usuario no tiene asignada una Unidad Organizacional."
            )
            return redirect('nueva_solicitud_compra')

        # -----------------------------------------
        # 3. VALIDACIÓN DE FECHA Y JUSTIFICACIÓN
        # -----------------------------------------
        fecha_raw = request.POST.get('fecha', '').strip()
        fecha = parse_date(fecha_raw) if fecha_raw else date.today()

        if fecha is None:
            messages.error(request, "La fecha indicada no es válida.")
            return redirect('nueva_solicitud_compra')

        justificacion = request.POST.get(
            'justificacion', ''
        ).strip()

        if not justificacion:
            messages.error(
                request,
                "Debe ingresar la justificación de la adquisición."
            )
            return redirect('nueva_solicitud_compra')

        # -----------------------------------------
        # 4. VALIDACIÓN DEL PAYLOAD
        # -----------------------------------------
        payload_raw = request.POST.get('payload', '')

        if not payload_raw:
            messages.error(
                request,
                "No se recibió información de los bienes requeridos."
            )
            return redirect('nueva_solicitud_compra')

        try:
            payload = json.loads(payload_raw)
        except (json.JSONDecodeError, TypeError):
            messages.error(
                request,
                "El formato de los bienes enviados no es válido."
            )
            return redirect('nueva_solicitud_compra')

        if not isinstance(payload, dict) or not payload:
            messages.error(
                request,
                "Debe agregar al menos un bien a la solicitud."
            )
            return redirect('nueva_solicitud_compra')

        # -----------------------------------------
        # 5. VALIDACIÓN PREVIA DE LOS BIENES
        # -----------------------------------------
        detalles_validados = []

        try:
            for item_key, item_data in payload.items():

                if not isinstance(item_data, dict):
                    raise ValueError(
                        "Se encontró un bien con información inválida."
                    )

                es_nuevo = item_data.get('es_nuevo', False)

                if not isinstance(es_nuevo, bool):
                    raise ValueError(
                        "El tipo de bien enviado no es válido."
                    )

                cantidad_raw = item_data.get('cantidad')

                if (
                    isinstance(cantidad_raw, bool)
                    or not str(cantidad_raw).isdigit()
                ):
                    raise ValueError(
                        "La cantidad debe ser un número entero positivo."
                    )

                cantidad = int(cantidad_raw)

                if cantidad <= 0:
                    raise ValueError(
                        "La cantidad solicitada debe ser mayor a cero."
                    )

                precio_raw = item_data.get(
                    'precio_referencial', 0
                )

                try:
                    precio_ref = Decimal(str(precio_raw))
                except (ValueError, TypeError, InvalidOperation):
                    raise ValueError(
                        "El precio referencial no es válido."
                    )

                if not precio_ref.is_finite() or precio_ref < 0:
                    raise ValueError(
                        "El precio referencial debe ser finito "
                        "y mayor o igual a cero."
                    )
                if precio_ref.as_tuple().exponent < -2:
                    raise ValueError(
                        "El precio referencial admite como máximo 2 decimales."
                    )

                if precio_ref >= Decimal('10000000000'):
                    raise ValueError(
                        "El precio referencial supera el máximo permitido."
                    )
                if es_nuevo:
                    # Bien que todavía no existe en el catálogo SIGAL
                    nombre = str(
                        item_data.get('nombre') or ''
                    ).strip()

                    if not nombre:
                        raise ValueError(
                            "Debe especificar la descripción "
                            "del bien no catalogado."
                        )
                    if len(nombre) > 200:
                        raise ValueError(
                            "La descripción del bien no puede superar los 200 caracteres."
                        )
                    partida_id = item_data.get('partida_id')

                    if not partida_id:
                        raise ValueError(
                            f"Debe seleccionar una partida "
                            f"presupuestaria para el bien: {nombre}."
                        )

                    try:
                        partida = PartidaPresupuestaria.objects.get(
                            pk=partida_id
                        )
                    except (
                        PartidaPresupuestaria.DoesNotExist,
                        ValueError,
                        TypeError
                    ):
                        raise ValueError(
                            f"La partida seleccionada para "
                            f"'{nombre}' no existe."
                        )

                    detalles_validados.append({
                        'material': None,
                        'partida': partida,
                        'es_nueva_adquisicion': True,
                        'descripcion_material_no_catalogado': nombre,
                        'cantidad_solicitada': cantidad,
                        'precio_unitario_referencial': precio_ref
                    })

                else:
                    # Bien existente en el catálogo SIGAL
                    try:
                        material = Material.objects.get(
                            pk=item_key
                        )
                    except (
                        Material.DoesNotExist,
                        ValueError,
                        TypeError
                    ):
                        raise ValueError(
                            "Uno de los materiales seleccionados "
                            "no existe en el catálogo SIGAL."
                        )

                    detalles_validados.append({
                        'material': material,
                        'partida': None,
                        'es_nueva_adquisicion': False,
                        'cantidad_solicitada': cantidad,
                        'precio_unitario_referencial': precio_ref
                    })

        except ValueError as error:
            messages.error(request, str(error))
            return redirect('nueva_solicitud_compra')

        # -----------------------------------------
        # 6. REGISTRO TRANSACCIONAL
        # -----------------------------------------
        try:
            with transaction.atomic():

                # Código temporal único, dentro del límite de 20 caracteres.
                codigo_temporal = (
                    f"TMP-{uuid.uuid4().hex[:16]}"
                )

                solicitud = Solicitud.objects.create(
                    codigo=codigo_temporal,
                    unidad_solicitante=unidad_solicitante,
                    solicitante=request.user,
                    fecha=fecha,
                    justificacion=justificacion,
                    tipo_requerimiento='BIEN',
                    flujo_atencion='ADQUISICION',
                    estado='REGISTRADA'
                )

                # El ID es generado por PostgreSQL.
                codigo = f"REQ-ADQ-{solicitud.pk:05d}"

                solicitud.codigo = codigo
                solicitud.save(update_fields=['codigo'])

                # Registrar todos los detalles de la adquisición.
                for detalle_data in detalles_validados:
                    DetalleSolicitud.objects.create(
                        solicitud=solicitud,
                        **detalle_data
                    )

                # Registrar trazabilidad institucional.
                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Solicitudes',
                    accion='Registrar Nueva Adquisición de Bienes',
                    descripcion=(
                        f"Se registró el requerimiento {codigo} "
                        f"para la nueva adquisición de bienes."
                    )
                )

            messages.success(
                request,
                f"Requerimiento {codigo} registrado correctamente."
            )

            return redirect('solicitudes')

        except IntegrityError:
            messages.error(
                request,
                "No se pudo registrar el requerimiento debido "
                "a un conflicto de integridad de datos."
            )
            return redirect('nueva_solicitud_compra')

        except DatabaseError:
            messages.error(
                request,
                "Ocurrió un error de base de datos al registrar "
                "el requerimiento."
            )
            return redirect('nueva_solicitud_compra')

    # -----------------------------------------
    # 7. FORMULARIO GET
    # -----------------------------------------
    materiales = Material.objects.all().values(
        'id',
        'nombre',
        'stock_actual'
    )

    partidas = PartidaPresupuestaria.objects.all().order_by(
        'codigo'
    )

    return render(
        request,
        'solicitudes/nueva_solicitud_compra.html',
        {
            'materiales': materiales,
            'partidas': partidas
        }
    )

@login_required
@rol_requerido(['ALMACENERO', 'ADMINISTRADOR', 'ADMIN_ALMACENES'])
def catalogar_item_pendiente(request, detalle_id):
    detalle = get_object_or_404(DetalleSolicitud, id=detalle_id)
    partidas = PartidaPresupuestaria.objects.all().order_by('codigo')
    unidades = UnidadMedida.objects.all().order_by('nombre')

    if not detalle.es_nueva_adquisicion:
        messages.error(request, "Este material ya se encuentra codificado y catalogado.")
        return redirect('detalle_solicitud', id=detalle.solicitud.id)

    if request.method == 'POST':
        partida_id = request.POST.get('partida')
        unidad_id = request.POST.get('unidad_medida_fk')
        stock_minimo = int(request.POST.get('stock_minimo', 5))

        partida = get_object_or_404(PartidaPresupuestaria, id=partida_id)
        unidad = get_object_or_404(UnidadMedida, id=unidad_id)
        nombre = detalle.descripcion_material_no_catalogado

        materiales_partida = Material.objects.filter(partida=partida).order_by('codigo')
        if materiales_partida.exists():
            ultimo_codigo = materiales_partida.last().codigo
            try:
                correlativo = int(ultimo_codigo.split('-')[1]) + 1
            except (ValueError, IndexError):
                correlativo = 1
        else:
            correlativo = 1

        codigo = f"{partida.codigo}-{str(correlativo).zfill(4)}"

        try:
            with transaction.atomic():
                material = Material.objects.create(
                    partida=partida,
                    codigo=codigo,
                    nombre=nombre,
                    descripcion="Registrado y codificado desde Solicitud de Adquisición",
                    unidad_medida=unidad.nombre,
                    unidad_medida_fk=unidad,
                    stock_actual=0,
                    stock_minimo=stock_minimo
                )

                detalle.material = material
                detalle.es_nueva_adquisicion = False
                detalle.descripcion_material_no_catalogado = None
                detalle.save()

                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Inventario',
                    accion='Catalogar Item Pendiente',
                    descripcion=f'Se codificó el material {nombre} como {codigo} desde la solicitud {detalle.solicitud.codigo}'
                )

            messages.success(request, f'El material {nombre} ha sido catalogado y codificado exitosamente con el código {codigo}.')
            return redirect('detalle_solicitud', id=detalle.solicitud.id)

        except Exception as e:
            messages.error(request, f'Error al catalogar el material: {str(e)}')
            return redirect('catalogar_item_pendiente', detalle_id=detalle_id)

    return render(request, 'inventario/catalogar_pendiente.html', {
        'detalle': detalle,
        'partidas': partidas,
        'unidades': unidades
    })


@login_required
def detalle_solicitud(request, id):
    solicitud = get_object_or_404(Solicitud, id=id)
    perfil = request.user.perfilusuario
    rol = perfil.rol

    roles_globales = [
        'ADMINISTRADOR',
        'ADMIN_ALMACENES',
        'ALMACENERO',
        'KARDISTA',
        'SECRETARIO_SAF',
        'PRESUPUESTOS',
        'RPA',
        'JEFE_ADMINISTRATIVO'
    ]

    if rol not in roles_globales:
        if rol == 'JEFE_INMEDIATO':
            if solicitud.unidad_solicitante != perfil.unidad:
                return HttpResponseForbidden("No tiene autorización para ver solicitudes de otras unidades.")
        else:
            if solicitud.solicitante != request.user:
                return HttpResponseForbidden("No tiene autorización para ver esta solicitud.")

    # Almacén de origen (si no tiene, toma el Central por defecto)
    almacen = getattr(solicitud, 'almacen_origen', None)
    if not almacen:
        almacen = Almacen.objects.filter(tipo='CENTRAL', is_active=True).first()

    # Procesar PEPS multicapa para cada material solicitado
    detalles = list(solicitud.detalles.select_related('material', 'material__unidad_medida_fk').all())
    total_real_solicitud = Decimal('0.00')

    for d in detalles:
        if not d.es_nueva_adquisicion and d.material and almacen:
            cant = d.cantidad_aprobada if d.cantidad_aprobada is not None else d.cantidad_solicitada
            peps_info = calcular_desglose_peps(d.material, almacen, cant)

            d.desglose_lotes = peps_info['desglose_lotes']
            d.subtotal_real_peps = peps_info['subtotal_real']
            d.alcanza_stock = peps_info['alcanza_stock']
            d.cantidad_faltante = peps_info['cantidad_faltante']
            total_real_solicitud += peps_info['subtotal_real']
        else:
            d.desglose_lotes = []
            d.subtotal_real_peps = d.subtotal_referencial
            d.alcanza_stock = True
            d.cantidad_faltante = 0
            total_real_solicitud += (d.subtotal_referencial or Decimal('0.00'))

    Bitacora.objects.create(
        usuario=request.user,
        modulo='Solicitudes',
        accion='Visualizar Detalle Solicitud',
        descripcion=f'El usuario visualizó en pantalla el detalle de la Solicitud Folio: {solicitud.codigo} (Estado: {solicitud.estado}).'
    )

    return render(request, 'solicitudes/detalle.html', {
        'solicitud': solicitud,
        'detalles': detalles,
        'total_real_solicitud': total_real_solicitud,
        'rol': rol
    })


@login_required
def revisar_solicitud(request, id):
    if not tiene_rol(
        request.user,
        ['JEFE_INMEDIATO', 'ADMINISTRADOR', 'ADMIN_ALMACENES']
    ):
        return HttpResponseForbidden("No autorizado.")

    solicitud = get_object_or_404(Solicitud, id=id)
    # Restringir al Jefe Inmediato a su unidad organizacional
    perfil = getattr(request.user, 'perfilusuario', None)

    if (
        perfil
        and perfil.rol == 'JEFE_INMEDIATO'
        and perfil.unidad_id != solicitud.unidad_solicitante_id
    ):
        return HttpResponseForbidden(
            "No está autorizado para revisar solicitudes de otra unidad."
        )
    if request.method == 'POST':
        try:
            with transaction.atomic():
                # Bloquear la solicitud antes de comprobar su estado
                solicitud = Solicitud.objects.select_for_update().get(pk=id)
                # Verificar autorización después de bloquear la solicitud
                perfil = getattr(request.user, 'perfilusuario', None)

                if perfil and perfil.rol == 'JEFE_INMEDIATO':
                    if perfil.unidad_id != solicitud.unidad_solicitante_id:
                        return HttpResponseForbidden(
                            "No está autorizado para revisar solicitudes de otra unidad."
                        )
                if solicitud.estado != 'REGISTRADA':
                    raise ValueError(
                        "Esta solicitud ya fue procesada o no está "
                        "en estado REGISTRADA."
                    )

                detalles = list(
                    solicitud.detalles.select_related('material')
                    .order_by('material_id', 'id')
                )

                if not detalles:
                    raise ValueError(
                        "La solicitud no contiene materiales registrados."
                    )

                almacen_origen = solicitud.almacen_origen
                if not almacen_origen:
                    raise ValueError(
                        "No se pudo determinar el almacén de origen."
                    )

                # Acumular cantidades por material para validar
                # correctamente los detalles repetidos
                cantidades_por_material = {}

                for detalle in detalles:
                    try:
                        aprobada = int(
                            request.POST.get(f"aprobado_{detalle.id}", "0")
                        )
                    except (TypeError, ValueError):
                        raise ValueError(
                            "La cantidad aprobada debe ser un número entero."
                        )

                    if aprobada < 0 or aprobada > detalle.cantidad_solicitada:
                        raise ValueError(
                            f"Cantidad aprobada inválida para "
                            f"'{detalle.material.nombre if detalle.material else 'material'}'."
                        )

                    detalle.cantidad_aprobada = aprobada

                    if (
                        solicitud.flujo_atencion == 'SALIDA_ALMACEN'
                        and detalle.material
                        and aprobada > 0
                    ):
                        material_id = detalle.material_id
                        cantidades_por_material[material_id] = (
                            cantidades_por_material.get(material_id, 0)
                            + aprobada
                        )

                if solicitud.flujo_atencion == 'SALIDA_ALMACEN':
                    # Bloquear inventarios en orden estable
                    for material_id in sorted(cantidades_por_material):
                        cantidad_total = cantidades_por_material[material_id]

                        inv = (
                            InventarioAlmacen.objects
                            .select_for_update()
                            .filter(
                                material_id=material_id,
                                almacen=almacen_origen
                            )
                            .first()
                        )

                        if inv is None:
                            raise ValueError(
                                f"No existe inventario para el material "
                                f"ID {material_id} en el almacén asignado."
                            )

                        if inv.stock_disponible < cantidad_total:
                            raise ValueError(
                                f"Stock insuficiente para "
                                f"'{inv.material.nombre}'. "
                                f"Disponible: {inv.stock_disponible}; "
                                f"aprobado: {cantidad_total}."
                            )

                        # Validar asignaciones directas de la unidad
                        asignaciones = (
                            AsignacionEntradaUnidad.objects
                            .select_for_update()
                            .filter(
                                unidad_organizacional=solicitud.unidad_solicitante,
                                nota_ingreso_detalle__material_id=material_id,
                                nota_ingreso_detalle__nota_ingreso__almacen_destino=almacen_origen
                            )
                            .order_by('id')
                        )

                        # Saldo asignado pendiente de retiro
                        saldo_asignado = sum(
                            a.saldo_disponible for a in asignaciones
                        )

                        # Reservas pendientes de otras solicitudes de la misma
                        # unidad, material y almacén.
                        # Consultar solicitudes con reservas pendientes de esta unidad
                        detalles_pendientes = (
                            DetalleSolicitud.objects
                            .filter(
                                solicitud__unidad_solicitante=solicitud.unidad_solicitante,
                                solicitud__flujo_atencion='SALIDA_ALMACEN',
                                solicitud__estado__in=['REVISADA', 'PREPARADA'],
                                material_id=material_id,
                            )
                            .select_related(
                                'solicitud',
                                'solicitud__almacen_asignado',
                                'solicitud__unidad_solicitante'
                            )
                        )

                        reservas_pendientes = 0

                        for detalle_pendiente in detalles_pendientes:
                            solicitud_pendiente = detalle_pendiente.solicitud

                            # La propiedad resuelve también solicitudes antiguas
                            # sin almacén persistido.
                            almacen_pendiente = solicitud_pendiente.almacen_origen

                            if almacen_pendiente and almacen_pendiente.pk == almacen_origen.pk:
                                reservas_pendientes += detalle_pendiente.cantidad_aprobada or 0

                        saldo_real_disponible = saldo_asignado - reservas_pendientes

                        if cantidad_total > saldo_real_disponible:
                            raise ValueError(
                                f"La unidad solicitante no tiene suficiente "
                                f"asignación para '{inv.material.nombre}'. "
                                f"Asignado disponible: {saldo_real_disponible}; "
                                f"aprobado: {cantidad_total}."
                            )

                        inv.stock_reservado += cantidad_total
                        inv.save()

                for detalle in detalles:
                    detalle.save(update_fields=['cantidad_aprobada'])

                solicitud.estado = 'REVISADA'
                solicitud.aprobado_por = (
                    request.user.get_full_name()
                    or request.user.username
                )
                solicitud.revisado_por = request.user
                solicitud.fecha_revision = timezone.now()
                solicitud.save()

                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Solicitudes',
                    accion='Revisar Solicitud',
                    descripcion=(
                        f"Se revisó la solicitud {solicitud.codigo} "
                        "y se reservaron los materiales autorizados."
                    )
                )

            messages.success(
                request,
                f"Solicitud {solicitud.codigo} revisada correctamente."
            )

        except (ValueError, ValidationError) as e:
            messages.error(request, str(e))

        except DatabaseError:
            messages.error(
                request,
                "Error de base de datos al revisar la solicitud."
            )

        return redirigir_despues_de_accion(request, solicitud)

    # Consulta GET: no se bloquean registros
    if solicitud.estado != 'REGISTRADA':
        messages.error(
            request,
            "Esta solicitud ya no se encuentra en estado REGISTRADA."
        )
        return redirigir_despues_de_accion(request, solicitud)

    detalles = solicitud.detalles.select_related('material')

    if not detalles.exists():
        messages.error(
            request,
            "La solicitud no contiene materiales registrados."
        )
        return redirigir_despues_de_accion(request, solicitud)

    return render(request, 'solicitudes/aprobar.html', {
        'solicitud': solicitud,
        'detalles': detalles
    })



@login_required
@rol_requerido(['SECRETARIO_SAF', 'ADMINISTRADOR'])
def validar_saf(request, id):
    """
    Validación documental SAF para nuevas adquisiciones de bienes.

    No modifica POA, presupuesto ni inventario.
    """

    # La validación debe realizarse únicamente mediante POST.
    if request.method != 'POST':
        messages.error(
            request,
            "La validación SAF debe realizarse mediante una solicitud POST."
        )
        return redirect('solicitudes')

    try:
        with transaction.atomic():

            # Bloquear la solicitud dentro de la transacción.
            solicitud = get_object_or_404(
                Solicitud.objects.select_for_update(),
                id=id
            )

            # Verificar que corresponda al Flujo 2.
            if (
                solicitud.flujo_atencion != 'ADQUISICION'
                or solicitud.tipo_requerimiento != 'BIEN'
            ):
                messages.error(
                    request,
                    "La validación SAF solo corresponde a "
                    "nuevas adquisiciones de bienes."
                )
                return redirigir_despues_de_accion(
                    request, solicitud
                )

            # Verificar el estado actual.
            if solicitud.estado != 'REVISADA':
                messages.error(
                    request,
                    "La solicitud debe estar revisada y autorizada "
                    "antes de la validación SAF."
                )
                return redirigir_despues_de_accion(
                    request, solicitud
                )

            # Verificar que existan detalles.
            if not solicitud.detalles.exists():
                messages.error(
                    request,
                    "La solicitud no contiene bienes registrados."
                )
                return redirigir_despues_de_accion(
                    request, solicitud
                )

            # Registrar validación SAF.
            solicitud.estado = 'VALIDADA_SAF'
            solicitud.saf_por = request.user
            solicitud.fecha_saf = timezone.now()

            solicitud.save(
                update_fields=[
                    'estado',
                    'saf_por',
                    'fecha_saf'
                ]
            )

            # Registrar auditoría.
            Bitacora.objects.create(
                usuario=request.user,
                modulo='Solicitudes',
                accion='Validación SAF',
                descripcion=(
                    f"Se validó documentalmente la solicitud "
                    f"de adquisición de bienes {solicitud.codigo}."
                )
            )

        messages.success(
            request,
            f"Solicitud {solicitud.codigo} validada por SAF."
        )

        return redirigir_despues_de_accion(
            request, solicitud
        )

    except DatabaseError:
        messages.error(
            request,
            "Ocurrió un error de base de datos durante "
            "la validación SAF."
        )
        return redirect('solicitudes')

@login_required
@rol_requerido(['PRESUPUESTOS', 'ADMINISTRADOR'])
def aprobar_solicitud(request, id):
    solicitud = get_object_or_404(Solicitud.objects.select_for_update(), id=id)

    if solicitud.flujo_atencion == 'SALIDA_ALMACEN':
        messages.warning(request, "Las solicitudes atendidas directamente desde stock no requieren intervención presupuestaria (POA).")
        return redirigir_despues_de_accion(request, solicitud)

    if solicitud.estado != 'VALIDADA_SAF':
        messages.error(request, "Solo solicitudes validadas por la SAF pueden someterse al control presupuestario.")
        return redirigir_despues_de_accion(request, solicitud)

    if not solicitud.tiene_detalles():
        messages.error(request, "La solicitud no contiene ningún concepto y no puede ser aprobada presupuestariamente.")
        return redirigir_despues_de_accion(request, solicitud)

    detalles = solicitud.detalles.all()

    try:
        with transaction.atomic():
            costos_partidas = {}

            for detalle in detalles:
                partida = detalle.partida_afectada
                if not partida:
                    messages.error(
                        request,
                        f"Error: El concepto '{detalle.descripcion_material_no_catalogado or detalle}' no tiene asociada una Partida Presupuestaria."
                    )
                    return redirigir_despues_de_accion(request, solicitud)

                costo_estimado = detalle.subtotal_referencial
                costos_partidas[partida] = costos_partidas.get(partida, Decimal('0.00')) + costo_estimado

            for partida, costo in costos_partidas.items():
                poa = POA.objects.select_for_update().filter(
                    unidad=solicitud.unidad_solicitante,
                    partida=partida,
                    gestion=GESTION_ACTUAL
                ).first()

                if not poa:
                    messages.error(
                        request,
                        f"La unidad {solicitud.unidad_solicitante.nombre} no tiene registrada la partida {partida.codigo} en su POA {GESTION_ACTUAL}."
                    )
                    return redirigir_despues_de_accion(request, solicitud)

                if poa.monto_disponible < costo:
                    messages.error(
                        request,
                        f"Presupuesto insuficiente en la partida {partida.codigo}. "
                        f"Requerido: {costo:.2f} Bs. | Disponible: {poa.monto_disponible:.2f} Bs."
                    )
                    return redirigir_despues_de_accion(request, solicitud)

                poa.monto_comprometido += costo
                poa.monto_disponible -= costo
                poa.save()

            solicitud.estado = 'VALIDADA_PRESUPUESTOS'
            solicitud.presupuestado_por = request.user
            solicitud.fecha_presupuesto = timezone.now()
            solicitud.save()

            Bitacora.objects.create(
                usuario=request.user,
                modulo='Presupuestos',
                accion='Validación Presupuestaria',
                descripcion=(
                    f'Se aprobó y reservó presupuesto para la solicitud {solicitud.codigo} '
                    f'por un monto comprometido total de {solicitud.total_referencial:.2f} Bs.'
                )
            )

        messages.success(request, f"Solicitud {solicitud.codigo} validada presupuestariamente y monto reservado en el POA.")
        return redirigir_despues_de_accion(request, solicitud)

    except DatabaseError:
        messages.error(request, "Error de base de datos al realizar el control presupuestario.")
        return redirigir_despues_de_accion(request, solicitud)


@login_required
@rol_requerido(['RPA', 'ADMINISTRADOR'])
def validar_rpa(request, id):
    solicitud = get_object_or_404(Solicitud.objects.select_for_update(), id=id)

    if solicitud.flujo_atencion == 'SALIDA_ALMACEN':
        messages.warning(request, "Las solicitudes con stock no requieren validación del RPA.")
        return redirigir_despues_de_accion(request, solicitud)

    try:
        with transaction.atomic():
            solicitud.estado = 'VALIDADA_RPA'
            solicitud.rpa_por = request.user
            solicitud.fecha_rpa = timezone.now()
            solicitud.save()

            Bitacora.objects.create(
                usuario=request.user,
                modulo='Solicitudes',
                accion='Aprobación RPA',
                descripcion=f'El RPA aprobó la solicitud {solicitud.codigo}'
            )

        messages.success(request, f"Solicitud {solicitud.codigo} aprobada por el RPA.")
    except Exception as e:
        messages.error(request, f"Error al procesar aprobación RPA: {str(e)}")

    return redirigir_despues_de_accion(request, solicitud)


@login_required
@rol_requerido(['JEFE_ADMINISTRATIVO', 'ADMINISTRADOR'])
def validar_jefatura(request, id):
    solicitud = get_object_or_404(Solicitud.objects.select_for_update(), id=id)

    if solicitud.flujo_atencion == 'SALIDA_ALMACEN':
        messages.warning(request, "Las solicitudes con stock no requieren aprobación de Jefatura Administrativa.")
        return redirigir_despues_de_accion(request, solicitud)

    try:
        with transaction.atomic():
            solicitud.estado = 'VALIDADA_JEFATURA'
            solicitud.jefatura_por = request.user
            solicitud.fecha_jefatura = timezone.now()
            solicitud.save()

            Bitacora.objects.create(
                usuario=request.user,
                modulo='Solicitudes',
                accion='Aprobación Jefatura Administrativa',
                descripcion=f'El Jefe Administrativo aprobó la solicitud {solicitud.codigo}'
            )

        messages.success(request, f"Solicitud {solicitud.codigo} aprobada por la Jefatura Administrativa.")
    except Exception as e:
        messages.error(request, f"Error al procesar aprobación de la Jefatura: {str(e)}")

    return redirigir_despues_de_accion(request, solicitud)


def puede_operar_almacen(usuario, almacen):
    if not usuario.is_authenticated or almacen is None:
        return False

    perfil = getattr(usuario, 'perfilusuario', None)

    if perfil is None:
        return False

    if perfil.rol in ['ADMINISTRADOR', 'ADMIN_ALMACENES']:
        return True

    if perfil.rol != 'ALMACENERO':
        return False

    return (
        almacen.responsable_id == usuario.id
        or almacen.usuarios_autorizados.filter(
            pk=perfil.pk
        ).exists()
    )
@login_required
@rol_requerido(['ALMACENERO', 'ADMINISTRADOR', 'ADMIN_ALMACENES'])
def preparar_solicitud(request, id):
    solicitud = get_object_or_404(Solicitud, id=id)
    if not puede_operar_almacen(request.user, solicitud.almacen_origen):
        return HttpResponseForbidden(
            "No está autorizado para preparar solicitudes de este almacén."
        )

    if solicitud.estado != 'REVISADA':
        messages.error(
            request,
            "Solo las solicitudes REVISADAS pueden prepararse."
        )
        return redirigir_despues_de_accion(request, solicitud)

    if request.method == 'POST':
        try:
            with transaction.atomic():
                solicitud = Solicitud.objects.select_for_update().get(pk=id)
                if not puede_operar_almacen(
                    request.user, solicitud.almacen_origen
                ):
                    raise PermissionDenied(
                        "No está autorizado para operar este almacén."
                    )
                if solicitud.estado != 'REVISADA':
                    raise ValueError(
                        "La solicitud ya no está en estado REVISADA."
                    )

                if not solicitud.almacen_origen:
                    raise ValueError(
                        "La solicitud no tiene un almacén asignado."
                    )

                solicitud.estado = 'PREPARADA'
                solicitud.preparado_por = request.user
                solicitud.fecha_preparado = timezone.now()
                solicitud.save(
                    update_fields=[
                        'estado',
                        'preparado_por',
                        'fecha_preparado'
                    ]
                )

            messages.success(
                request,
                f"Solicitud {solicitud.codigo} preparada correctamente."
            )
            return redirect('detalle_solicitud', id=solicitud.id)

        except ValueError as e:
            messages.error(request, str(e))
            return redirigir_despues_de_accion(request, solicitud)

    return render(
        request,
        'solicitudes/preparar.html',
        {'solicitud': solicitud}
    )

@login_required
@rol_requerido(['ALMACENERO', 'ADMINISTRADOR', 'ADMIN_ALMACENES'])
def entregar_solicitud(request, id):
    """
    Despacho Físico de Materiales con Sistema de Devolución/Retiro de Ítems.
    Si un ítem es retirado o se entrega menos de lo aprobado, libera automáticamente
    la reserva física de almacén y exige justificativo obligatorio.
    """
    if not tiene_rol(
        request.user,
        ['ALMACENERO', 'ADMINISTRADOR', 'ADMIN_ALMACENES']
    ):
        return HttpResponseForbidden(
            "No está autorizado para entregar materiales."
        )
    solicitud = get_object_or_404(Solicitud, id=id)
    if not puede_operar_almacen(request.user, solicitud.almacen_origen):
        return HttpResponseForbidden(
            "No está autorizado para entregar materiales de este almacén."
        )

    if solicitud.flujo_atencion == 'CONTRATACION_SERVICIO':
        messages.error(request, "Un requerimiento de SERVICIO no admite entrega física de inventario.")
        return redirigir_despues_de_accion(request, solicitud)

    if solicitud.estado != 'PREPARADA':
        messages.error(request, "Solo solicitudes en estado PREPARADA pueden despacharse físicamente.")
        return redirigir_despues_de_accion(request, solicitud)

    almacen_origen = solicitud.almacen_origen
    if not almacen_origen:
        messages.error(request, "No se encontró un Almacén activo asignado para esta solicitud.")
        return redirigir_despues_de_accion(request, solicitud)

    detalles = solicitud.detalles.select_related(
        'material',
        'material__partida',
        'material__unidad_medida_fk'
    ).order_by('material_id', 'id')
    from inventario.models import AsignacionEntradaUnidad, NotaSalida, NotaSalidaDetalle

    # =========================================================
    # GET: PANTALLA DE DESPACHO CON CONTROL DE DEVOLUCIONES
    # =========================================================
    if request.method == 'GET':
        items_despacho = []
        for det in detalles:
            material = det.material
            cant_aprobada = det.cantidad_aprobada if det.cantidad_aprobada is not None else det.cantidad_solicitada

            inv = InventarioAlmacen.objects.filter(material=material, almacen=almacen_origen).first()
            stock_disp = inv.stock_fisico if inv else 0

            # Consultar cupo asignado
            asigs = AsignacionEntradaUnidad.objects.filter(
                unidad_organizacional=solicitud.unidad_solicitante,
                nota_ingreso_detalle__material=material,
                nota_ingreso_detalle__nota_ingreso__almacen_destino=almacen_origen,
                cantidad_asignada__gt=F('cantidad_retirada')
            )
            tot_asig = asigs.aggregate(s=Sum('cantidad_asignada'))['s'] or 0
            tot_ret = asigs.aggregate(s=Sum('cantidad_retirada'))['s'] or 0
            saldo_cupo = max(0, tot_asig - tot_ret)

            items_despacho.append({
                'detalle': det,
                'material': material,
                'unidad_manejo': material.unidad_medida_fk.codigo if material.unidad_medida_fk else material.unidad_medida,
                'cant_aprobada': cant_aprobada,
                'stock_disponible': stock_disp,
                'saldo_cupo': saldo_cupo,
                'conforme_poa': (saldo_cupo >= cant_aprobada or saldo_cupo > 0),
            })

        return render(request, 'solicitudes/entregar.html', {
            'solicitud': solicitud,
            'almacen': almacen_origen,
            'items_despacho': items_despacho
        })

    # =========================================================
    # POST: DESPACHO CON LIBERACIÓN DE STOCK Y JUSTIFICATIVO
    # =========================================================
    try:
        with transaction.atomic():
            solicitud_lock = Solicitud.objects.select_for_update().get(id=id)
            if not puede_operar_almacen(
                request.user, solicitud_lock.almacen_origen
            ):
                return HttpResponseForbidden(
                    "No está autorizado para despachar desde este almacén."
                )
            if solicitud_lock.estado != 'PREPARADA':
                raise ValueError(
                    f"La solicitud {solicitud_lock.codigo} ya no está "
                    "en estado PREPARADA. No se puede procesar "
                    "nuevamente su despacho."
                )

            # Validar primero si hay devoluciones sin justificar
            for detalle in detalles:
                cant_aprobada = detalle.cantidad_aprobada if detalle.cantidad_aprobada is not None else detalle.cantidad_solicitada
                es_devolucion = request.POST.get(f"devolver_{detalle.id}") == 'true'
                cant_post = request.POST.get(f"entregado_{detalle.id}", '').strip()

                cant_real = 0 if es_devolucion else (int(cant_post) if cant_post else cant_aprobada)
                justificativo = request.POST.get(f"justificativo_{detalle.id}", '').strip()

                if cant_real < cant_aprobada and len(justificativo) < 5:
                    raise ValueError(
                        f"Debe indicar el justificativo de devolución o reducción para '{detalle.material.nombre}' "
                        f"(Aprobado: {cant_aprobada} u. -> Entregando: {cant_real} u.)."
                    )

            # Crear Nota de Salida solo si se entrega al menos 1 material
            items_a_entregar = []
            for detalle in detalles:
                es_devolucion = request.POST.get(f"devolver_{detalle.id}") == 'true'
                cant_post = request.POST.get(f"entregado_{detalle.id}", '').strip()
                cant_aprobada = detalle.cantidad_aprobada if detalle.cantidad_aprobada is not None else detalle.cantidad_solicitada
                cant_real = 0 if es_devolucion else (int(cant_post) if cant_post else cant_aprobada)
                if cant_real < 0 or cant_real > cant_aprobada:
                    raise ValueError(
                        f"Cantidad inválida para '{detalle.material.nombre}'. "
                        f"Se aprobaron {cant_aprobada} unidades y se intentan entregar {cant_real}."
                    )
                if cant_real > 0:
                    items_a_entregar.append((detalle, cant_real))

            nota_salida = None
            if items_a_entregar:
                codigo_temporal = f"TMP-{uuid.uuid4().hex}"

                nota_salida = NotaSalida.objects.create(
                    nro_nota=codigo_temporal,
                    solicitud_origen=solicitud_lock,
                    almacen_origen=almacen_origen,
                    unidad_destino=solicitud_lock.unidad_solicitante,
                    fecha=timezone.now().date(),
                    usuario=request.user
                )

                # Generar correlativo a partir del ID asignado
                nro_nota_salida = f"NS-{nota_salida.pk:05d}"

                nota_salida.nro_nota = nro_nota_salida
                nota_salida.save(update_fields=['nro_nota'])

            # Procesar cada material (Despacho real o Devolución/Liberación)
            for detalle in detalles:
                material = Material.objects.select_for_update().get(id=detalle.material.id)
                cant_aprobada = detalle.cantidad_aprobada if detalle.cantidad_aprobada is not None else detalle.cantidad_solicitada
                es_devolucion = request.POST.get(f"devolver_{detalle.id}") == 'true'
                cant_post = request.POST.get(f"entregado_{detalle.id}", '').strip()
                cant_despacho = 0 if es_devolucion else (int(cant_post) if cant_post else cant_aprobada)
                justificativo = request.POST.get(f"justificativo_{detalle.id}", '').strip()

                if cant_despacho < 0 or cant_despacho > cant_aprobada:
                    raise ValueError(
                        f"Cantidad de despacho inválida para '{material.nombre}'. "
                        f"Se aprobaron {cant_aprobada} unidades y "
                        f"se intentan entregar {cant_despacho}."
                    )

                inv = InventarioAlmacen.objects.select_for_update().filter(material=material, almacen=almacen_origen).first()
                diferencia_devuelta = max(0, cant_aprobada - cant_despacho)

                # 1. CASO DEVOLUCIÓN TOTAL O PARCIAL: Liberar reserva de stock a estantería
                if diferencia_devuelta > 0:
                    if inv is None:
                        raise ValidationError(
                            f"No existe inventario para '{material.nombre}' "
                            "en el almacén de origen."
                        )

                    if inv.stock_reservado < cant_aprobada:
                        raise ValidationError(
                            f"Reserva insuficiente para procesar el despacho de "
                            f"'{material.nombre}'. "
                            f"Reservado: {inv.stock_reservado}; "
                            f"aprobado: {cant_aprobada}."
                        )

                    inv.stock_reservado -= diferencia_devuelta
                    inv.save(update_fields=['stock_reservado'])
                    detalle.observacion = (
                        f"DEVOLUCIÓN EN VENTANILLA: {diferencia_devuelta} u. devueltas/canceladas al stock. "
                        f"Motivo: {justificativo}"
                    )

                # 2. CASO ENTREGA FÍSICA: Descontar de estantería y registrar salida PEPS
                if cant_despacho > 0:
                    mov = registrar_salida_valorada_peps(
                        material=material,
                        almacen=almacen_origen,
                        cantidad_salida=cant_despacho,
                        tipo_movimiento='SALIDA',
                        referencia=f"DESPACHO: {solicitud_lock.codigo}",
                        usuario=request.user,
                        unidad_destino=solicitud_lock.unidad_solicitante,
                        descontar_reserva=True
                    )

                    detalle.cantidad_entregada = cant_despacho
                    detalle.precio_unitario_referencial = mov.costo_unitario

                    NotaSalidaDetalle.objects.create(
                        nota_salida=nota_salida,
                        material=material,
                        cantidad=cant_despacho,
                        costo_unitario_real=mov.costo_unitario,
                        costo_total_real=mov.costo_total
                    )
                else:
                    detalle.cantidad_entregada = 0

                detalle.save()

            # 3. Finalizar estado del trámite
            solicitud_lock.estado = 'ENTREGADA'
            solicitud_lock.entregado_por = request.user
            solicitud_lock.fecha_entrega = timezone.now()
            solicitud_lock.save()

            salida_info = f" con Nota de Salida {nota_salida.nro_nota}" if nota_salida else " (Trámite sin ítems despachados/totalmente devuelto)"
            Bitacora.objects.create(
                usuario=request.user,
                modulo='Inventario',
                accion='Entrega Física con Control de Devolución',
                descripcion=f'Se procesó el despacho del pedido {solicitud_lock.codigo}{salida_info}. Stock reservado reajustado.'
            )

        messages.success(request, f"Entrega procesada correctamente. Stock y devoluciones registradas con éxito.")
        return redirect('detalle_solicitud', id=solicitud.id)

    except ValueError as e:
        messages.error(request, str(e))
        return redirect('entregar_solicitud', id=solicitud.id)
    except Exception as e:
        messages.error(request, f"Error al procesar el despacho: {str(e)}")
        return redirect('entregar_solicitud', id=solicitud.id)
@login_required
def cerrar_solicitud(request, id):
    if not tiene_rol(request.user, ['ALMACENERO', 'ADMINISTRADOR', 'ADMIN_ALMACENES']):
        return HttpResponseForbidden("No autorizado.")

    solicitud = get_object_or_404(Solicitud, id=id)

    if solicitud.estado != 'ENTREGADA':
        messages.error(request, "Solo solicitudes ENTREGADAS pueden marcarse como CERRADAS.")
        return redirigir_despues_de_accion(request, solicitud)

    solicitud.estado = 'CERRADA'
    solicitud.cerrado_por = request.user
    solicitud.fecha_cierre = timezone.now()
    solicitud.save()

    Bitacora.objects.create(
        usuario=request.user,
        modulo='Solicitudes',
        accion='Cerrar trámite',
        descripcion=f'Se archivó y cerró el trámite de la Solicitud {solicitud.codigo}'
    )

    messages.success(request, f"La solicitud {solicitud.codigo} ha sido CERRADA y archivada correctamente.")
    return redirigir_despues_de_accion(request, solicitud)



@login_required
def editar_solicitud(request, id):
    perfil = getattr(request.user, 'perfilusuario', None)

    if perfil is None:
        return HttpResponseForbidden(
            "Su usuario no tiene un perfil institucional asignado."
        )

    solicitud = get_object_or_404(Solicitud, id=id)

    if perfil.rol not in ['ADMINISTRADOR', 'ADMIN_ALMACENES']:
        if solicitud.unidad_solicitante_id != perfil.unidad_id:
            return HttpResponseForbidden(
                "No tiene permisos para modificar solicitudes de otra unidad."
            )

    if solicitud.estado != 'REGISTRADA':
        return HttpResponseForbidden(
            "Esta solicitud ya se encuentra en proceso de revisión "
            "y no es editable."
        )

    # ---------------------------------------------------------
    # GET: Mostrar formulario de edición
    # ---------------------------------------------------------
    if request.method == "GET":
        detalles = solicitud.detalles.select_related('material')

        detalles_json = json.dumps([
            {
                "id": d.material.id if d.material else d.id,
                "nombre": (
                    d.material.nombre
                    if d.material
                    else d.descripcion_material_no_catalogado
                ),
                "cantidad": d.cantidad_solicitada,
                "stock": (
                    d.material.stock_actual
                    if d.material
                    else 0
                ),
                "precio_referencial": float(
                    d.precio_unitario_referencial
                ),
                "es_nuevo": d.es_nueva_adquisicion
            }
            for d in detalles
        ])

        return render(request, "solicitudes/editar.html", {
            "solicitud": solicitud,
            "detalles_json": detalles_json
        })

    if request.method != "POST":
        return JsonResponse(
            {"ok": False, "error": "Método HTTP no permitido."},
            status=405
        )

    # ---------------------------------------------------------
    # POST: Actualizar solicitud
    # ---------------------------------------------------------
    try:
        data = json.loads(request.body.decode("utf-8"))

        if not isinstance(data, dict):
            raise ValueError("El contenido enviado no es válido.")

        payload = data.get("payload", {})
        justificacion = str(
            data.get("justificacion") or ""
        ).strip()

        tipo_requerimiento = data.get(
            "tipo_requerimiento", "BIEN"
        )

        # SIGAL: Nueva adquisición exclusivamente de bienes.
        if tipo_requerimiento != 'BIEN':
            raise ValueError(
                "SIGAL únicamente permite requerimientos de bienes."
            )

        if not isinstance(payload, dict) or not payload:
            raise ValueError(
                "Debe agregar al menos un material a la solicitud."
            )

        if not justificacion:
            raise ValueError(
                "Debe ingresar la justificación de la solicitud."
            )

        # -----------------------------------------------------
        # Validar todos los materiales antes de modificar datos
        # -----------------------------------------------------
        detalles_validados = []

        for material_id, item_data in payload.items():

            if isinstance(item_data, dict):
                cantidad_raw = item_data.get('cantidad', 1)
                precio_ref_raw = item_data.get(
                    'precio_referencial', 0
                )
                es_nuevo = item_data.get('es_nuevo', False)
                nombre_no_catalogado = str(
                    item_data.get('nombre') or ''
                ).strip()
            else:
                cantidad_raw = item_data
                precio_ref_raw = 0
                es_nuevo = False
                nombre_no_catalogado = ''

            try:
                cantidad = int(cantidad_raw)
            except (ValueError, TypeError, OverflowError):
                raise ValueError(
                    "La cantidad de los materiales debe ser "
                    "un número entero válido."
                )

            if cantidad <= 0:
                raise ValueError(
                    "La cantidad de los materiales debe "
                    "ser mayor a cero."
                )

            try:
                precio_ref = Decimal(str(precio_ref_raw))
            except (ValueError, TypeError, InvalidOperation):
                raise ValueError(
                    "El precio referencial debe ser numérico."
                )

            if not precio_ref.is_finite() or precio_ref < 0:
                raise ValueError(
                    "El precio referencial debe ser un número "
                    "finito mayor o igual a cero."
                )

            if es_nuevo or not str(material_id).isdigit():
                if not nombre_no_catalogado:
                    raise ValueError(
                        "Debe ingresar el nombre del bien "
                        "que desea adquirir."
                    )

                detalles_validados.append({
                    'material': None,
                    'es_nueva_adquisicion': True,
                    'descripcion_material_no_catalogado': (
                        nombre_no_catalogado
                    ),
                    'cantidad_solicitada': cantidad,
                    'precio_unitario_referencial': precio_ref
                })

            else:
                try:
                    material = Material.objects.get(
                        pk=material_id
                    )
                except Material.DoesNotExist:
                    raise ValueError(
                        f"El material con ID {material_id} "
                        "no existe en el catálogo."
                    )

                detalles_validados.append({
                    'material': material,
                    'es_nueva_adquisicion': False,
                    'cantidad_solicitada': cantidad,
                    'precio_unitario_referencial': precio_ref
                })

        # -----------------------------------------------------
        # Actualización atómica
        # -----------------------------------------------------
        with transaction.atomic():

            solicitud = (
                Solicitud.objects
                .select_for_update()
                .get(pk=id)
            )

            # Revalidar permisos y estado dentro de la transacción
            if perfil.rol not in [
                'ADMINISTRADOR', 'ADMIN_ALMACENES'
            ]:
                if solicitud.unidad_solicitante_id != perfil.unidad_id:
                    raise PermissionError(
                        "No tiene permisos para modificar "
                        "solicitudes de otra unidad."
                    )

            if solicitud.estado != 'REGISTRADA':
                raise ValueError(
                    "La solicitud ya fue procesada y "
                    "no puede modificarse."
                )

            solicitud.justificacion = justificacion
            solicitud.tipo_requerimiento = tipo_requerimiento
            solicitud.save(
                update_fields=[
                    'justificacion',
                    'tipo_requerimiento'
                ]
            )

            # Sustituir detalles anteriores
            solicitud.detalles.all().delete()

            for detalle_data in detalles_validados:
                DetalleSolicitud.objects.create(
                    solicitud=solicitud,
                    **detalle_data
                )

            # Determinar el flujo una sola vez,
            # cuando todos los detalles ya existen.
            solicitud.determinar_y_asignar_flujo()

            # Registrar una sola entrada en bitácora
            Bitacora.objects.create(
                usuario=request.user,
                modulo='Solicitudes',
                accion='Editar Solicitud',
                descripcion=(
                    f"Se actualizó la solicitud "
                    f"{solicitud.codigo}. "
                    f"Flujo asignado: "
                    f"{solicitud.get_flujo_atencion_display()}."
                )
            )

        return JsonResponse({
            "ok": True,
            "mensaje": "Solicitud actualizada correctamente.",
            "flujo_atencion": solicitud.flujo_atencion
        })

    except json.JSONDecodeError:
        return JsonResponse({
            "ok": False,
            "error": "Formato de datos JSON inválido."
        }, status=400)

    except PermissionError as e:
        return JsonResponse({
            "ok": False,
            "error": str(e)
        }, status=403)

    except ValueError as e:
        return JsonResponse({
            "ok": False,
            "error": str(e)
        }, status=400)

    except DatabaseError:
        return JsonResponse({
            "ok": False,
            "error": "Error de base de datos al actualizar la solicitud."
        }, status=500)


@login_required
def rechazar_solicitud(request, id):
    roles_revisores = [
        'JEFE_INMEDIATO',
        'SECRETARIO_SAF',
        'PRESUPUESTOS',
        'RPA',
        'JEFE_ADMINISTRATIVO',
        'ADMINISTRADOR',
        'ADMIN_ALMACENES'
    ]

    if not tiene_rol(request.user, roles_revisores):
        return HttpResponseForbidden("No tiene permisos para realizar esta acción.")

    solicitud = get_object_or_404(Solicitud, id=id)

    if request.method == 'POST':
        motivo = request.POST.get('motivo_predefinido')
        if motivo == 'OTRO':
            motivo = request.POST.get('motivo_personalizado')

        try:
            with transaction.atomic():
                solicitud = Solicitud.objects.select_for_update().get(id=id)
                if solicitud.estado in ['RECHAZADA', 'ENTREGADA', 'CERRADA']:
                    raise ValidationError(
                        "No se puede rechazar una solicitud que ya fue "
                        "rechazada, entregada o cerrada."
                    )

                if (
                    solicitud.flujo_atencion == 'SALIDA_ALMACEN'
                    and solicitud.estado in ['REVISADA', 'PREPARADA']
                ):
                    liberar_reservas_solicitud(solicitud)
                estados_con_reserva = ['VALIDADA_PRESUPUESTOS', 'VALIDADA_RPA', 'VALIDADA_JEFATURA', 'PREPARADA']
                if solicitud.estado in estados_con_reserva and solicitud.flujo_atencion in ['ADQUISICION', 'CONTRATACION_SERVICIO']:
                    for detalle in solicitud.detalles.all():
                        partida = detalle.partida_afectada
                        if partida:
                            poa = POA.objects.select_for_update().filter(
                                unidad=solicitud.unidad_solicitante,
                                partida=partida,
                                gestion=GESTION_ACTUAL
                            ).first()

                            if poa:
                                costo_estimado = detalle.subtotal_referencial
                                poa.monto_comprometido -= costo_estimado
                                poa.monto_disponible += costo_estimado
                                poa.save()

                solicitud.estado = 'RECHAZADA'
                solicitud.motivo_rechazo = motivo
                solicitud.aprobado_por = request.user.get_full_name() or request.user.username
                solicitud.save()

                if solicitud.flujo_atencion == 'SALIDA_ALMACEN':
                    descripcion_liberacion = "Se liberaron las reservas de materiales correspondientes."
                    mensaje_liberacion = "Se liberaron las reservas de materiales."
                else:
                    descripcion_liberacion = "Se procesó la liberación presupuestaria cuando correspondía."
                    mensaje_liberacion = "Se procesaron las liberaciones correspondientes."

                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Solicitudes',
                    accion='Rechazar Solicitud',
                    descripcion=(
                        f'Se rechazó la solicitud {solicitud.codigo} '
                        f'por: {motivo}. {descripcion_liberacion}'
                    )
                )

                messages.info(
                    request,
                    f"La solicitud {solicitud.codigo} ha sido rechazada. "
                    f"{mensaje_liberacion}"
                )
                return redirigir_despues_de_accion(request, solicitud)
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
            return redirigir_despues_de_accion(request, solicitud)

        except Exception:
            messages.error(
                request,
                "No se pudo procesar el rechazo de la solicitud. "
                "Verifique los datos e inténtelo nuevamente."
            )
            return redirigir_despues_de_accion(request, solicitud)
    return render(request, 'solicitudes/rechazar.html', {'solicitud': solicitud})


@login_required
def reabrir_solicitud(request, id):
    if not tiene_rol(request.user, ['JEFE_INMEDIATO', 'ADMINISTRADOR', 'ADMIN_ALMACENES']):
        return HttpResponseForbidden("No tiene permisos para realizar esta acción.")

    solicitud = get_object_or_404(Solicitud, id=id)

    if solicitud.estado != 'RECHAZADA':
        return redirigir_despues_de_accion(request, solicitud)

    solicitud.estado = 'REGISTRADA'
    solicitud.save()

    messages.info(request, f"La solicitud {solicitud.codigo} ha sido reabierta.")
    return redirigir_despues_de_accion(request, solicitud)


@login_required
def solicitud_pdf(request, id):
    """
    Formulario Oficial de Pedido de Materiales y Bienes (Flujo 1).
    Desglosa automáticamente las capas PEPS en renglones independientes
    para que en el papel oficial cada línea cumpla Cantidad x P.Unit = Costo Total.
    """
    solicitud = get_object_or_404(
        Solicitud.objects.prefetch_related(
            'detalles__material__partida',
            'detalles__material__unidad_medida_fk',
            'detalles__partida'
        ).select_related(
            'unidad_solicitante', 'solicitante', 'revisado_por', 'preparado_por', 'entregado_por'
        ),
        id=id
    )

    if solicitud.estado not in ['PREPARADA', 'ENTREGADA', 'CERRADA']:
        messages.warning(
            request,
            "El Formulario Oficial de Pedido no puede imprimirse hasta que Almacén verifique existencias y prepare el despacho."
        )
        return redirect('detalle_solicitud', id=solicitud.id)

    almacen = getattr(solicitud, 'almacen_origen', None) or Almacen.objects.filter(tipo='CENTRAL', is_active=True).first()

    items_procesados = []
    total_costo_pedido = Decimal('0.00')
    codigo_presupuesto = "343"

    for d in solicitud.detalles.all():
        # 1. Código seguro
        if d.material and d.material.codigo:
            cod_item = d.material.codigo
        elif d.partida and d.partida.codigo:
            cod_item = d.partida.codigo
        else:
            cod_item = "S/C"

        # 2. Descripción base
        if d.material and d.material.nombre:
            desc_base = d.material.nombre
        else:
            desc_base = d.descripcion_material_no_catalogado or "Ítem no catalogado"

        # 3. Unidad de medida segura
        if d.material and d.material.unidad_medida_fk:
            u_med = d.material.unidad_medida_fk.codigo
        elif d.material and d.material.unidad_medida:
            u_med = d.material.unidad_medida
        else:
            u_med = "PIEZA"

        # 4. Partida presupuestaria segura
        if d.material and d.material.partida and d.material.partida.codigo:
            cod_partida = d.material.partida.codigo
        elif d.partida and d.partida.codigo:
            cod_partida = d.partida.codigo
        else:
            cod_partida = "343"

        if codigo_presupuesto == "343" and cod_partida != "343":
            codigo_presupuesto = cod_partida

        # 5. Cantidad entregada real (evalúa devoluciones a 0)
        if solicitud.estado in ['ENTREGADA', 'CERRADA']:
            cant_entrega = d.cantidad_entregada if d.cantidad_entregada is not None else d.cantidad_solicitada
        else:
            cant_entrega = d.cantidad_aprobada if d.cantidad_aprobada is not None else d.cantidad_solicitada

        # Caso: Devolución total en ventanilla (0 unidades entregadas)
        if cant_entrega == 0:
            items_procesados.append({
                'codigo': cod_item,
                'descripcion': f"{desc_base} (DEVUELTO AL STOCK)",
                'unidad_medida': u_med,
                'cantidad_solicitada': d.cantidad_solicitada,
                'cantidad_entregada': 0,
                'partida_codigo': cod_partida,
                'costo_unitario': d.precio_unitario_referencial or Decimal('0.00'),
                'costo_total': Decimal('0.00'),
            })
            continue

        # 6. Cálculo y Desglose PEPS Multicapa para el Formulario Oficial
        if d.material and almacen:
            peps_info = calcular_desglose_peps(d.material, almacen, cant_entrega)
            lotes = peps_info['desglose_lotes']

            if len(lotes) > 1:
                # Si consumió varios lotes, se imprime una fila para cada lote:
                # Fila 1: Lavandina 4L [Lote: NI-00164] -> 2 u. x Bs. 30,00 = 60,00
                # Fila 2: Lavandina 4L [Lote: NI-00165] -> 1 u. x Bs. 45,00 = 45,00
                for lote in lotes:
                    costo_lote_tot = lote['subtotal']
                    total_costo_pedido += costo_lote_tot
                    items_procesados.append({
                        'codigo': cod_item,
                        'descripcion': f"{desc_base} [Lote: {lote['referencia']}]",
                        'unidad_medida': u_med,
                        'cantidad_solicitada': lote['cantidad'],
                        'cantidad_entregada': lote['cantidad'],
                        'partida_codigo': cod_partida,
                        'costo_unitario': lote['costo_unitario'],
                        'costo_total': costo_lote_tot,
                    })
                continue
            elif len(lotes) == 1:
                # Si consumió un solo lote
                costo_u = lotes[0]['costo_unitario']
                costo_tot = lotes[0]['subtotal']
                total_costo_pedido += costo_tot
                items_procesados.append({
                    'codigo': cod_item,
                    'descripcion': f"{desc_base} [Lote: {lotes[0]['referencia']}]",
                    'unidad_medida': u_med,
                    'cantidad_solicitada': d.cantidad_solicitada,
                    'cantidad_entregada': cant_entrega,
                    'partida_codigo': cod_partida,
                    'costo_unitario': costo_u,
                    'costo_total': costo_tot,
                })
                continue

        # Fallback estándar para ítems de compra directa sin lotes
        costo_u = d.precio_unitario_referencial or Decimal('0.00')
        costo_tot = Decimal(cant_entrega) * costo_u
        total_costo_pedido += costo_tot
        items_procesados.append({
            'codigo': cod_item,
            'descripcion': desc_base,
            'unidad_medida': u_med,
            'cantidad_solicitada': d.cantidad_solicitada,
            'cantidad_entregada': cant_entrega,
            'partida_codigo': cod_partida,
            'costo_unitario': costo_u,
            'costo_total': costo_tot,
        })

    return render(request, 'solicitudes/pdf_formulario_pedido.html', {
        'solicitud': solicitud,
        'items': items_procesados,
        'total_costo': total_costo_pedido,
        'codigo_presupuesto': codigo_presupuesto,
    })


@transaction.atomic
@login_required
@rol_requerido(['ADMINISTRADOR', 'ADMIN_ALMACENES'])
@require_POST
def retroceder_estado_solicitud(request, id):
    solicitud = get_object_or_404(
        Solicitud.objects.select_for_update(),
        id=id
    )

    estado_actual = solicitud.estado

    if solicitud.flujo_atencion == 'SALIDA_ALMACEN':
        map_retroceso = {
            'REVISADA': (
                'REGISTRADA',
                'revisado_por',
                'fecha_revision'
            ),
            'PREPARADA': (
                'REVISADA',
                'preparado_por',
                'fecha_preparado'
            ),
        }
    else:
        map_retroceso = {
            'REVISADA': ('REGISTRADA', 'revisado_por', 'fecha_revision'),
            'VALIDADA_SAF': ('REVISADA', 'saf_por', 'fecha_saf'),
            'VALIDADA_PRESUPUESTOS': (
                'VALIDADA_SAF', 'presupuestado_por', 'fecha_presupuesto'
            ),
            'VALIDADA_RPA': (
                'VALIDADA_PRESUPUESTOS', 'rpa_por', 'fecha_rpa'
            ),
            'VALIDADA_JEFATURA': (
                'VALIDADA_RPA', 'jefatura_por', 'fecha_jefatura'
            ),
            'PREPARADA': (
                'VALIDADA_JEFATURA', 'preparado_por', 'fecha_preparado'
            ),
        }

    if estado_actual not in map_retroceso:
        messages.error(
            request,
            "No es posible retroceder esta solicitud."
        )
        return redirigir_despues_de_accion(request, solicitud)

    nuevo_estado, campo_usuario, campo_fecha = map_retroceso[estado_actual]


    # Liberar la reserva al regresar de REVISADA a REGISTRADA.
    if (
        solicitud.flujo_atencion == 'SALIDA_ALMACEN'
        and estado_actual == 'REVISADA'
        and nuevo_estado == 'REGISTRADA'
    ):
        liberar_reservas_solicitud(solicitud)

    solicitud.estado = nuevo_estado
    setattr(solicitud, campo_usuario, None)
    setattr(solicitud, campo_fecha, None)
    solicitud.save()

    Bitacora.objects.create(
        usuario=request.user,
        modulo='Solicitudes',
        accion='Reversión de Estado Administrativo',
        descripcion=(
            f'El administrador revirtió la solicitud '
            f'{solicitud.codigo} desde {estado_actual} '
            f'hasta {nuevo_estado}.'
        )
    )

    messages.success(
        request,
        f"La solicitud {solicitud.codigo} volvió al estado "
        f"{solicitud.get_estado_display()}."
    )

    return redirigir_despues_de_accion(request, solicitud)

@login_required
def nuevo_pedido_almacen(request):
    """
    Registro de pedidos con Control Estricto Total:
    Solo permite solicitar materiales que posean Asignación Directa > 0.
    """
    perfil = getattr(request.user, 'perfilusuario', None)
    rol = perfil.rol if perfil else 'UNIDAD_SOLICITANTE'

    unidades_disponibles = None
    if rol in ['ADMINISTRADOR', 'ADMIN_ALMACENES']:
        unidades_disponibles = UnidadOrganizacional.objects.filter(is_active=True).order_by('nombre')
        unidad_id = request.GET.get('unidad_id') or (request.POST.get('unidad_solicitante_id') if request.method == 'POST' else None)
        if unidad_id:
            unidad_solicitante = get_object_or_404(UnidadOrganizacional, id=unidad_id)
        else:
            unidad_solicitante = perfil.unidad or unidades_disponibles.first()
    else:
        unidad_solicitante = perfil.unidad

    if not unidad_solicitante:
        messages.error(request, "Su usuario no tiene asignada una Unidad Organizacional activa.")
        return redirect('solicitudes')

    # Almacenes autorizados
    almacenes_autorizados = Almacen.objects.filter(
        unidades_atendidas=unidad_solicitante,
        is_active=True
    )
    if not almacenes_autorizados.exists():
        almacenes_autorizados = Almacen.objects.filter(tipo='CENTRAL', is_active=True)

    from inventario.models import AsignacionEntradaUnidad

    # =========================================================
    # POST: REGISTRAR PEDIDO VALIDANDO CUPO ASIGNADO FÍSICO
    # =========================================================
    if request.method == 'POST':
        fecha = request.POST.get('fecha')
        payload_raw = request.POST.get("payload")
        justificacion = request.POST.get('justificacion', '').strip()

        if not payload_raw:
            messages.error(request, "No se recibió información de materiales.")
            return redirect('nuevo_pedido_almacen')

        payload_raw = html.unescape(payload_raw) if hasattr(html, 'unescape') else payload_raw
        try:
            payload = json.loads(payload_raw)
        except json.JSONDecodeError:
            messages.error(request, "Error en el formato de los datos enviados.")
            return redirect('nuevo_pedido_almacen')

        if not payload:
            messages.error(request, "Debe agregar al menos un material al pedido.")
            return redirect('nuevo_pedido_almacen')

        try:

            with transaction.atomic():
                # 1. Resolver el almacén antes de crear la solicitud
                from solicitudes.services import (
                    resolver_almacen_para_unidad,
                    AlmacenNoDisponible,
                    ConfiguracionAlmacenAmbigua
                )

                try:
                    almacen_origen = resolver_almacen_para_unidad(
                        unidad_solicitante
                    )
                except (AlmacenNoDisponible, ConfiguracionAlmacenAmbigua) as e:
                    raise ValueError(str(e))

                # 2. Generar código temporal único
                codigo_temporal = f"TMP-{uuid.uuid4().hex[:16]}"

                # 3. Crear la solicitud
                solicitud = Solicitud.objects.create(
                    codigo=codigo_temporal,
                    unidad_solicitante=unidad_solicitante,
                    almacen_asignado=almacen_origen,
                    solicitante=request.user,
                    fecha=fecha,
                    justificacion=justificacion,
                    tipo_requerimiento='BIEN',
                    flujo_atencion='SALIDA_ALMACEN',
                    estado='REGISTRADA'
                )

                # 4. Generar código institucional sin duplicados
                from django.db import connection
                import re

                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_advisory_xact_lock(%s)",
                        [2026001]
                    )

                codigos = Solicitud.objects.filter(
                    codigo__startswith='PED-'
                ).values_list('codigo', flat=True)

                numeros = [
                    int(coincidencia.group(1))
                    for codigo_existente in codigos
                    if (coincidencia := re.fullmatch(r'PED-(\d+)', codigo_existente))
                ]

                siguiente_numero = max(numeros, default=0) + 1
                codigo = f"PED-{siguiente_numero:05d}"

                solicitud.codigo = codigo
                solicitud.save(update_fields=['codigo'])

                #5. Validar y registrar los materiales del pedido
                if not isinstance(payload, dict):
                    raise ValueError(
                        "El formato de los materiales no es válido."
                    )

                for item_key, item_data in payload.items():
                    if not isinstance(item_data, dict):
                        raise ValueError(
                            "Los datos de un material no son válidos."
                        )

                    try:
                        cantidad = int(item_data.get('cantidad', 0))
                    except (TypeError, ValueError):
                        raise ValueError(
                            "La cantidad solicitada debe ser un número entero."
                        )

                    if cantidad <= 0:
                        raise ValueError(
                            "La cantidad solicitada debe ser mayor a cero."
                        )

                    material = Material.objects.get(pk=item_key)

                    # Validar el saldo asignado a esta unidad
                    # exclusivamente en el almacén de origen.
                    asignaciones = AsignacionEntradaUnidad.objects.filter(
                        unidad_organizacional=unidad_solicitante,
                        nota_ingreso_detalle__material=material,
                        nota_ingreso_detalle__nota_ingreso__almacen_destino=almacen_origen,
                        cantidad_asignada__gt=F('cantidad_retirada')
                    )

                    totales = asignaciones.aggregate(
                        asignado=Sum('cantidad_asignada'),
                        retirado=Sum('cantidad_retirada')
                    )

                    saldo_cupo = (
                        (totales['asignado'] or 0)
                        - (totales['retirado'] or 0)
                    )

                    if cantidad > saldo_cupo:
                        raise ValueError(
                            f"Cupo insuficiente para '{material.nombre}'. "
                            f"Disponible: {saldo_cupo}; "
                            f"solicitado: {cantidad}."
                        )

                    # Validar stock físico disponible
                    inventario = InventarioAlmacen.objects.filter(
                        material=material,
                        almacen=almacen_origen
                    ).first()

                    stock_disponible = (
                        inventario.stock_disponible if inventario else 0
                    )

                    if cantidad > stock_disponible:
                        raise ValueError(
                            f"Stock insuficiente para '{material.nombre}'. "
                            f"Disponible: {stock_disponible}; "
                            f"solicitado: {cantidad}."
                        )

                    # Consultar costo referencial PEPS
                    lote_peps = MovimientoInventario.objects.filter(
                        material=material,
                        almacen=almacen_origen,
                        tipo='ENTRADA',
                        saldo_disponible_lote__gt=0
                    ).order_by('fecha', 'id').first()

                    costo_referencial = (
                        lote_peps.costo_unitario
                        if lote_peps else Decimal('0.00')
                    )

                    DetalleSolicitud.objects.create(
                        solicitud=solicitud,
                        material=material,
                        cantidad_solicitada=cantidad,
                        precio_unitario_referencial=costo_referencial
                    )

                #6. Registrar la operación en auditoría
                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Solicitudes',
                    accion='Registrar Pedido Almacén (Asignación Directa)',
                    descripcion=(
                        f'Se registró el pedido {codigo} '
                        f'para {unidad_solicitante.nombre}, '
                        f'validado contra asignaciones directas.'
                    )
                )
            messages.success(request, f"Pedido de Almacén {codigo} registrado exitosamente.")
            return redirect('solicitudes')

        except ValueError as e:
            messages.error(request, str(e))
            url_retorno = f"{request.path}?unidad_id={unidad_solicitante.id}" if rol in ['ADMINISTRADOR', 'ADMIN_ALMACENES'] else request.path
            return redirect(url_retorno)
        except Exception as e:
            messages.error(request, f"Error al registrar el pedido: {str(e)}")
            return redirect('nuevo_pedido_almacen')

    # =========================================================
    # GET: CARGAR ÚNICAMENTE MATERIALES CON ASIGNACIÓN DIRECTA > 0 (ESTRICTO TOTAL)
    # =========================================================
    from solicitudes.services import (
        resolver_almacen_para_unidad,
        AlmacenNoDisponible,
        ConfiguracionAlmacenAmbigua,
    )

    try:
        almacen_origen = resolver_almacen_para_unidad(unidad_solicitante)
    except (AlmacenNoDisponible, ConfiguracionAlmacenAmbigua) as e:
        messages.error(request, str(e))
        return redirect('solicitudes')
    asignaciones = AsignacionEntradaUnidad.objects.filter(
        unidad_organizacional=unidad_solicitante,
        cantidad_asignada__gt=F('cantidad_retirada'),
        nota_ingreso_detalle__material__is_active=True,
        nota_ingreso_detalle__nota_ingreso__almacen_destino=almacen_origen
    ).select_related(
        'nota_ingreso_detalle__material',
        'nota_ingreso_detalle__material__partida',
        'nota_ingreso_detalle__material__unidad_medida_fk'
    )

    materiales_dict = {}
    for asig in asignaciones:
        mat = asig.nota_ingreso_detalle.material
        saldo_asig = asig.saldo_disponible

        if saldo_asig > 0:
            if mat.id not in materiales_dict:
                u_med = mat.unidad_medida_fk.codigo if mat.unidad_medida_fk else mat.unidad_medida
                partida_cod = mat.partida.codigo if mat.partida else "—"
                partida_nom = mat.partida.nombre if mat.partida else ""

                inv = InventarioAlmacen.objects.filter(
                    material=mat,
                    almacen=almacen_origen
                ).first()
                stock_real = inv.stock_disponible if inv else 0
                tope = min(saldo_asig, stock_real)

                materiales_dict[mat.id] = {
                    'id': mat.id,
                    'nombre': mat.nombre,
                    'codigo': mat.codigo,
                    'partida_codigo': partida_cod,
                    'partida_nombre': partida_nom,
                    'unidad_medida': u_med,
                    'stock_almacen': stock_real,
                    'costo_unitario': float(asig.nota_ingreso_detalle.precio_unitario),
                    'cupo_asignado': saldo_asig,
                    'cuota_saldo': saldo_asig,
                    'max_solicitable': tope,
                    'modalidad': 'ASIGNACION_DIRECTA',
                    'modalidad_label': f'Asignación Directa ({saldo_asig} {u_med})',
                    'almacen_despacho': almacen_origen.nombre
                }
            else:
                materiales_dict[mat.id]['cupo_asignado'] += saldo_asig
                materiales_dict[mat.id]['cuota_saldo'] += saldo_asig
                nuevo_tope = min(
                    materiales_dict[mat.id]['cupo_asignado'],
                    materiales_dict[mat.id]['stock_almacen']
                )
                materiales_dict[mat.id]['max_solicitable'] = nuevo_tope
                materiales_dict[mat.id]['modalidad_label'] = f"Asignación Directa ({materiales_dict[mat.id]['cupo_asignado']} {materiales_dict[mat.id]['unidad_medida']})"

    materiales_filtrados = list(materiales_dict.values())

    return render(request, 'solicitudes/nuevo_pedido_almacen.html', {
        'materiales': materiales_filtrados,
        'unidad_solicitante': unidad_solicitante,
        'unidades_disponibles': unidades_disponibles,
        'rol': rol
    })

def verificar_documento_publico(request, codigo):
    solicitud = get_object_or_404(
        Solicitud.objects.prefetch_related('detalles__material'),
        codigo=codigo
    )

    if solicitud.estado not in ['ENTREGADA', 'CERRADA']:
        return HttpResponseForbidden("Este documento se encuentra en trámite y no cuenta con certificación de verificación pública aún.")

    return render(request, 'solicitudes/verificar_publico.html', {'solicitud': solicitud})
