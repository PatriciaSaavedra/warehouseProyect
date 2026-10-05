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
import os

from django.conf import settings

from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, landscape
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics import renderPDF

from solicitudes.models import Solicitud

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
    if unidad_id and perfil and perfil.rol in ['ADMINISTRADOR', 'ADMIN_ALMACENES']:
        unidad = UnidadOrganizacional.objects.filter(id=unidad_id).first()
    else:
        unidad = perfil.unidad if perfil else None

    from inventario.models import Material, AsignacionEntradaUnidad
    from django.db.models import F, Q, Sum

    es_admin_o_almacen = perfil and perfil.rol in ['ADMINISTRADOR', 'ADMIN_ALMACENES', 'ALMACENERO']

    data = []

    # CASO 1: UNIDAD SOLICITANTE NORMAL (Filtrar por Asignación Física Directa)
    if unidad and not es_admin_o_almacen:
        # Buscamos asignaciones activas de esta unidad que tengan saldo disponible
        asignaciones = AsignacionEntradaUnidad.objects.filter(
            unidad_organizacional=unidad,
            cantidad_asignada__gt=F('cantidad_retirada'),
            nota_ingreso_detalle__material__is_active=True
        ).filter(
            Q(nota_ingreso_detalle__material__nombre__icontains=q) |
            Q(nota_ingreso_detalle__material__codigo__icontains=q)
        ).select_related('nota_ingreso_detalle__material', 'nota_ingreso_detalle__material__unidad_medida_fk')

        # Agrupar por material para sumar el saldo de distintos lotes asignados a la misma oficina
        materiales_dict = {}
        for asig in asignaciones:
            mat = asig.nota_ingreso_detalle.material
            saldo_asig = asig.saldo_disponible

            if mat.id not in materiales_dict:
                u_med = mat.unidad_medida_fk.codigo if mat.unidad_medida_fk else (mat.unidad_medida or 'UND')
                materiales_dict[mat.id] = {
                    'id': mat.id,
                    'codigo': mat.codigo,
                    'nombre': mat.nombre,
                    'unidad': u_med,
                    'saldo_disponible': saldo_asig,
                }
            else:
                materiales_dict[mat.id]['saldo_disponible'] += saldo_asig

        data = [
            {
                'id': item['id'],
                'nombre': f"{item['codigo']} - {item['nombre']} (Cupo: {item['saldo_disponible']} {item['unidad']})",
                'stock': item['saldo_disponible']  # Su tope de pedido es su saldo asignado
            }
            for item in materiales_dict.values()
        ][:10]

    # CASO 2: ADMINISTRADOR / ALMACENERO (Búsqueda general en catálogo)
    else:
        query = Material.objects.filter(
            Q(nombre__icontains=q) | Q(codigo__icontains=q),
            stock_actual__gt=0,
            is_active=True
        ).select_related('unidad_medida_fk')[:10]

        data = [
            {
                'id': m.id,
                'nombre': f"{m.codigo} - {m.nombre} (Stock Global: {m.stock_actual})",
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

    if request.method == 'POST':
        # El almacenero envía las cantidades reales que va a entregar
        with transaction.atomic():
            for det in solicitud.detalles.all():
                campo_cant = f"cantidad_aprobar_{det.id}"
                if campo_cant in request.POST:
                    cant_a_entregar = int(request.POST.get(campo_cant, 0))
                    
                    # Validar contra stock real del almacén
                    inv = InventarioAlmacen.objects.filter(
                        material=det.material, 
                        almacen=solicitud.almacen_origen
                    ).first()
                    
                    stock_real = inv.stock_disponible if inv else 0
                    
                    # No puede entregar más de lo que hay físicamente
                    if cant_a_entregar > stock_real:
                        messages.error(request, f"No puedes entregar {cant_a_entregar} de {det.material.nombre}, solo hay {stock_real} en estantería.")
                        return redirect('preparar_solicitud', id=solicitud.id)
                    
                    # Se asigna la cantidad física verificada
                    det.cantidad_aprobada = cant_a_entregar
                    det.cantidad_entregada = cant_a_entregar
                    det.save()

            # Pasa al estado que habilita la impresión
            solicitud.estado = 'PREPARADA'
            solicitud.preparado_por = request.user
            solicitud.fecha_preparado = timezone.now()
            solicitud.save()

            messages.success(request, f"Solicitud {solicitud.codigo} preparada con éxito. Formulario listo para impresión y entrega.")
            return redirect('detalle_solicitud', id=solicitud.id)

    return render(request, 'solicitudes/preparar.html', {'solicitud': solicitud})

@login_required
@rol_requerido(['ALMACENERO', 'ADMINISTRADOR', 'ADMIN_ALMACENES'])
def entregar_solicitud(request, id):
    solicitud = get_object_or_404(Solicitud, id=id)

    if solicitud.flujo_atencion == 'CONTRATACION_SERVICIO':
        messages.error(request, "Un requerimiento de SERVICIO no admite entrega física de inventario.")
        return redirigir_despues_de_accion(request, solicitud)

    if solicitud.estado != 'PREPARADA':
        messages.error(request, "Solo solicitudes en estado PREPARADA pueden despacharse físicamente.")
        return redirigir_despues_de_accion(request, solicitud)

    almacen_origen = solicitud.almacen_origen
    if not almacen_origen:
        messages.error(request, "No se encontró un Almacén Central activo configurado en el sistema.")
        return redirigir_despues_de_accion(request, solicitud)

    detalles = solicitud.detalles.select_related('material', 'material__partida', 'material__unidad_medida_fk')
    from inventario.models import AsignacionEntradaUnidad, NotaSalida, NotaSalidaDetalle

    # =========================================================
    # GET: PANTALLA DE DESPACHO CON CONTROL DE CUPO ASIGNADO
    # =========================================================
    if request.method == 'GET':
        items_despacho = []
        for det in detalles:
            material = det.material
            cant_sugerida = det.cantidad_aprobada if det.cantidad_aprobada is not None else det.cantidad_solicitada

            inv = InventarioAlmacen.objects.filter(material=material, almacen=almacen_origen).first()
            stock_disp = inv.stock_fisico if inv else 0

            # Consultar cupo asignado en entradas para esta oficina
            asigs = AsignacionEntradaUnidad.objects.filter(
                unidad_organizacional=solicitud.unidad_solicitante,
                nota_ingreso_detalle__material=material,
                cantidad_asignada__gt=F('cantidad_retirada')
            )
            tot_asig = asigs.aggregate(s=Sum('cantidad_asignada'))['s'] or 0
            tot_ret = asigs.aggregate(s=Sum('cantidad_retirada'))['s'] or 0
            saldo_cupo = max(0, tot_asig - tot_ret)

            conforme_cupo = (saldo_cupo >= cant_sugerida) or (saldo_cupo > 0)
            mensaje_cupo = f"Cupo Asignado: {saldo_cupo} u. disponibles" if saldo_cupo > 0 else "Bolsa Libre de Almacén (Sin asignación previa)"

            items_despacho.append({
                'detalle': det,
                'material': material,
                'unidad_manejo': material.unidad_medida_fk.codigo if material.unidad_medida_fk else material.unidad_medida,
                'cant_sugerida': cant_sugerida,
                'stock_disponible': stock_disp,
                'conforme_poa': conforme_cupo, # mantiene nombre de variable para no romper el template
                'mensaje_poa': mensaje_cupo,
                'saldo_cupo': saldo_cupo
            })

        return render(request, 'solicitudes/entregar.html', {
            'solicitud': solicitud,
            'almacen': almacen_origen,
            'items_despacho': items_despacho
        })

    # =========================================================
    # POST: EJECUCIÓN DEL DESPACHO FÍSICO Y DESCUENTO DE CUPOS
    # =========================================================
    try:
        with transaction.atomic():
            solicitud_lock = Solicitud.objects.select_for_update().get(id=id)

            ultima_salida = NotaSalida.objects.select_for_update().order_by('id').last()
            nro_salida_num = (ultima_salida.id + 1) if ultima_salida else 1
            nro_nota_salida = f"NS-{nro_salida_num:05d}"

            nota_salida = NotaSalida.objects.create(
                nro_nota=nro_nota_salida,
                solicitud_origen=solicitud_lock,
                almacen_origen=almacen_origen,
                unidad_destino=solicitud_lock.unidad_solicitante,
                fecha=timezone.now().date(),
                usuario=request.user
            )

            for detalle in detalles:
                material = Material.objects.select_for_update().get(id=detalle.material.id)
                
                cant_post = request.POST.get(f"entregado_{detalle.id}")
                try:
                    cantidad_despacho = int(cant_post) if cant_post else (detalle.cantidad_aprobada or detalle.cantidad_solicitada)
                except ValueError:
                    cantidad_despacho = detalle.cantidad_aprobada or detalle.cantidad_solicitada

                if cantidad_despacho <= 0:
                    continue

                inv = InventarioAlmacen.objects.select_for_update().filter(material=material, almacen=almacen_origen).first()
                if not inv or inv.stock_fisico < cantidad_despacho:
                    raise ValueError(f"Stock físico insuficiente en {almacen_origen.nombre} para '{material.nombre}'.")

                # 1. Ejecutar salida PEPS y descontar stock de estantería
                mov = registrar_salida_valorada_peps(
                    material=material,
                    almacen=almacen_origen,
                    cantidad_salida=cantidad_despacho,
                    tipo_movimiento='SALIDA',
                    referencia=f"DESPACHO: {solicitud_lock.codigo}",
                    usuario=request.user,
                    unidad_destino=solicitud_lock.unidad_solicitante,
                    descontar_reserva=True
                )

                # 2. DESCONTAR CUPO ASIGNADO EN ENTRADAS A ESTA OFICINA (FIFO)
                asignaciones_oficina = AsignacionEntradaUnidad.objects.select_for_update().filter(
                    unidad_organizacional=solicitud_lock.unidad_solicitante,
                    nota_ingreso_detalle__material=material,
                    cantidad_asignada__gt=F('cantidad_retirada')
                ).order_by('id')

                cant_a_descontar_cupo = cantidad_despacho
                for asig in asignaciones_oficina:
                    if cant_a_descontar_cupo <= 0:
                        break
                    disp = asig.saldo_disponible
                    if disp >= cant_a_descontar_cupo:
                        asig.cantidad_retirada += cant_a_descontar_cupo
                        asig.save()
                        cant_a_descontar_cupo = 0
                    else:
                        asig.cantidad_retirada += disp
                        asig.save()
                        cant_a_descontar_cupo -= disp

                # 3. Guardar detalle de salida
                detalle.cantidad_entregada = cantidad_despacho
                detalle.precio_unitario_referencial = mov.costo_unitario
                detalle.save()

                NotaSalidaDetalle.objects.create(
                    nota_salida=nota_salida,
                    material=material,
                    cantidad=cantidad_despacho,
                    costo_unitario_real=mov.costo_unitario,
                    costo_total_real=mov.costo_total
                )

            # 4. Finalizar trámite
            solicitud_lock.estado = 'ENTREGADA'
            solicitud_lock.entregado_por = request.user
            solicitud_lock.fecha_entrega = timezone.now()
            solicitud_lock.save()

            Bitacora.objects.create(
                usuario=request.user,
                modulo='Inventario',
                accion='Despacho Físico Procesado',
                descripcion=f'Se emitió la Nota de Salida {nro_nota_salida} para el folio {solicitud_lock.codigo} con descuento de asignaciones.'
            )

        messages.success(request, f"Despacho procesado exitosamente. Se generó la Nota de Salida {nro_nota_salida}.")
        return redirect('detalle_solicitud', id=solicitud.id)

    except Exception as e:
        messages.error(request, f"Error al procesar la entrega: {str(e)}")
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
        Solicitud.objects.prefetch_related('detalles__material__partida', 'detalles__partida'),
        id=id
    )

    # ========================================================
    # 1. REGLA SABS: BLOQUEO HASTA VERIFICACIÓN/APROBACIÓN DE ALMACENES
    # ========================================================
    if solicitud.estado not in ['PREPARADA', 'ENTREGADA', 'CERRADA']:
        messages.warning(
            request, 
            "El Formulario Oficial de Pedido no puede imprimirse hasta que Almacén verifique existencias y prepare/apruebe el despacho."
        )
        return redirect('detalle_solicitud', id=solicitud.id)

    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="pedido_material_{solicitud.codigo}.pdf"'

    # Formato horizontal idéntico a la hoja física (Landscape Letter: 792 x 612 pt)
    pdf = canvas.Canvas(response, pagesize=landscape(letter))
    width, height = landscape(letter)
    
    pdf.setTitle(f"Pedido de Material {solicitud.codigo}")
    pdf.setSubject("SGA - Gobierno Autónomo Departamental de Potosí")
    pdf.setAuthor("Sistema de Gestión de Almacenes")

    # ========================================================
    # 2. LOGO INSTITUCIONAL OFICIAL
    # ========================================================
    posibles_logos = [
        os.path.join(settings.BASE_DIR, 'static', 'img', 'logo_gober_horizontal.png'),
        os.path.join(settings.BASE_DIR, 'static', 'img', 'cropped-cropped-logo-vertical-gober.jpg'),
    ]
    for ruta in posibles_logos:
        if os.path.exists(ruta):
            try:
                pdf.drawImage(ruta, 45, height - 68, width=60, height=45, preserveAspectRatio=True, mask='auto')
                break
            except Exception:
                pass

    # ========================================================
    # 3. ENCABEZADO INSTITUCIONAL CENTRADO (Idéntico a la hoja física)
    # ========================================================
    pdf.setFont("Helvetica-Bold", 10.5)
    pdf.drawString(115, height - 32, "GOBIERNO AUTÓNOMO DEPARTAMENTAL DE POTOSÍ")
    pdf.setFont("Helvetica", 8)
    almacen_nombre = solicitud.almacen_origen.nombre.upper() if hasattr(solicitud, 'almacen_origen') and solicitud.almacen_origen else "ALMACÉN CENTRAL"
    pdf.drawString(115, height - 44, f"{almacen_nombre} DEL GOBIERNO AUTÓNOMO DEPARTAMENTAL DE POTOSÍ")

    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(115, height - 66, "PEDIDO DE MATERIAL y/o BIENES")

    # Fecha del Pedido en formato formulario
    pdf.setFont("Helvetica", 8)
    dia = solicitud.fecha.strftime('%d') if solicitud.fecha else "___"
    mes = solicitud.fecha.strftime('%m') if solicitud.fecha else "___"
    anio = solicitud.fecha.strftime('%Y') if solicitud.fecha else "2026"
    pdf.drawString(115, height - 80, f"Fecha del Pedido: {dia} de {mes} de {anio}")

    # Cuadro de Metadatos Presupuestarios (Derecha)
    right_x = 520
    pdf.setFont("Helvetica", 7.5)
    pdf.drawString(right_x, height - 30, "Programa: _____________________________________")
    pdf.drawString(right_x, height - 42, "Subprograma: __________________________________")
    pdf.drawString(right_x, height - 54, "Proyecto: _____________________________________")
    pdf.drawString(right_x, height - 66, "Act. u Obra: __________________________________")
    pdf.drawString(right_x, height - 78, f"Unid. Ejec.: {solicitud.unidad_solicitante.nombre[:26]}")
    pdf.drawString(right_x, height - 90, f"Código Presup: _______________ Código Nº: {solicitud.codigo}")

    # ========================================================
    # 4. TABLA OFICIAL SEGÚN HOJA FÍSICA (Columnas ① ② ③ ④)
    # ========================================================
    headers_1 = ['CODIGO', 'DESCRIPCION', 'Unidad de\nManejo', 'Cantidad', '', 'Partida\nPresupuestaria', 'Costo (Bs.)', '']
    headers_2 = ['', '', '', 'Pedido', 'Entrega', '', 'Unidad', 'Total']

    data = [headers_1, headers_2]
    total_costo_pedido = Decimal('0.00')

    for d in solicitud.detalles.all():
        if d.es_nueva_adquisicion:
            desc = d.descripcion_material_no_catalogado or "Ítem no catalogado"
            codigo_mat = "N/C"
            unidad_cod = "N/C"
            partida_cod = d.partida.codigo if d.partida else "N/C"
            costo_u = d.precio_unitario_referencial
            costo_total = Decimal(d.cantidad_entregada) * costo_u if d.cantidad_entregada > 0 else Decimal('0.00')
        else:
            desc = d.material.nombre[:42]
            codigo_mat = d.material.codigo
            unidad_cod = d.material.unidad_medida_fk.codigo if d.material.unidad_medida_fk else (d.material.unidad_medida or "PZA")
            partida_cod = d.material.partida.codigo if d.material.partida else "—"

            if solicitud.estado in ['ENTREGADA', 'CERRADA']:
                from inventario.models import NotaSalidaDetalle
                ns_det = NotaSalidaDetalle.objects.filter(
                    nota_salida__solicitud_origen=solicitud,
                    material=d.material
                ).first()
                if ns_det:
                    costo_u = ns_det.costo_unitario_real
                    costo_total = ns_det.costo_total_real
                else:
                    costo_u = d.precio_unitario_referencial
                    costo_total = Decimal(d.cantidad_entregada) * costo_u
            else:
                costo_u = d.precio_unitario_referencial
                costo_total = Decimal(d.cantidad_entregada) * costo_u

        cant_pedida = d.cantidad_solicitada
        cant_entrega = d.cantidad_entregada
        total_costo_pedido += costo_total

        data.append([
            codigo_mat,
            desc,
            str(unidad_cod)[:8],
            str(cant_pedida),
            str(cant_entrega) if cant_entrega > 0 else "—",
            partida_cod,
            f"{costo_u:.2f}",
            f"{costo_total:.2f}" if (costo_total > 0 and cant_entrega > 0) else "—"
        ])

    # Fila de Totales
    data.append([
        'TOTAL GENERAL', '', '', '', '', '', '', f"{total_costo_pedido:.2f}"
    ])

    col_widths = [75, 235, 50, 42, 42, 68, 70, 70]  # Suma = 652 pt
    t = Table(data, colWidths=col_widths)
    last_row = len(data) - 1

    t.setStyle(TableStyle([
        ('SPAN', (0, 0), (0, 1)),  
        ('SPAN', (1, 0), (1, 1)),  
        ('SPAN', (2, 0), (2, 1)),  
        ('SPAN', (3, 0), (4, 0)),  # Cantidad (Pedido / Entrega)
        ('SPAN', (5, 0), (5, 1)),  
        ('SPAN', (6, 0), (7, 0)),  # Costo (Unidad / Total)
        ('SPAN', (0, last_row), (5, last_row)), # Fila TOTAL
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('ALIGN', (1, 2), (1, last_row - 1), 'LEFT'),
        ('ALIGN', (0, last_row), (0, last_row), 'RIGHT'),
        ('ALIGN', (6, 2), (-1, -1), 'RIGHT'),
        ('FONTNAME', (0, 0), (-1, 1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 7),
        ('GRID', (0, 0), (-1, last_row), 0.5, colors.HexColor('#6B7280')),
        ('BACKGROUND', (0, 0), (-1, 1), colors.HexColor('#F3F4F6')),
        ('FONTNAME', (0, last_row), (-1, last_row), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2.5),
        ('TOPPADDING', (0, 0), (-1, -1), 2.5),
    ]))

    w_act, h_act = t.wrapOn(pdf, 652, height - 200)
    pdf_y = height - 105 - h_act
    t.drawOn(pdf, 45, pdf_y)

    # ========================================================
    # 5. LAS 5 FIRMAS OFICIALES ALINEADAS (Idénticas a la foto física)
    # ========================================================
    # Posición dinámica debajo de la tabla
    y_firmas = max(42, pdf_y - 70)
    pdf.setFont("Helvetica", 7)

    # Nombres de responsables si existen
    solicitante_nom = solicitud.solicitante.get_full_name() or solicitud.solicitante.username
    autorizador_nom = solicitud.aprobado_por or (solicitud.revisado_por.get_full_name() if solicitud.revisado_por else "")
    entregador_nom = solicitud.entregado_por.get_full_name() if solicitud.entregado_por else "Almacenero"

    # 1. Pedido Por
    pdf.drawString(45, y_firmas + 25, "_______________________")
    pdf.drawString(45, y_firmas + 14, "Pedido Por:")
    pdf.setFont("Helvetica-Bold", 6.5)
    pdf.drawString(45, y_firmas + 4, solicitante_nom[:20])
    pdf.setFont("Helvetica", 6)
    pdf.drawString(45, y_firmas - 5, "Nombre, Cargo y Firma")

    # 2. V.B. Por
    pdf.setFont("Helvetica", 7)
    pdf.drawString(195, y_firmas + 25, "_______________________")
    pdf.drawString(195, y_firmas + 14, "V.B. Por:")
    pdf.setFont("Helvetica", 6)
    pdf.drawString(195, y_firmas - 5, "Jefe Administrativo")

    # 3. Autorizado Por
    pdf.setFont("Helvetica", 7)
    pdf.drawString(345, y_firmas + 25, "_______________________")
    pdf.drawString(345, y_firmas + 14, "Autorizado Por:")
    pdf.setFont("Helvetica-Bold", 6.5)
    pdf.drawString(345, y_firmas + 4, autorizador_nom[:20])
    pdf.setFont("Helvetica", 6)
    pdf.drawString(345, y_firmas - 5, "Nombre, Cargo y Firma")

    # 4. Entregado Por
    pdf.setFont("Helvetica", 7)
    pdf.drawString(495, y_firmas + 25, "_______________________")
    pdf.drawString(495, y_firmas + 14, "Entregado Por:")
    pdf.setFont("Helvetica-Bold", 6.5)
    pdf.drawString(495, y_firmas + 4, entregador_nom[:20])
    pdf.setFont("Helvetica", 6)
    pdf.drawString(495, y_firmas - 5, "Nombre, Cargo y Firma")

    # 5. Recibido Por
    pdf.setFont("Helvetica", 7)
    pdf.drawString(645, y_firmas + 25, "_______________________")
    pdf.drawString(645, y_firmas + 14, "Recibido Por:")
    pdf.setFont("Helvetica-Bold", 6.5)
    pdf.drawString(645, y_firmas + 4, solicitante_nom[:20])
    pdf.setFont("Helvetica", 6)
    pdf.drawString(645, y_firmas - 5, "Nombre, Cargo y Firma")

    # ========================================================
    # 6. CÓDIGO QR Y FECHA DE SALIDA FÍSICA
    # ========================================================
    if solicitud.estado in ['ENTREGADA', 'CERRADA']:
        qr_url = f"http://10.153.101.3:8000/solicitudes/verificar/{solicitud.codigo}/"
        qr_code = QrCodeWidget(qr_url)
        bounds = qr_code.getBounds()
        w_qr = bounds[2] - bounds[0]
        h_qr = bounds[3] - bounds[1]
        
        d = Drawing(40, 40, transform=[40./w_qr, 0, 0, 40./h_qr, 0, 0])
        d.add(qr_code)
        renderPDF.draw(d, pdf, width - 75, 12)
        
        pdf.setFont("Helvetica-Bold", 6.5)
        pdf.setFillColor(colors.HexColor('#16A34A'))
        pdf.drawString(45, 14, f"CÓDIGO DE VALIDACIÓN: {solicitud.codigo}-2026-SABS-OK • DOCUMENTO OFICIAL DESPACHADO")
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
    Registra pedidos de consumo de oficina validados contra la ASIGNACIÓN FÍSICA DIRECTA
    recibida en las Notas de Entrada de Almacén (Sin dependencia de techos POA).
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

                for item_key, item_data in payload.items():
                    cantidad = int(item_data.get('cantidad', 1))
                    if cantidad <= 0:
                        raise ValueError("La cantidad solicitada debe ser mayor a cero.")

                    material = Material.objects.select_related('partida').get(id=item_key)

                    # 1. Validar existencia física neta en Almacén
                    stock_almacen = InventarioAlmacen.objects.filter(
                        material=material,
                        almacen__in=almacenes_autorizados
                    ).aggregate(
                        disponible=Sum(F('stock_fisico') - F('stock_reservado'))
                    )['disponible'] or 0

                    if stock_almacen < cantidad:
                        raise ValueError(
                            f"Stock insuficiente en almacén para '{material.nombre}'. "
                            f"Disponible en estantería: {stock_almacen} (solicitado: {cantidad})."
                        )

                    # 2. VALIDAR CUPO ASIGNADO DE LA UNIDAD (Consulta directa robusta)
                    asigs = AsignacionEntradaUnidad.objects.filter(
                        unidad_organizacional=unidad_solicitante,
                        nota_ingreso_detalle__material=material,
                        cantidad_asignada__gt=F('cantidad_retirada')
                    )
                    tot_asig = asigs.aggregate(s=Sum('cantidad_asignada'))['s'] or 0
                    tot_ret = asigs.aggregate(s=Sum('cantidad_retirada'))['s'] or 0
                    saldo_cupo = max(0, tot_asig - tot_ret)

                    # Si tiene cupo asignado, no puede pedir más que su cupo
                    if saldo_cupo > 0 and cantidad > saldo_cupo:
                        raise ValueError(
                            f"Cupo asignado insuficiente para '{material.nombre}'. "
                            f"Su oficina tiene {saldo_cupo} unidades asignadas disponibles (solicitó: {cantidad})."
                        )

                    # 3. Costo referencial del lote PEPS activo más antiguo
                    lote_peps = MovimientoInventario.objects.filter(
                        material=material,
                        tipo='ENTRADA',
                        almacen__in=almacenes_autorizados,
                        saldo_disponible_lote__gt=0
                    ).order_by('fecha', 'id').first()

                    costo_referencial = lote_peps.costo_unitario if lote_peps else Decimal('0.00')

                    DetalleSolicitud.objects.create(
                        solicitud=solicitud,
                        material=material,
                        cantidad_solicitada=cantidad,
                        precio_unitario_referencial=costo_referencial
                    )

                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Solicitudes',
                    accion='Registrar Pedido Almacén (Asignación Directa)',
                    descripcion=f'Se registró el Pedido {codigo} para {unidad_solicitante.nombre} validado contra cupos de entrada.'
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
    # GET: CARGAR ARTÍCULOS PRIORIZANDO LA ASIGNACIÓN DE LA UNIDAD
    # =========================================================
    materiales_filtrados = []

    # 1. Asignaciones específicas de la unidad_solicitante
    asignaciones = AsignacionEntradaUnidad.objects.filter(
        unidad_organizacional=unidad_solicitante,
        cantidad_asignada__gt=F('cantidad_retirada'),
        nota_ingreso_detalle__material__is_active=True
    ).select_related(
        'nota_ingreso_detalle__material',
        'nota_ingreso_detalle__material__partida',
        'nota_ingreso_detalle__material__unidad_medida_fk'
    )

    materiales_dict = {}
    for asig in asignaciones:
        mat = asig.nota_ingreso_detalle.material
        saldo_asig = asig.saldo_disponible

        if mat.id not in materiales_dict:
            u_med = mat.unidad_medida_fk.codigo if mat.unidad_medida_fk else mat.unidad_medida
            partida_cod = mat.partida.codigo if mat.partida else "—"
            partida_nom = mat.partida.nombre if mat.partida else ""

            inv = InventarioAlmacen.objects.filter(material=mat, almacen__in=almacenes_autorizados).first()
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
                'almacen_despacho': almacenes_autorizados.first().nombre if almacenes_autorizados.first() else "Almacén Central"
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

    # 2. Si el Administrador está operando, también le mostramos el resto de materiales de almacén (Bolsa Libre)
    if rol in ['ADMINISTRADOR', 'ADMIN_ALMACENES']:
        inventarios_extra = InventarioAlmacen.objects.filter(
            almacen__in=almacenes_autorizados,
            stock_fisico__gt=0,
            material__is_active=True
        ).exclude(
            material_id__in=materiales_dict.keys()
        ).select_related('material', 'material__partida', 'material__unidad_medida_fk', 'almacen')

        for inv in inventarios_extra:
            mat = inv.material
            disp = inv.stock_disponible
            if disp > 0:
                lote = MovimientoInventario.objects.filter(
                    material=mat,
                    tipo='ENTRADA',
                    almacen=inv.almacen,
                    saldo_disponible_lote__gt=0
                ).order_by('fecha', 'id').first()
                costo_u = lote.costo_unitario if lote else Decimal('0.00')

                u_med = mat.unidad_medida_fk.codigo if mat.unidad_medida_fk else mat.unidad_medida
                materiales_filtrados.append({
                    'id': mat.id,
                    'nombre': mat.nombre,
                    'codigo': mat.codigo,
                    'partida_codigo': mat.partida.codigo if mat.partida else "—",
                    'partida_nombre': mat.partida.nombre if mat.partida else "",
                    'unidad_medida': u_med,
                    'stock_almacen': disp,
                    'costo_unitario': float(costo_u),
                    'cupo_asignado': disp,
                    'cuota_saldo': disp,
                    'max_solicitable': disp,
                    'modalidad': 'BOLSA_COMUN',
                    'modalidad_label': f'Bolsa Libre Almacén ({disp} {u_med})',
                    'almacen_despacho': inv.almacen.nombre
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