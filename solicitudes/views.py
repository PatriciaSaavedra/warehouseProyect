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

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter, landscape
from reportlab.platypus import Table, TableStyle
from reportlab.lib import colors
from reportlab.graphics.shapes import Drawing
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget

from organizacion.models import UnidadOrganizacional
from presupuestos.models import POA
from auditoria.models import Bitacora

from .models import Solicitud, DetalleSolicitud, ESTADOS_SOLICITUD, FLUJOS_ATENCION
from inventario.models import (
    InventarioAlmacen, Material, MovimientoInventario, 
    PartidaPresupuestaria, UnidadMedida, Almacen, AsignacionMaterialUnidad  
)
try:
    from inventario.models import AsignacionMaterialUnidad
except ImportError:
    AsignacionMaterialUnidad = None
from inventario.services import registrar_salida_valorada_peps
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
    Pestaña Personal: Mis Solicitudes creadas por el usuario autenticado.
    """
    perfil = getattr(request.user, 'perfilusuario', None)
    if not perfil:
        return render(request, 'solicitudes/index_propias.html', {'solicitudes': []})

    solicitudes_query = Solicitud.objects.filter(solicitante=request.user)

    query = request.GET.get('q', '').strip()
    filtro_estado = request.GET.get('estado', '').strip()
    desde_str = request.GET.get('desde', '').strip()
    hasta_str = request.GET.get('hasta', '').strip()

    if query:
        solicitudes_query = solicitudes_query.filter(Q(codigo__icontains=query) | Q(justificacion__icontains=query))
    if filtro_estado:
        solicitudes_query = solicitudes_query.filter(estado=filtro_estado)
    if desde_str:
        solicitudes_query = solicitudes_query.filter(fecha__gte=desde_str)
    if hasta_str:
        solicitudes_query = solicitudes_query.filter(fecha__lte=hasta_str)

    solicitudes_query = solicitudes_query.order_by('-id')

    paginator = Paginator(solicitudes_query, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'solicitudes/index_propias.html', {
        'page_obj': page_obj,
        'query': query,
        'filtro_estado': filtro_estado,
        'desde': desde_str,
        'hasta': hasta_str,
        'estados': ESTADOS_SOLICITUD,
        'rol': perfil.rol,
    })


@login_required
@rol_requerido([
    'JEFE_INMEDIATO', 'SECRETARIO_SAF', 'PRESUPUESTOS', 'RPA', 
    'JEFE_ADMINISTRATIVO', 'ALMACENERO', 'KARDISTA', 'ADMINISTRADOR',
    'ADMIN_ALMACENES'
])
def solicitudes_general(request):
    """
    Bandeja de Gestión: Permite a los revisores y a los encargados de almacén auditar folios.
    """
    perfil = request.user.perfilusuario
    rol = perfil.rol
    unidad = perfil.unidad

    # Acceso global para administradores y personal de almacenes
    if rol in ['ADMINISTRADOR', 'ADMIN_ALMACENES', 'ALMACENERO', 'KARDISTA']:
        solicitudes_query = Solicitud.objects.all()
    else:
        solicitudes_query = Solicitud.objects.filter(unidad_solicitante=unidad)

    query = request.GET.get('q', '').strip()
    filtro_estado = request.GET.get('estado', '').strip()
    filtro_flujo = request.GET.get('flujo', '').strip()
    desde_str = request.GET.get('desde', '').strip()
    hasta_str = request.GET.get('hasta', '').strip()

    if query:
        solicitudes_query = solicitudes_query.filter(
            Q(codigo__icontains=query) |
            Q(solicitante__username__icontains=query) |
            Q(unidad_solicitante__nombre__icontains=query) |
            Q(justificacion__icontains=query)
        )
    if filtro_estado:
        solicitudes_query = solicitudes_query.filter(estado=filtro_estado)
    if filtro_flujo:
        solicitudes_query = solicitudes_query.filter(flujo_atencion=filtro_flujo)
    if desde_str:
        solicitudes_query = solicitudes_query.filter(fecha__gte=desde_str)
    if hasta_str:
        solicitudes_query = solicitudes_query.filter(fecha__lte=hasta_str)

    solicitudes_query = solicitudes_query.select_related('solicitante', 'unidad_solicitante').order_by('-id')

    hoy = timezone.now().date()
    primer_dia_mes = hoy.replace(day=1)
    
    pendientes_count = Solicitud.objects.filter(estado='REGISTRADA').count()
    preparacion_count = Solicitud.objects.filter(estado__in=['APROBADA', 'PREPARADA']).count()
    entregas_hoy_count = Solicitud.objects.filter(estado='ENTREGADA', fecha_entrega__date=hoy).count()
    total_folios_count = Solicitud.objects.filter(fecha_registro__date__gte=primer_dia_mes).count()

    paginator = Paginator(solicitudes_query, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'solicitudes/index_general.html', {
        'solicitudes': page_obj, 
        'rol': rol,
        'kpi_pendientes': pendientes_count,
        'kpi_preparacion': preparacion_count,
        'kpi_entregas_hoy': entregas_hoy_count,
        'kpi_total_folios': total_folios_count,
        'query': query,
        'filtro_estado': filtro_estado,
        'filtro_flujo': filtro_flujo,
        'desde': desde_str,
        'hasta': hasta_str,
        'estados': ESTADOS_SOLICITUD,
        'flujos': FLUJOS_ATENCION
    })


@login_required
def buscar_materiales(request):
    q = request.GET.get('q', '').strip()
    unidad_id = request.GET.get('unidad_id')

    if not q:
        return JsonResponse([], safe=False)

    perfil = getattr(request.user, 'perfilusuario', None)
    if unidad_id and perfil.rol in ['ADMINISTRADOR', 'ADMIN_ALMACENES']:
        unidad = UnidadOrganizacional.objects.filter(id=unidad_id).first()
    else:
        unidad = perfil.unidad if perfil else None

    partidas_poa = POA.objects.filter(
        unidad=unidad,
        gestion=GESTION_ACTUAL,
        monto_disponible__gt=0
    ).values_list('partida_id', flat=True) if unidad else []

    query = Material.objects.filter(
        Q(nombre__icontains=q) | Q(codigo__icontains=q),
        partida_id__in=partidas_poa,
        stock_actual__gt=0,
        is_active=True
    ).select_related('partida')[:10]

    data = [
        {
            'id': m.id,
            'nombre': f"{m.codigo} - {m.nombre}",
            'stock': m.stock_actual
        }
        for m in query
    ]
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
    if request.method == 'POST':
        fecha = request.POST.get('fecha')
        payload_raw = request.POST.get("payload")
        tipo_requerimiento = request.POST.get('tipo_requerimiento', '').strip()

        if not tipo_requerimiento or tipo_requerimiento not in ['BIEN', 'SERVICIO']:
            messages.error(request, "Debe seleccionar un Tipo de Requerimiento válido (Bien o Servicio).")
            return redirect('nueva_solicitud_compra')

        if not payload_raw:
            messages.error(request, "No se recibió información de ítems.")
            return redirect('nueva_solicitud_compra')

        payload_raw = html.unescape(payload_raw)
        try:
            payload = json.loads(payload_raw)
        except json.JSONDecodeError:
            messages.error(request, "Error en el formato de los datos enviados.")
            return redirect('nueva_solicitud_compra')

        if not payload:
            messages.error(request, "Debe agregar al menos un ítem.")
            return redirect('nueva_solicitud_compra')

        justificacion = request.POST.get('justificacion', '').strip()
        perfil = getattr(request.user, 'perfilusuario', None)
        unidad_solicitante = perfil.unidad if perfil else None

        if not unidad_solicitante:
            messages.error(request, "Su usuario no tiene asignada una Unidad Organizacional.")
            return redirect('nueva_solicitud_compra')

        try:
            with transaction.atomic():
                ultima = Solicitud.objects.select_for_update().order_by('id').last()
                numero = (ultima.id + 1) if ultima else 1
                
                if tipo_requerimiento == 'SERVICIO':
                    codigo = f'REQ-SRV-{numero:05d}'
                    flujo = 'CONTRATACION_SERVICIO'
                else:
                    codigo = f'REQ-ADQ-{numero:05d}'
                    flujo = 'ADQUISICION'

                solicitud = Solicitud.objects.create(
                    codigo=codigo,
                    unidad_solicitante=unidad_solicitante,
                    solicitante=request.user,
                    fecha=fecha,
                    justificacion=justificacion,
                    tipo_requerimiento=tipo_requerimiento,
                    flujo_atencion=flujo,
                    estado='REGISTRADA'
                )

                for item_key, item_data in payload.items():
                    cantidad = int(item_data.get('cantidad', 1))
                    es_nuevo = item_data.get('es_nuevo', False)
                    precio_ref_raw = item_data.get('precio_referencial', 0)
                    partida_id = item_data.get('partida_id')
                    partida_obj = PartidaPresupuestaria.objects.get(id=partida_id) if partida_id else None

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
                            partida=partida_obj,
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
                            partida=None,
                            cantidad_solicitada=cantidad,
                            precio_unitario_referencial=precio_ref
                        )

                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Solicitudes',
                    accion='Registrar Solicitud Compra/Servicio',
                    descripcion=f'Se registró el Requerimiento de Adquisición {codigo} ({tipo_requerimiento})'
                )

            messages.success(request, f"Requerimiento {codigo} registrado correctamente.")
            return redirect('solicitudes')

        except ValueError as e:
            messages.error(request, str(e))
            return redirect('nueva_solicitud_compra')
        except DatabaseError:
            messages.error(request, "Hubo un error al guardar el requerimiento en la base de datos.")
            return redirect('nueva_solicitud_compra')

    materiales = Material.objects.all().values('id', 'nombre', 'stock_actual')
    partidas = PartidaPresupuestaria.objects.all().order_by('codigo')
    return render(request, 'solicitudes/nueva_solicitud_compra.html', {
        'materiales': materiales,
        'partidas': partidas
    })


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

    Bitacora.objects.create(
        usuario=request.user,
        modulo='Solicitudes',
        accion='Visualizar Detalle Solicitud',
        descripcion=f'El usuario visualizó en pantalla el detalle de la Solicitud Folio: {solicitud.codigo} (Estado: {solicitud.estado}).'
    )
    return render(request, 'solicitudes/detalle.html', {
        'solicitud': solicitud,
        'rol': rol
    })


@login_required
def revisar_solicitud(request, id):
    if not tiene_rol(request.user, ['JEFE_INMEDIATO', 'ADMINISTRADOR', 'ADMIN_ALMACENES']):
        return HttpResponseForbidden("No autorizado.")

    solicitud = get_object_or_404(Solicitud.objects.select_for_update(), id=id)
    
    if not solicitud.tiene_detalles():
        messages.error(request, "La solicitud no contiene ningún material registrado y no puede ser procesada.")
        return redirigir_despues_de_accion(request, solicitud)

    if solicitud.estado != 'REGISTRADA':
        messages.error(request, "Esta solicitud ya no se encuentra en estado Registrada.")
        return redirigir_despues_de_accion(request, solicitud)

    detalles = solicitud.detalles.select_related('material')

    if request.method == 'POST':
        try:
            with transaction.atomic():
                almacen_origen = solicitud.almacen_origen
                if not almacen_origen:
                    raise ValueError(
                        "No se pudo determinar el almacén de origen para esta solicitud. "
                        "Asegúrese de haber registrado un Almacén Central activo (Tipo: 'CENTRAL') "
                        "en el Panel de Administración de Django."
                    )

                for detalle in detalles:
                    aprobada = int(request.POST.get(f"aprobado_{detalle.id}") or 0)
                    if aprobada < 0:
                        aprobada = 0

                    detalle.cantidad_aprobada = aprobada
                    detalle.save()

                    if solicitud.flujo_atencion == 'SALIDA_ALMACEN' and detalle.material:
                        inv, created = InventarioAlmacen.objects.get_or_create(
                            material=detalle.material,
                            almacen=almacen_origen,
                            defaults={'stock_fisico': 0, 'stock_reservado': 0}
                        )

                        if inv.stock_disponible < aprobada:
                            raise ValueError(
                                f"No es posible autorizar. Stock disponible insuficiente en "
                                f"'{detalle.material.nombre}' para el {almacen_origen.nombre}. "
                                f"Solicitado: {aprobada} | Disponible: {inv.stock_disponible}"
                            )
                        
                        inv.stock_reservado += aprobada
                        inv.save()

                solicitud.estado = 'REVISADA'
                solicitud.aprobado_por = request.user.get_full_name() or request.user.username
                solicitud.revisado_por = request.user            
                solicitud.fecha_revision = timezone.now()         
                solicitud.save()

                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Solicitudes',
                    accion='Revisar Solicitud',
                    descripcion=f'El jefe inmediato revisó, autorizó y reservó stock para la solicitud {solicitud.codigo}'
                )

            messages.success(request, f"Solicitud {solicitud.codigo} revisada y autorizada correctamente.")
            return redirigir_despues_de_accion(request, solicitud)

        except ValueError as e:
            messages.error(request, str(e))
            return redirigir_despues_de_accion(request, solicitud)
        except DatabaseError as e:
            messages.error(request, "Error de base de datos al procesar la revisión de la solicitud.")
            return redirigir_despues_de_accion(request, solicitud)

    return render(request, 'solicitudes/aprobar.html', {
        'solicitud': solicitud,
        'detalles': detalles
    })


@login_required
@rol_requerido(['SECRETARIO_SAF', 'ADMINISTRADOR', 'ADMIN_ALMACENES'])
def validar_saf(request, id):
    solicitud = get_object_or_404(Solicitud.objects.select_for_update(), id=id)

    if solicitud.flujo_atencion == 'SALIDA_ALMACEN':
        messages.warning(request, "Las solicitudes con stock disponible pasan directamente de Autorización de Unidad a Preparación en Almacén.")
        return redirigir_despues_de_accion(request, solicitud)

    if solicitud.estado != 'REVISADA':
        messages.error(request, "La solicitud aún no ha sido revisada ni autorizada por su Jefe de Unidad.")
        return redirigir_despues_de_accion(request, solicitud)

    try:
        with transaction.atomic():
            solicitud.estado = 'VALIDADA_SAF'
            solicitud.saf_por = request.user
            solicitud.fecha_saf = timezone.now()
            solicitud.save()

            Bitacora.objects.create(
                usuario=request.user,
                modulo='Solicitudes',
                accion='Validación SAF',
                descripcion=f'El Secretario SAF validó la solicitud {solicitud.codigo}'
            )

        messages.success(request, f"Solicitud {solicitud.codigo} validada por la SAF.")
    except Exception as e:
        messages.error(request, f"Error al procesar validación SAF: {str(e)}")

    return redirigir_despues_de_accion(request, solicitud)


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


@login_required
@rol_requerido(['ALMACENERO', 'ADMINISTRADOR', 'ADMIN_ALMACENES'])
def preparar_solicitud(request, id):
    solicitud = get_object_or_404(Solicitud, id=id)

    if solicitud.flujo_atencion == 'SALIDA_ALMACEN':
        permitido = (solicitud.estado in ['REVISADA', 'VALIDADA_JEFATURA'])
        mensaje_error = "Para solicitudes con stock, se requiere primero la autorización del Jefe de Unidad (Estado: Revisada)."
    else:
        permitido = (solicitud.estado == 'VALIDADA_JEFATURA')
        mensaje_error = "Solo solicitudes validadas administrativamente por la Jefatura Administrativa pueden prepararse."

    if not permitido:
        messages.error(request, mensaje_error)
        return redirigir_despues_de_accion(request, solicitud)

    solicitud.estado = 'PREPARADA'
    solicitud.preparado_por = request.user
    solicitud.fecha_preparado = timezone.now()
    solicitud.save()

    Bitacora.objects.create(
        usuario=request.user,
        modulo='Solicitudes',
        accion='Preparación física',
        descripcion=f'El almacenero preparó y empaquetó físicamente los materiales de la solicitud {solicitud.codigo}'
    )

    messages.success(request, f"La solicitud {solicitud.codigo} ha sido marcada como PREPARADA para su despacho.")
    return redirigir_despues_de_accion(request, solicitud)


@login_required
def entregar_solicitud(request, id):
    """
    Paso 8 (Paso ③ y ④ de la Boleta Oficial GADP):
    - Permite al Almacenero ajustar la 'Cantidad Entregada' físicamente.
    - Muestra la Unidad de Manejo y valida conformidad con el POA / Cuota.
    - Descuenta existencias por PEPS según lo efectivamente entregado.
    - Libera reservas de ítems no entregados y ejecuta presupuesto POA real.
    """
    if not tiene_rol(request.user, ['ALMACENERO', 'ADMINISTRADOR', 'ADMIN_ALMACENES']):
        return HttpResponseForbidden("No tiene autorización para realizar entregas físicas de almacén.")

    solicitud = get_object_or_404(Solicitud, id=id)

    if solicitud.flujo_atencion == 'CONTRATACION_SERVICIO':
        messages.error(request, "Error: Los requerimientos de SERVICIO no generan salida física de inventario.")
        return redirigir_despues_de_accion(request, solicitud)

    if solicitud.estado != 'PREPARADA':
        messages.error(request, "Solo solicitudes en estado PREPARADA pueden ser despachadas físicamente.")
        return redirigir_despues_de_accion(request, solicitud)

    almacen_origen = solicitud.almacen_origen
    if not almacen_origen:
        messages.error(request, "No se pudo determinar el almacén de despacho para esta solicitud.")
        return redirigir_despues_de_accion(request, solicitud)

    detalles = solicitud.detalles.select_related('material', 'material__partida', 'material__unidad_medida_fk')

    # ========================================================
    # FASE 1: MOSTRAR PANTALLA DE DESPACHO (GET)
    # ========================================================
    if request.method == 'GET':
        items_despacho = []
        for d in detalles:
            material = d.material
            cant_sugerida = d.cantidad_aprobada if d.cantidad_aprobada is not None else d.cantidad_solicitada

            # Stock físico disponible en este almacén
            inv = InventarioAlmacen.objects.filter(material=material, almacen=almacen_origen).first()
            stock_disponible = inv.stock_disponible if inv else 0

            # Costo unitario referencial PEPS
            last_ent = MovimientoInventario.objects.filter(
                material=material, tipo='ENTRADA', almacen=almacen_origen
            ).order_by('-fecha').first()
            costo_unitario = last_ent.costo_unitario if last_ent else Decimal('0.00')

            # Definir el año de la solicitud primero:
            gestion_solicitud = solicitud.fecha.year if solicitud.fecha else GESTION_ACTUAL

            # 1. Verificar Cuota Física
            asignacion = None
            if AsignacionMaterialUnidad:
                asignacion = AsignacionMaterialUnidad.objects.filter(
                    unidad=solicitud.unidad_solicitante, 
                    material=material,
                    gestion=gestion_solicitud
                ).first()

            # 2. Verificar POA
            partida = d.partida_afectada
            poa = POA.objects.filter(
                unidad=solicitud.unidad_solicitante, 
                partida=partida,
                gestion=gestion_solicitud
            ).first()

            if asignacion:
                saldo_cuota = max(0, asignacion.cantidad_asignada - asignacion.cantidad_consumida)
                conforme_poa = cant_sugerida <= saldo_cuota
                mensaje_poa = f"Cuota Asignada (Saldo: {saldo_cuota})"
                modalidad = "CUOTA_FISICA"
            elif poa:
                costo_estimado = Decimal(cant_sugerida) * costo_unitario
                conforme_poa = poa.monto_disponible >= costo_estimado
                mensaje_poa = f"POA Partida {partida.codigo} (Saldo: Bs. {poa.monto_disponible:.2f})"
                modalidad = "BOLSA_COMUN"
            else:
                conforme_poa = False
                mensaje_poa = "Sin presupuesto en POA ni Cuota"
                modalidad = "SIN_RESPALDO"

            items_despacho.append({
                'detalle': d,
                'material': material,
                'unidad_manejo': material.unidad_medida_fk.codigo if material.unidad_medida_fk else material.unidad_medida,
                'cant_sugerida': cant_sugerida,
                'stock_disponible': stock_disponible,
                'costo_unitario': costo_unitario,
                'conforme_poa': conforme_poa,
                'mensaje_poa': mensaje_poa,
                'modalidad': modalidad,
            })

        return render(request, 'solicitudes/entregar.html', {
            'solicitud': solicitud,
            'almacen': almacen_origen,
            'items_despacho': items_despacho,
        })

    # ========================================================
    # FASE 2: PROCESAR ENTREGA FÍSICA Y NOTA DE SALIDA (POST)
    # ========================================================
    from inventario.models import NotaSalida, NotaSalidaDetalle

    try:
        with transaction.atomic():
            solicitud = Solicitud.objects.select_for_update().get(id=id)
            costos_partidas = {}

            # Crear cabecera oficial de Nota de Salida
            ultima_salida = NotaSalida.objects.select_for_update().order_by('id').last()
            nro_salida_num = (ultima_salida.id + 1) if ultima_salida else 1
            nro_nota_salida = f"NS-{nro_salida_num:05d}"

            nota_salida = NotaSalida.objects.create(
                nro_nota=nro_nota_salida,
                solicitud_origen=solicitud,
                almacen_origen=almacen_origen,
                unidad_destino=solicitud.unidad_solicitante,
                fecha=timezone.now().date(),
                usuario=request.user
            )

            for detalle in detalles:
                material = Material.objects.get(id=detalle.material.id)
                
                # El almacenero envía la cantidad que realmente entrega
                cant_input = request.POST.get(f"entregado_{detalle.id}")
                try:
                    cantidad_despacho = int(cant_input) if cant_input is not None else detalle.cantidad_solicitada
                except ValueError:
                    cantidad_despacho = 0

                if cantidad_despacho <= 0:
                    continue  # Si no entregó nada de este ítem, no genera movimiento

                # Validar existencia física real
                inv = InventarioAlmacen.objects.select_for_update().filter(material=material, almacen=almacen_origen).first()
                if not inv or inv.stock_fisico < cantidad_despacho:
                    raise ValueError(f"Stock insuficiente en almacén para despachar '{material.nombre}'.")

                # Liberar reserva si entregó menos de lo que estaba reservado
                cant_reservada_previa = detalle.cantidad_aprobada or detalle.cantidad_solicitada
                if cant_reservada_previa > cantidad_despacho:
                    diferencia_no_entregada = cant_reservada_previa - cantidad_despacho
                    inv.stock_reservado = max(0, inv.stock_reservado - diferencia_no_entregada)
                    inv.save()

                # Descontar stock por PEPS (Kardex Valorado)
                mov = registrar_salida_valorada_peps(
                    material=material,
                    almacen=almacen_origen,
                    cantidad_salida=cantidad_despacho,
                    tipo_movimiento='SALIDA',
                    referencia=f"DESPACHO: {solicitud.codigo}",
                    usuario=request.user,
                    unidad_destino=solicitud.unidad_solicitante,
                    descontar_reserva=True
                )

                # Registrar cantidad entregada formalmente
                detalle.cantidad_entregada = cantidad_despacho
                detalle.save()

                # Guardar detalle de la Nota de Salida
                NotaSalidaDetalle.objects.create(
                    nota_salida=nota_salida,
                    material=material,
                    cantidad=cantidad_despacho,
                    costo_unitario_real=mov.costo_unitario,
                    costo_total_real=mov.costo_total
                )

                # Actualizar cuota física si correspondía
                try:
                    q_asig = AsignacionMaterialUnidad.objects.filter(
                        unidad=solicitud.unidad_solicitante, 
                        material=material,
                        gestion=GESTION_ACTUAL
                    ).first() if AsignacionMaterialUnidad else None
                    if q_asig:
                        q_asig.cantidad_consumida += cantidad_despacho
                        q_asig.save()
                except Exception:
                    pass

                partida = material.partida
                costos_partidas[partida] = costos_partidas.get(partida, Decimal('0.00')) + mov.costo_total

            # Ejecutar presupuesto real en el POA
            for partida, costo_real in costos_partidas.items():
                poa = POA.objects.select_for_update().filter(
                    unidad=solicitud.unidad_solicitante,
                    partida=partida,
                    gestion=GESTION_ACTUAL
                ).first()
                if poa:
                    poa.monto_disponible -= costo_real
                    poa.monto_ejecutado += costo_real
                    poa.save()

            solicitud.estado = 'ENTREGADA'
            solicitud.entregado_por = request.user
            solicitud.fecha_entrega = timezone.now()
            solicitud.save()

            Bitacora.objects.create(
                usuario=request.user,
                modulo='Solicitudes',
                accion='Entrega Física Almacén',
                descripcion=f'Se despachó físicamente la Solicitud {solicitud.codigo} mediante Nota de Salida {nro_nota_salida}'
            )

        messages.success(request, f"Despacho físico completado y Nota de Salida {nro_nota_salida} generada exitosamente.")
        return redirect('detalle_solicitud', id=solicitud.id)

    except ValueError as e:
        messages.error(request, str(e))
        return redirect('entregar_solicitud', id=solicitud.id)
    except Exception as e:
        messages.error(request, f"Error al procesar el despacho físico: {str(e)}")
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
    solicitud = get_object_or_404(Solicitud, id=id)
    perfil = request.user.perfilusuario

    if perfil.rol not in ['ADMINISTRADOR', 'ADMIN_ALMACENES']:
        if solicitud.unidad_solicitante != perfil.unidad:
            return HttpResponseForbidden("No tiene permisos para modificar solicitudes de otra unidad.")

    if solicitud.estado != 'REGISTRADA':
        return HttpResponseForbidden("Esta solicitud ya se encuentra en proceso de revisión y no es editable.")

    if request.method == "GET":
        detalles = solicitud.detalles.select_related('material')
        detalles_json = json.dumps([
            {
                "id": d.material.id if d.material else d.id,
                "nombre": d.material.nombre if d.material else d.descripcion_material_no_catalogado,
                "cantidad": d.cantidad_solicitada,
                "stock": d.material.stock_actual if d.material else 0,
                "precio_referencial": float(d.precio_unitario_referencial),
                "es_nuevo": d.es_nueva_adquisicion
            }
            for d in detalles
        ])

        return render(request, "solicitudes/editar.html", {
            "solicitud": solicitud,
            "detalles_json": detalles_json
        })

    try:
        data = json.loads(request.body.decode("utf-8"))
        payload = data.get("payload", {})
        justificacion = data.get("justificacion", "")
        tipo_requerimiento = data.get("tipo_requerimiento", "BIEN")

        if not tipo_requerimiento or tipo_requerimiento not in ['BIEN', 'SERVICIO']:
            return JsonResponse({"ok": False, "error": "Debe seleccionar un tipo de requerimiento válido (Bien o Servicio)."}, status=400)

        if not payload:
            return JsonResponse({"ok": False, "error": "Debe agregar al menos un material a la solicitud"}, status=400)

        with transaction.atomic():
            solicitud.justificacion = justificacion
            solicitud.tipo_requerimiento = tipo_requerimiento
            solicitud.save()
            solicitud.detalles.all().delete()

            for material_id, item_data in payload.items():
                if isinstance(item_data, dict):
                    cantidad = int(item_data.get('cantidad', 1))
                    precio_ref_raw = item_data.get('precio_referencial', 0)
                    es_nuevo = item_data.get('es_nuevo', False)
                    nombre_no_catalogado = item_data.get('nombre', '')
                else:
                    cantidad = int(item_data)
                    precio_ref_raw = 0
                    es_nuevo = False
                    nombre_no_catalogado = ''

                try:
                    precio_ref = Decimal(str(precio_ref_raw))
                except (ValueError, TypeError):
                    precio_ref = Decimal('0.00')

                if cantidad <= 0:
                    return JsonResponse({"ok": False, "error": "La cantidad de los ítems debe ser mayor a cero."}, status=400)

                if precio_ref < 0:
                    return JsonResponse({"ok": False, "error": "El precio referencial no puede ser negativo."}, status=400)

                if es_nuevo or not str(material_id).isdigit():
                    DetalleSolicitud.objects.create(
                        solicitud=solicitud,
                        material=None,
                        es_nueva_adquisicion=True,
                        descripcion_material_no_catalogado=nombre_no_catalogado,
                        cantidad_solicitada=cantidad,
                        precio_unitario_referencial=precio_ref
                    )
                else:
                    material = Material.objects.get(id=material_id)
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
                    descripcion=f'Se registró la Solicitud {solicitud.codigo} con flujo asignado: {solicitud.get_flujo_atencion_display()}'
                )

        return JsonResponse({"ok": True})

    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Formato de datos JSON inválido"}, status=400)


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

                if solicitud.estado in ['REVISADA', 'PREPARADA'] and solicitud.flujo_atencion == 'SALIDA_ALMACEN':
                    for detalle in solicitud.detalles.all():
                        if detalle.material:
                            inv = InventarioAlmacen.objects.select_for_update().filter(
                                material=detalle.material,
                                almacen=solicitud.almacen_origen
                            ).first()
                            if inv:
                                cantidad_reserva = detalle.cantidad_aprobada or detalle.cantidad_solicitada
                                inv.stock_reservado -= cantidad_reserva
                                inv.save()
                
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

                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Solicitudes',
                    accion='Rechazar Solicitud',
                    descripcion=f'Se rechazó la solicitud {solicitud.codigo} por: {motivo}. Fondos del POA liberados.'
                )

            messages.info(request, f"La solicitud {solicitud.codigo} ha sido rechazada y sus fondos han sido liberados.")
            return redirigir_despues_de_accion(request, solicitud)

        except Exception as e:
            messages.error(request, f"Error al procesar el rechazo de la solicitud: {str(e)}")
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
    solicitud = get_object_or_404(
        Solicitud.objects.prefetch_related('detalles__material__partida'),
        id=id
    )

    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="pedido_material_{solicitud.codigo}.pdf"'

    pdf = canvas.Canvas(response, pagesize=landscape(letter))
    width, height = landscape(letter)
    
    pdf.setTitle(f"Pedido de Material {solicitud.codigo}")
    pdf.setSubject("SGA - Gobierno Autónomo Departamental de Potosí")
    pdf.setAuthor("Sistema de Gestión de Almacenes")

    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(50, height - 40, "ESTADO PLURINACIONAL DE BOLIVIA")
    pdf.drawString(50, height - 52, "GOBIERNO AUTÓNOMO DEPARTAMENTAL DE POTOSÍ")
    pdf.setFont("Helvetica", 9)
    pdf.drawString(50, height - 64, "ALMACÉN CENTRAL")

    pdf.setFont("Helvetica-Bold", 15)
    pdf.drawString(50, height - 95, "PEDIDO DE MATERIALES y/o BIENES")

    pdf.setFont("Helvetica", 8)
    right_x = 480
    pdf.drawString(right_x, height - 35, "Programa: _____________________________________")
    pdf.drawString(right_x, height - 47, "Subprograma: __________________________________")
    pdf.drawString(right_x, height - 59, "Proyecto: _____________________________________")
    pdf.drawString(right_x, height - 71, "Act. u Obra: __________________________________")
    pdf.drawString(right_x, height - 83, f"Unid. Ejec.: {solicitud.unidad_solicitante.nombre[:25]}")
    pdf.drawString(right_x, height - 95, f"Código Presup: _______________ Código Nº: {solicitud.codigo}")

    pdf.setFont("Helvetica-Bold", 9)
    fecha_pedido = solicitud.fecha.strftime('%d / %m / %Y') if solicitud.fecha else "__ / __ / ____"
    pdf.drawString(50, height - 120, f"Fecha del Pedido: {fecha_pedido}")

    headers_1 = ['CÓDIGO', 'DESCRIPCIÓN', 'Unidad de\nManejo', 'Cantidad', '', 'Partida\nPresupuestaria', 'Costo (Bs.)', '']
    headers_2 = ['', '', '', 'Pedida', 'Entrega', '', 'Unidad', 'TOTAL']

    data = [headers_1, headers_2]

    for d in solicitud.detalles.all():
        if d.es_nueva_adquisicion:
            desc = d.descripcion_material_no_catalogado
            codigo_mat = "N/C"
            unidad_cod = "N/C"
            partida_cod = "N/C"
            costo_u = Decimal('0.00')
        else:
            desc = d.material.nombre[:40]
            codigo_mat = d.material.codigo
            unidad_cod = d.material.unidad_medida_fk.codigo if d.material.unidad_medida_fk else d.material.unidad_medida
            partida_cod = d.material.partida.codigo
            
            last_entrada = MovimientoInventario.objects.filter(material=d.material, tipo='ENTRADA').order_by('-fecha').first()
            costo_u = last_entrada.costo_unitario if last_entrada else Decimal('0.00')

        cant_pedida = d.cantidad_solicitada
        cant_entrega = d.cantidad_entregada
        costo_total = cant_entrega * costo_u

        data.append([
            codigo_mat,
            desc,
            unidad_cod,
            str(cant_pedida),
            str(cant_entrega) if cant_entrega > 0 else "—",
            partida_cod,
            f"{costo_u:.2f}",
            f"{costo_total:.2f}" if costo_total > 0 else "—"
        ])

    col_widths = [75, 192, 55, 45, 45, 80, 100, 100]
    t = Table(data, colWidths=col_widths)

    t_style = TableStyle([
        ('SPAN', (0, 0), (0, 1)),  
        ('SPAN', (1, 0), (1, 1)),  
        ('SPAN', (2, 0), (2, 1)),  
        ('SPAN', (3, 0), (4, 0)),  
        ('SPAN', (5, 0), (5, 1)),  
        ('SPAN', (6, 0), (7, 0)),  
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('ALIGN', (1, 2), (1, -1), 'LEFT'),
        ('ALIGN', (6, 2), (-1, -1), 'RIGHT'),
        ('FONTNAME', (0, 0), (-1, 1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 1), 8),
        ('BACKGROUND', (0, 0), (-1, 1), colors.HexColor('#F3F4F6')),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#D1D5DB')),
        ('LINEBELOW', (0, 1), (-1, 1), 1, colors.HexColor('#9CA3AF')),
        ('FONTNAME', (0, 2), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 2), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
    ])
    t.setStyle(t_style)

    table_height = len(data) * 18
    t.wrapOn(pdf, 50, height - 150 - table_height)
    t.drawOn(pdf, 50, height - 150 - table_height)

    pdf.setFont("Helvetica", 7.5)
    y_firma_1 = 90
    pdf.drawString(50, y_firma_1, "___________________________")
    pdf.drawString(50, y_firma_1 - 10, "Pedido por:")
    pdf.setFont("Helvetica-Bold", 7.5)
    pdf.drawString(50, y_firma_1 - 20, f"{solicitud.solicitante.get_full_name() or solicitud.solicitante.username}")
    
    pdf.setFont("Helvetica", 7.5)
    pdf.drawString(230, y_firma_1, "___________________________")
    pdf.drawString(230, y_firma_1 - 10, "Autorizado por:")
    pdf.setFont("Helvetica-Bold", 7.5)
    pdf.drawString(230, y_firma_1 - 20, "Director Administrativo")
    
    pdf.setFont("Helvetica", 7.5)
    pdf.drawString(410, y_firma_1, "___________________________")
    pdf.drawString(410, y_firma_1 - 10, "Entregado por:")
    pdf.setFont("Helvetica-Bold", 7.5)
    entregador = solicitud.entregado_por.get_full_name() if solicitud.entregado_por else "Almacenero"
    pdf.drawString(410, y_firma_1 - 20, entregador)
    
    pdf.setFont("Helvetica", 7.5)
    pdf.drawString(590, y_firma_1, "___________________________")
    pdf.drawString(590, y_firma_1 - 10, "Recibido por:")
    pdf.setFont("Helvetica-Bold", 7.5)
    pdf.drawString(590, y_firma_1 - 20, "Firma del Solicitante")

    y_firma_2 = 40
    pdf.setFont("Helvetica", 7.5)
    pdf.drawString(140, y_firma_2, "___________________________")
    pdf.drawString(140, y_firma_2 - 10, "Control Existencias:")
    pdf.setFont("Helvetica-Bold", 7.5)
    pdf.drawString(140, y_firma_2 - 20, "Kardista de Almacén")

    pdf.setFont("Helvetica", 7.5)
    pdf.drawString(320, y_firma_2, "___________________________")
    pdf.drawString(320, y_firma_2 - 10, "Presupuestos:")
    pdf.setFont("Helvetica-Bold", 7.5)
    presupuestador = solicitud.presupuestado_por.get_full_name() if solicitud.presupuestado_por else "Unidad de Presupuestos"
    pdf.drawString(320, y_firma_2 - 20, presupuestador)

    pdf.setFont("Helvetica-Bold", 8)
    fecha_salida = solicitud.fecha_entrega.strftime('%d / %m / %Y') if solicitud.fecha_entrega else "__ / __ / ____"
    pdf.drawString(540, y_firma_2, f"Fecha de salida física: {fecha_salida}")

    if solicitud.estado in ['ENTREGADA', 'CERRADA']:
        qr_url = request.build_absolute_uri(reverse('verificar_documento_publico', args=[solicitud.codigo]))
        qr_code = QrCodeWidget(qr_url)
        bounds = qr_code.getBounds()
        width_qr = bounds[2] - bounds[0]
        height_qr = bounds[3] - bounds[1]
        
        d = Drawing(55, 55, transform=[55./width_qr, 0, 0, 55./height_qr, 0, 0])
        d.add(qr_code)
        renderPDF.draw(d, pdf, 710, 15)
        
        pdf.setFont("Helvetica-Bold", 6.5)
        pdf.setFillColor(colors.HexColor('#16A34A'))
        pdf.drawString(540, 15, f"CÓDIGO DE VALIDACIÓN: {solicitud.codigo}-2026-SABS-OK")
        pdf.setFillColor(colors.black)
    else:
        pdf.setFont("Helvetica-Bold", 8)
        pdf.setFillColor(colors.HexColor('#DC2626'))
        pdf.drawString(540, 15, "DOCUMENTO EN TRÁMITE - SIN VALOR OFICIAL")
        pdf.setFillColor(colors.black)

    pdf.save()
    return response


@transaction.atomic
@login_required
@rol_requerido(['ADMINISTRADOR', 'ADMIN_ALMACENES'])
def retroceder_estado_solicitud(request, id):
    solicitud = get_object_or_404(Solicitud.objects.select_for_update(), id=id)
    estado_actual = solicitud.estado

    if solicitud.flujo_atencion == 'SALIDA_ALMACEN':
        map_retroceso = {
            'REVISADA': ('REGISTRADA', 'revisado_por', 'fecha_revision'),
            'PREPARADA': ('REVISADA', 'preparado_por', 'fecha_preparado'),
        }
    else:
        map_retroceso = {
            'REVISADA': ('REGISTRADA', 'revisado_por', 'fecha_revision'),
            'VALIDADA_SAF': ('REVISADA', 'saf_por', 'fecha_saf'),
            'VALIDADA_PRESUPUESTOS': ('VALIDADA_SAF', 'presupuestado_por', 'fecha_presupuesto'),
            'VALIDADA_RPA': ('VALIDADA_PRESUPUESTOS', 'rpa_por', 'fecha_rpa'),
            'VALIDADA_JEFATURA': ('VALIDADA_RPA', 'jefatura_por', 'fecha_jefatura'),
            'PREPARADA': ('VALIDADA_JEFATURA', 'preparado_por', 'fecha_preparado'),
        }

    if estado_actual not in map_retroceso:
        messages.error(
            request, 
            f"No es posible revertir el estado de una solicitud en fase {solicitud.get_estado_display()} "
            "o que ya ha sido entregada físicamente en Almacén."
        )
        return redirect(request.META.get('HTTP_REFERER', 'solicitudes'))

    nuevo_estado, campo_usuario, campo_fecha = map_retroceso[estado_actual]

    try:
        with transaction.atomic():
            solicitud.estado = nuevo_estado
            setattr(solicitud, campo_usuario, None)
            setattr(solicitud, campo_fecha, None)
            solicitud.save()

            Bitacora.objects.create(
                usuario=request.user,
                modulo='Solicitudes',
                accion='Reversión de Estado Administrativo',
                descripcion=(
                    f'El Administrador revirtió el estado de la solicitud {solicitud.codigo} '
                    f'desde {estado_actual} al paso anterior: {nuevo_estado}.'
                )
            )

        messages.success(request, f"Se ha revertido con éxito el estado de la solicitud {solicitud.codigo} a '{solicitud.get_estado_display()}'.")
    except Exception as e:
        messages.error(request, f"Error al procesar la reversión del estado: {str(e)}")

    return redirect(request.META.get('HTTP_REFERER', 'solicitudes'))

@login_required
def nuevo_pedido_almacen(request):
    """
    Registra pedidos de consumo aplicando:
    1. Aislamiento físico: Solo descuenta del almacén autorizado para la Unidad Solicitante.
    2. Regla Híbrida (Tarjeta 1): Modo CUOTA FÍSICA si existe asignación previa, o BOLSA COMÚN (POA).
    3. Precio Referencial (Tarjeta 2): Guarda la estimación unitaria referencial para control del POA.
    """
    perfil = getattr(request.user, 'perfilusuario', None)
    rol = perfil.rol if perfil else 'UNIDAD_SOLICITANTE'

    # ========================================================
    # 1. RESOLVER LA UNIDAD SOLICITANTE
    # ========================================================
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

    # ========================================================
    # 2. IDENTIFICAR ALMACENES AUTORIZADOS DE DESPACHO
    # ========================================================
    almacenes_autorizados = Almacen.objects.filter(
        unidades_atendidas=unidad_solicitante,
        is_active=True
    )
    if not almacenes_autorizados.exists():
        almacenes_autorizados = Almacen.objects.filter(tipo='CENTRAL', is_active=True)

    # ========================================================
    # 3. PROCESAMIENTO DEL POST (CREACIÓN DEL PEDIDO)
    # ========================================================
    if request.method == 'POST':
        fecha = request.POST.get('fecha')
        payload_raw = request.POST.get("payload")
        justificacion = request.POST.get('justificacion', '').strip()

        if not payload_raw:
            messages.error(request, "No se recibió información de materiales.")
            return redirect('nuevo_pedido_almacen')

        payload_raw = html.unescape(payload_raw)
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
                ultima = Solicitud.objects.select_for_update().order_by('id').last()
                numero = (ultima.id + 1) if ultima else 1
                codigo = f'PED-{numero:05d}'

                solicitud = Solicitud.objects.create(
                    codigo=codigo,
                    unidad_solicitante=unidad_solicitante,
                    solicitante=request.user,
                    fecha=fecha,
                    justificacion=justificacion,
                    tipo_requerimiento='BIEN',
                    flujo_atencion='SALIDA_ALMACEN',
                    estado='REGISTRADA'
                )

                costo_acumulado_por_partida = {}

                for item_key, item_data in payload.items():
                    cantidad = int(item_data.get('cantidad', 1))
                    if cantidad <= 0:
                        raise ValueError("La cantidad solicitada debe ser mayor a cero.")

                    material = Material.objects.select_related('partida').get(id=item_key)

                    # 1. Costo referencial interno para control de POA (el usuario no lo escribe)
                    last_ent = MovimientoInventario.objects.filter(
                        material=material, 
                        tipo='ENTRADA',
                        almacen__in=almacenes_autorizados
                    ).order_by('-fecha').first()
                    costo_referencial = last_ent.costo_unitario if last_ent else Decimal('0.00')

                    # 2. Validación física en el almacén
                    stock_almacen_autorizado = InventarioAlmacen.objects.filter(
                        material=material,
                        almacen__in=almacenes_autorizados
                    ).aggregate(
                        disponible=Sum(F('stock_fisico') - F('stock_reservado'))
                    )['disponible'] or 0

                    if stock_almacen_autorizado < cantidad:
                        raise ValueError(
                            f"Stock insuficiente en el almacén para '{material.nombre}'. "
                            f"Disponible: {stock_almacen_autorizado} | Solicitado: {cantidad}."
                        )

                    # 3. Validación de Cuota vs Bolsa Común
                    asignacion = AsignacionMaterialUnidad.objects.filter(
                        unidad=unidad_solicitante, 
                        material=material,
                        gestion=GESTION_ACTUAL
                    ).first() if AsignacionMaterialUnidad else None

                    if asignacion:
                        saldo_cuota = max(0, asignacion.cantidad_asignada - asignacion.cantidad_consumida)
                        if cantidad > saldo_cuota:
                            raise ValueError(f"Supera la cuota autorizada ({saldo_cuota} disp.) para '{material.nombre}'.")
                    else:
                        partida = material.partida
                        if not partida:
                            raise ValueError(f"El material '{material.nombre}' no tiene partida presupuestaria.")
                        
                        poa = POA.objects.select_for_update().filter(
                            unidad=unidad_solicitante,
                            partida=partida,
                            gestion=GESTION_ACTUAL
                        ).first()

                        if not poa:
                            raise ValueError(f"La unidad no tiene la partida {partida.codigo} en su POA {GESTION_ACTUAL}.")

                        subtotal_estimado = Decimal(cantidad) * costo_referencial
                        costo_acumulado_por_partida[poa] = costo_acumulado_por_partida.get(poa, Decimal('0.00')) + subtotal_estimado

                    # 4. Guardar Detalle (Paso ①: Cantidad solicitada)
                    DetalleSolicitud.objects.create(
                        solicitud=solicitud,
                        material=material,
                        cantidad_solicitada=cantidad,
                        precio_unitario_referencial=costo_referencial
                    )
                # C. Validar techos financieros para los ítems que consumen Bolsa Común
                for poa, total_partida in costo_acumulado_por_partida.items():
                    if poa.monto_disponible < total_partida:
                        raise ValueError(
                            f"Presupuesto insuficiente en la partida {poa.partida.codigo}. "
                            f"Total requerido: {total_partida:.2f} Bs. | Saldo POA disponible: {poa.monto_disponible:.2f} Bs."
                        )

                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Solicitudes',
                    accion='Registrar Pedido Almacén',
                    descripcion=f'Se registró el Pedido {codigo} para {unidad_solicitante.nombre}'
                )

            messages.success(request, f"Pedido de Almacén {codigo} registrado exitosamente.")
            return redirect('solicitudes')

        except ValueError as e:
            messages.error(request, str(e))
            return redirect(f"{request.path}?unidad_id={unidad_solicitante.id}" if rol in ['ADMINISTRADOR', 'ADMIN_ALMACENES'] else request.path)
        except Exception as e:
            messages.error(request, f"Error al registrar el pedido: {str(e)}")
            return redirect('nuevo_pedido_almacen')

    # ========================================================
    # 4. CARGA DEL FORMULARIO (GET)
    # ========================================================
    poas_unidad = POA.objects.filter(
        unidad=unidad_solicitante,
        gestion=GESTION_ACTUAL,
        monto_disponible__gt=0
    ).select_related('partida')

    partidas_permitidas_ids = list(poas_unidad.values_list('partida_id', flat=True))
    poa_por_partida = {p.partida_id: p for p in poas_unidad}

    # Cargar cuotas físicas programadas
    cuotas_unidad = {}
    try:
        q_cuotas = AsignacionMaterialUnidad.objects.filter(unidad=unidad_solicitante)
        if hasattr(AsignacionMaterialUnidad, 'gestion'):
            q_cuotas = q_cuotas.filter(gestion=GESTION_ACTUAL)
        for asig in q_cuotas:
            cant_asig = getattr(asig, 'cantidad_asignada', getattr(asig, 'cantidad', 0))
            cant_cons = getattr(asig, 'cantidad_consumida', 0)
            cuotas_unidad[asig.material_id] = {
                'asignada': cant_asig,
                'consumida': cant_cons,
                'saldo': max(0, cant_asig - cant_cons)
            }
    except Exception:
        cuotas_unidad = {}

    materiales_con_cuota_ids = list(cuotas_unidad.keys())

    # Traer existencias de almacenes autorizados
    inventarios_unidad = InventarioAlmacen.objects.filter(
        Q(material__partida_id__in=partidas_permitidas_ids) | Q(material_id__in=materiales_con_cuota_ids),
        almacen__in=almacenes_autorizados,
        stock_fisico__gt=0
    ).select_related('material', 'material__partida', 'material__unidad_medida_fk', 'almacen')

    stock_por_material = {}
    for inv in inventarios_unidad:
        mat_id = inv.material_id
        disp = max(0, inv.stock_fisico - inv.stock_reservado)
        if disp > 0:
            if mat_id not in stock_por_material:
                stock_por_material[mat_id] = {
                    'material': inv.material,
                    'stock_autorizado': 0,
                    'almacen_nombre': inv.almacen.nombre
                }
            stock_por_material[mat_id]['stock_autorizado'] += disp

    materiales_filtrados = []
    for mat_id, data_item in stock_por_material.items():
        mat = data_item['material']
        stock_autorizado = data_item['stock_autorizado']
        poa = poa_por_partida.get(mat.partida_id)

        # Precio referencial sugerido (último PEPS de entrada)
        last_ent = MovimientoInventario.objects.filter(
            material=mat,
            tipo='ENTRADA',
            almacen__in=almacenes_autorizados
        ).order_by('-fecha').first()
        costo_u = last_ent.costo_unitario if last_ent else Decimal('0.00')

        # Evaluación de Modalidad (Tarjeta 1)
        if mat_id in cuotas_unidad:
            modalidad = 'CUOTA_FISICA'
            modalidad_label = 'Cuota Asignada'
            saldo_cuota = cuotas_unidad[mat_id]['saldo']
            cuota_asig = cuotas_unidad[mat_id]['asignada']
            cuota_cons = cuotas_unidad[mat_id]['consumida']
            max_solicitable = min(saldo_cuota, stock_autorizado)
        else:
            modalidad = 'BOLSA_COMUN'
            modalidad_label = 'Bolsa Común'
            saldo_cuota = 0
            cuota_asig = 0
            cuota_cons = 0
            if costo_u > 0 and poa:
                cupo_max_poa = int(poa.monto_disponible // costo_u)
            else:
                cupo_max_poa = stock_autorizado
            max_solicitable = min(cupo_max_poa, stock_autorizado)

        if max_solicitable > 0:
            materiales_filtrados.append({
                'id': mat.id,
                'nombre': mat.nombre,
                'codigo': mat.codigo,
                'partida_codigo': mat.partida.codigo if mat.partida else '—',
                'partida_nombre': mat.partida.nombre if mat.partida else 'Sin partida',
                'unidad_medida': mat.unidad_medida_fk.codigo if mat.unidad_medida_fk else mat.unidad_medida,
                'stock_almacen': stock_autorizado,
                'costo_unitario': float(costo_u),
                'saldo_poa': float(poa.monto_disponible) if poa else 0.0,
                'max_solicitable': max_solicitable,
                'almacen_despacho': data_item['almacen_nombre'],
                'modalidad': modalidad,
                'modalidad_label': modalidad_label,
                'cuota_asignada': cuota_asig,
                'cuota_consumida': cuota_cons,
                'cuota_saldo': saldo_cuota,
            })

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