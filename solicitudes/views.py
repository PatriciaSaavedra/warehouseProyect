import json
import html
from decimal import Decimal, InvalidOperation
from django.utils import timezone 
from django.http import JsonResponse, HttpResponse, HttpResponseForbidden # <-- CORREGIDO: Se eliminó el import de 'request'
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.db import transaction, DatabaseError
from django.contrib import messages
from django.db.models import Q
from django.db import transaction
# Importaciones de ReportLab para el PDF oficial
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter, landscape
from reportlab.platypus import Table, TableStyle
from reportlab.lib import colors
from django.core.paginator import Paginator


# Importaciones para el dibujo del código QR nativo de ReportLab [28]
from reportlab.graphics.shapes import Drawing
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget

from .models import Solicitud, DetalleSolicitud, ESTADOS_SOLICITUD 
from inventario.models import Material, MovimientoInventario, PartidaPresupuestaria, UnidadMedida
from inventario.services import registrar_salida_valorada_peps  # Importamos nuestro servicio PEPS (FIFO)
from usuarios.decorators import tiene_rol, rol_requerido
from presupuestos.models import POA
from auditoria.models import Bitacora

# Gestión fiscal actual de la Gobernación de Potosí
GESTION_ACTUAL = 2026


# ========================================================
# FUNCIÓN AUXILIAR DE REDIRECCIÓN INTELIGENTE (UX) [28]
# ========================================================
def redirigir_despues_de_accion(request, solicitud):
    """
    Determina de forma dinámica adónde redirigir al usuario para no perder su contexto (UX) [28].
    Garantiza que los revisores se mantengan en la Bandeja de Gestión y los solicitantes en sus pedidos [28].
    """
    perfil = getattr(request.user, 'perfilusuario', None)
    rol = perfil.rol if perfil else 'UNIDAD_SOLICITANTE'
    
    # Definimos el destino de retorno según el rol del usuario que interactúa [28]
    if rol == 'UNIDAD_SOLICITANTE':
        destino_default = 'solicitudes'          # Pestaña personal: Mis Solicitudes
    else:
        destino_default = 'solicitudes_general'  # Pestaña administrativa: Bandeja de Gestión [28]

    referer = request.META.get('HTTP_REFERER', '')
    
    # Si viene desde el detalle, o de los formularios de revisión/rechazo, lo mantiene en el detalle del folio [28]
    if 'detalle' in referer or 'revisar' in referer or 'rechazar' in referer:
        return redirect('detalle_solicitud', id=solicitud.id)
        
    # Si opera desde la tabla, lo mantiene en la misma vista (respetando sus filtros y paginación) [28]
    return redirect(referer if referer else destino_default)

# ========================================================
# VISTAS OPERATIVAS DEL MÓDULO DE SOLICITUDES
# ========================================================

@login_required
def solicitudes(request):
    """
    Pestaña Personal: Muestra estrictamente las solicitudes creadas por el usuario autenticado [11, 28].
    """
    perfil = getattr(request.user, 'perfilusuario', None)
    if not perfil:
        return render(request, 'solicitudes/index_propias.html', {'solicitudes': []})

    # Filtramos estrictamente por el usuario creador
    solicitudes_query = Solicitud.objects.filter(solicitante=request.user)

    # Buscador y rango de fechas básico para uso personal
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
@rol_requerido(['JEFE_INMEDIATO', 'SECRETARIO_SAF', 'PRESUPUESTOS', 'RPA', 'JEFE_ADMINISTRATIVO', 'ALMACENERO', 'KARDISTA', 'ADMINISTRADOR'])
def solicitudes_general(request):
    """
    Pestaña General / Bandeja de Gestión: Muestra y filtra todos los folios bajo norma SABS [28].
    """
    perfil = request.user.perfilusuario
    rol = perfil.rol
    unidad = perfil.unidad

    # Consulta base según el rol del revisor [28]
    if rol == 'ADMINISTRADOR' or rol in ['ALMACENERO', 'KARDISTA']:
        solicitudes_query = Solicitud.objects.all()
    else:
        # Los jefes de unidad solo auditan las solicitudes pertenecientes a su oficina [11]
        solicitudes_query = Solicitud.objects.filter(unidad_solicitante=unidad)

    # Capturar parámetros de filtros avanzados
    query = request.GET.get('q', '').strip()
    filtro_estado = request.GET.get('estado', '').strip()
    filtro_flujo = request.GET.get('flujo', '').strip()  # <-- NUEVO FILTRO PARA TARJETA 4
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
    if filtro_flujo:  # <-- NUEVO FILTRO APLICADO
        solicitudes_query = solicitudes_query.filter(flujo_atencion=filtro_flujo)
    if desde_str:
        solicitudes_query = solicitudes_query.filter(fecha__gte=desde_str)
    if hasta_str:
        solicitudes_query = solicitudes_query.filter(fecha__lte=hasta_str)

    solicitudes_query = solicitudes_query.select_related('solicitante', 'unidad_solicitante').order_by('-id')

    # KPIs superiores del Almacén [28]
    hoy = timezone.now().date()
    primer_dia_mes = hoy.replace(day=1)
    
    pendientes_count = Solicitud.objects.filter(estado='REGISTRADA').count()
    preparacion_count = Solicitud.objects.filter(estado__in=['APROBADA', 'PREPARADA']).count()
    entregas_hoy_count = Solicitud.objects.filter(estado='ENTREGADA', fecha_entrega__date=hoy).count()
    total_folios_count = Solicitud.objects.filter(fecha_registro__date__gte=primer_dia_mes).count()

    paginator = Paginator(solicitudes_query, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # Importamos las tuplas oficiales de flujo para pasarlas al template
    from .models import FLUJOS_ATENCION

    return render(request, 'solicitudes/index_general.html', {
        'solicitudes': page_obj, 
        'rol': rol,
        'kpi_pendientes': pendientes_count,
        'kpi_preparacion': preparacion_count,
        'kpi_entregas_hoy': entregas_hoy_count,
        'kpi_total_folios': total_folios_count,
        'query': query,
        'filtro_estado': filtro_estado,
        'filtro_flujo': filtro_flujo,  # <-- PASAR A LA PLANTILLA
        'desde': desde_str,
        'hasta': hasta_str,
        'estados': ESTADOS_SOLICITUD,
        'flujos': FLUJOS_ATENCION  # <-- PASAR A LA PLANTILLA
    })
@login_required
def buscar_materiales(request):
    """
    Busca materiales en tiempo real. Admite el parámetro opcional 'solo_con_stock'
    para filtrar únicamente materiales disponibles en estanterías.
    """
    q = request.GET.get('q', '').strip()
    solo_con_stock = request.GET.get('solo_con_stock', 'false').lower() == 'true'
    
    if not q:
        return JsonResponse([], safe=False)

    query_base = Material.objects.filter(
        Q(nombre__icontains=q) |
        Q(codigo__icontains=q) |
        Q(partida__codigo__icontains=q)
    )

    # Si se pide desde Almacén, solo mostramos materiales con existencias
    if solo_con_stock:
        query_base = query_base.filter(stock_actual__gt=0)

    materiales = query_base.select_related('partida')[:10]

    data = [
        {
            'id': m.id,
            'nombre': f"{m.codigo} - {m.nombre}",
            'stock': m.stock_actual
        }
        for m in materiales
    ]
    return JsonResponse(data, safe=False)

@login_required
def nueva_solicitud(request):
    """
    Registra solicitudes soportando materiales existentes y nuevas adquisiciones no catalogadas,
    almacenando el tipo de requerimiento y los precios referenciales unitarios.
    """
    if request.method == 'POST':
        fecha = request.POST.get('fecha')
        payload_raw = request.POST.get("payload")
        
        # Tarjeta 1: Obtener y validar tipo de requerimiento
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

                # Tarjeta 1: Guardar tipo de requerimiento
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
                    
                    # Tarjeta 2: Obtener y validar el precio referencial (>= 0)
                    precio_ref_raw = item_data.get('precio_referencial', 0)
                    try:
                        precio_ref = Decimal(str(precio_ref_raw))
                    except (ValueError, TypeError, InvalidOperation):
                        precio_ref = Decimal('0.00')

                    if cantidad <= 0:
                        raise ValueError("La cantidad solicitada debe ser mayor a cero.")
                        
                    if precio_ref < 0:
                        raise ValueError("El precio unitario referencial debe ser mayor o igual a 0.")

                    # Tarjeta 2: Registrar el precio referencial proporcionado por la Unidad Solicitante
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
    return render(request, 'solicitudes/nueva.html', {
        'materiales': materiales
    })
@login_required
def nueva_solicitud_compra(request):
    """
    Registra solicitudes de adquisición de nuevos bienes (sin stock o no catalogados) 
    o de contratación de servicios.
    """
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
                    
                    # Tarjeta 8: Recuperar partida asignada para ítems no catalogados o servicios
                    partida_id = item_data.get('partida_id')
                    partida_obj = None
                    if partida_id:
                        partida_obj = PartidaPresupuestaria.objects.get(id=partida_id)

                    try:
                        precio_ref = Decimal(str(precio_ref_raw))
                    except (ValueError, TypeError, InvalidOperation):
                        precio_ref = Decimal('0.00')

                    if cantidad <= 0:
                        raise ValueError("La cantidad solicitada debe ser mayor a cero.")
                    if precio_ref < 0:
                        raise ValueError("El precio unitario referencial debe ser mayor o igual a 0.")

                    if es_nuevo:
                        # Se registra asignándole la partida presupuestaria de adquisición/servicio
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
                            partida=None, # Obtiene la partida del material
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
    # Tarjeta 8: Pasar las partidas presupuestarias existentes al contexto del formulario de compras
    partidas = PartidaPresupuestaria.objects.all().order_by('codigo')
    return render(request, 'solicitudes/nueva_solicitud_compra.html', {
        'materiales': materiales,
        'partidas': partidas
    })


@login_required
@rol_requerido(['ALMACENERO', 'ADMINISTRADOR'])
def catalogar_item_pendiente(request, detalle_id):
    """
    Permite al Almacenero codificar oficialmente un material no catalogado solicitado (Inciso b) [28].
    """
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

    return render(
        request, 
        'inventario/catalogar_pendiente.html', 
        {
            'detalle': detalle,
            'partidas': partidas,
            'unidades': unidades
        }
    )


@login_required
def detalle_solicitud(request, id):
    """
    Controla el acceso al detalle de la solicitud basándose en el rol del usuario [11, 28].
    Garantiza que el Almacenero y los revisores SABS tengan acceso global para operar [28].
    """
    solicitud = get_object_or_404(Solicitud, id=id)
    perfil = request.user.perfilusuario
    rol = perfil.rol

    # 1. Definimos los roles que requieren acceso global al detalle para cumplir sus funciones SABS [28]
    roles_globales = [
        'ADMINISTRADOR', 
        'ALMACENERO', 
        'KARDISTA', 
        'SECRETARIO_SAF', 
        'PRESUPUESTOS', 
        'RPA', 
        'JEFE_ADMINISTRATIVO'
    ]

    if rol not in roles_globales:
        if rol == 'JEFE_INMEDIATO':
            # Los jefes de unidad solo auditan las solicitudes de su propia oficina [11]
            if solicitud.unidad_solicitante != perfil.unidad:
                return HttpResponseForbidden("No tiene autorización para ver solicitudes de otras unidades.")
        else:
            # Los funcionarios comunes solo pueden ver los requerimientos que ellos crearon [11]
            if solicitud.solicitante != request.user:
                return HttpResponseForbidden("No tiene autorización para ver esta solicitud.")

    return render(request, 'solicitudes/detalle.html', {
        'solicitud': solicitud,
        'rol': rol
    })
@transaction.atomic
@login_required
def revisar_solicitud(request, id):
    """
    Paso 2: El Jefe Inmediato revisa y autoriza las cantidades (Pasa de 'REGISTRADA' a 'REVISADA') [11].
    """
    if not tiene_rol(request.user, ['JEFE_INMEDIATO', 'ADMINISTRADOR']):
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
                for detalle in detalles:
                    aprobada = int(request.POST.get(f"aprobado_{detalle.id}") or 0)
                    if aprobada < 0:
                        aprobada = 0

                    detalle.cantidad_aprobada = aprobada
                    detalle.save()

                solicitud.estado = 'REVISADA'
                solicitud.aprobado_por = request.user.get_full_name() or request.user.username
                solicitud.revisado_por = request.user            
                solicitud.fecha_revision = timezone.now()         
                solicitud.save()

                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Solicitudes',
                    accion='Revisar Solicitud',
                    descripcion=f'El jefe inmediato revisó y autorizó cantidades para la solicitud {solicitud.codigo}'
                )

            messages.success(request, f"Solicitud {solicitud.codigo} revisada y autorizada correctamente.")
            return redirigir_despues_de_accion(request, solicitud)

        except DatabaseError:
            messages.error(request, "Error al procesar la revisión de la solicitud.")
            return redirigir_despues_de_accion(request, solicitud)

    return render(request, 'solicitudes/aprobar.html', {
        'solicitud': solicitud,
        'detalles': detalles
    })


@login_required
@rol_requerido(['SECRETARIO_SAF', 'ADMINISTRADOR'])
def validar_saf(request, id):
    """
    Paso 3: El Secretario de la SAF valida la solicitud (Estado: VALIDADA_SAF) [28].
    """
    solicitud = get_object_or_404(Solicitud.objects.select_for_update(), id=id)

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
    """
    Paso 4 (Tarjeta 8 y 9): Validación presupuestaria de adquisiciones y servicios.
    Controla el saldo disponible en el POA y reserva (compromete) el presupuesto estimado.
    """
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

            # Verificar saldo disponible y ejecutar la reserva en el POA
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

                # Tarjeta 9: Comprometer (reservar) presupuesto automáticamente
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

    # Tarjeta 3: No intervenir en stock
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

    # Tarjeta 3: No intervenir en stock
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
@rol_requerido(['ALMACENERO', 'ADMINISTRADOR'])
def preparar_solicitud(request, id):
    solicitud = get_object_or_404(Solicitud, id=id)

    # Tarjeta 3: Permitir preparación inmediata para bienes con stock
    if solicitud.flujo_atencion == 'SALIDA_ALMACEN':
        permitido = (solicitud.estado == 'REVISADA')
        mensaje_error = "Para solicitudes con stock, se requiere primero la autorización del Jefe de Unidad (Estado: Revisada)."
    else:
        # Requerimientos sin stock o servicios requieren validación hasta la Jefatura
        permitido = (solicitud.estado == 'VALIDADA_JEFATURA')
        mensaje_error = "Solo solicitudes validadas administrativamente por Jefatura pueden prepararse."

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
        descripcion=f'El almacenero preparó físicamente los materiales de la solicitud {solicitud.codigo}'
    )

    messages.success(request, f"La solicitud {solicitud.codigo} ha sido marcada como PREPARADA para su despacho.")
    return redirigir_despues_de_accion(request, solicitud)

@login_required
def entregar_solicitud(request, id):
    """
    Paso 8 (Tarjeta 9 y 21): Entrega física de materiales. 
    Descuenta stock por PEPS, libera la reserva del POA y consolida la ejecución real.
    """
    if not tiene_rol(request.user, ['ALMACENERO', 'ADMINISTRADOR']):
        return HttpResponseForbidden("No autorizado.")

    solicitud = get_object_or_404(Solicitud, id=id)

    if solicitud.flujo_atencion == 'CONTRATACION_SERVICIO':
        messages.error(request, "Error: Un requerimiento de tipo SERVICIO no puede ser procesado para entrega física de inventario.")
        return redirigir_despues_de_accion(request, solicitud)

    if solicitud.estado != 'PREPARADA':
        messages.error(request, "Solo solicitudes en estado PREPARADA pueden entregarse físicamente.")
        return redirigir_despues_de_accion(request, solicitud)

    detalles = solicitud.detalles.select_related('material__partida')

    try:
        with transaction.atomic():
            solicitud = Solicitud.objects.select_for_update().get(id=id)
            costos_partidas = {}

            # 1. Descontar stock usando PEPS y agrupar costos reales de salida
            for detalle in detalles:
                material = Material.objects.get(id=detalle.material.id)
                cantidad_despacho = detalle.cantidad_aprobada if detalle.cantidad_aprobada is not None else detalle.cantidad_solicitada

                if material.stock_actual < cantidad_despacho:
                    messages.error(request, f"Inconsistencia: Stock insuficiente en {material.nombre} para despachar la solicitud.")
                    return redirigir_despues_de_accion(request, solicitud)

                # Despacho PEPS
                mov = registrar_salida_valorada_peps(
                    material=material,
                    cantidad_salida=cantidad_despacho,
                    tipo_movimiento='SALIDA',
                    referencia=f"DESPACHO: {solicitud.codigo}",
                    usuario=request.user,
                    unidad_destino=solicitud.unidad_solicitante
                )

                detalle.cantidad_entregada = cantidad_despacho
                detalle.save()

                partida = material.partida
                costos_partidas[partida] = costos_partidas.get(partida, Decimal('0.00')) + mov.costo_total

            # 2. Tarjeta 9: Ejecutar presupuesto real y liberar compromisos
            for partida, costo_real in costos_partidas.items():
                poa = POA.objects.select_for_update().get(
                    unidad=solicitud.unidad_solicitante,
                    partida=partida,
                    gestion=GESTION_ACTUAL
                )
                
                if solicitud.flujo_atencion == 'ADQUISICION':
                    # Sumamos el costo referencial originalmente reservado para esta partida
                    costo_estimado_partida = sum(
                        d.subtotal_referencial for d in detalles if d.partida_afectada == partida
                    )
                    
                    # - Liberamos la reserva provisional comprometida
                    poa.monto_comprometido -= costo_estimado_partida
                    # - Registramos la ejecución real de compra/despacho
                    poa.monto_ejecutado += costo_real
                    # - Ajustamos la diferencia (ahorro o sobreprecio) en el saldo disponible
                    poa.monto_disponible += (costo_estimado_partida - costo_real)
                else:
                    # Flujo directo de almacén (No requirió aprobación presupuestaria previa)
                    poa.monto_disponible -= costo_real
                    poa.monto_ejecutado += costo_real
                
                poa.save()

            solicitud.estado = 'ENTREGADA'
            solicitud.entregado_por = request.user
            solicitud.fecha_entrega = timezone.now()
            solicitud.save()

            Bitacora.objects.create(
                usuario=request.user,
                modulo='Presupuestos',
                accion='Ejecución Presupuestaria Consolidada',
                descripcion=f'Se consolidó el gasto real de la solicitud {solicitud.codigo} en el POA de la unidad.'
            )

        messages.success(request, f"Entrega física procesada y ejecución presupuestaria consolidada de forma correcta.")
        return redirigir_despues_de_accion(request, solicitud)

    except Exception as e:
        messages.error(request, f"Error al procesar el despacho PEPS/POA: {str(e)}")
        return redirigir_despues_de_accion(request, solicitud)

@login_required
def cerrar_solicitud(request, id):
    """
    Paso 9: Concluye administrativamente la carpeta de solicitud (Pasa de 'ENTREGADA' a 'CERRADA').
    """
    if not tiene_rol(request.user, ['ALMACENERO', 'ADMINISTRADOR']):
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


# --- EN TU ARCHIVO views.py ---

@login_required
def editar_solicitud(request, id):
    """
    Permite modificar una solicitud existente únicamente si se encuentra en 'REGISTRADA',
    actualizando tipo de requerimiento, materiales y precios unitarios referenciales.
    """
    solicitud = get_object_or_404(Solicitud, id=id)
    perfil = request.user.perfilusuario

    if perfil.rol != 'ADMINISTRADOR':
        if solicitud.unidad_solicitante != perfil.unidad:
            return HttpResponseForbidden("No tiene permisos para modificar solicitudes de otra unidad.")

    if solicitud.estado != 'REGISTRADA':
        return HttpResponseForbidden("Esta solicitud ya se encuentra en proceso de revisión y no es editable.")

    if request.method == "GET":
        detalles = solicitud.detalles.select_related('material')

        # Tarjeta 2: Exportar el precio referencial para que se muestre en el formulario frontend
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
        
        # Tarjeta 1: Recibir tipo de requerimiento
        tipo_requerimiento = data.get("tipo_requerimiento", "BIEN")

        if not tipo_requerimiento or tipo_requerimiento not in ['BIEN', 'SERVICIO']:
            return JsonResponse({
                "ok": False,
                "error": "Debe seleccionar un tipo de requerimiento válido (Bien o Servicio)."
            }, status=400)

        if not payload:
            return JsonResponse({
                "ok": False,
                "error": "Debe agregar al menos un material a la solicitud"
            }, status=400)

        with transaction.atomic():
            solicitud.justificacion = justificacion
            solicitud.tipo_requerimiento = tipo_requerimiento
            solicitud.save()

            solicitud.detalles.all().delete()

            for material_id, item_data in payload.items():
                # Soportamos tanto el formato estructurado de diccionario como valores planos
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
                    return JsonResponse({
                        "ok": False,
                        "error": "La cantidad de los ítems debe ser mayor a cero."
                    }, status=400)

                if precio_ref < 0:
                    return JsonResponse({
                        "ok": False,
                        "error": "El precio referencial no puede ser negativo."
                    }, status=400)

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
        return JsonResponse({
            "ok": False,
            "error": "Formato de datos JSON inválido"
        }, status=400)

@login_required
def rechazar_solicitud(request, id):
    """
    Permite a cualquier rol revisor de la cadena SABS rechazar y archivar el requerimiento.
    Tarjeta 9: Devuelve los recursos comprometidos al saldo disponible del POA si el trámite se anula.
    """
    roles_revisores = [
        'JEFE_INMEDIATO', 
        'SECRETARIO_SAF', 
        'PRESUPUESTOS', 
        'RPA', 
        'JEFE_ADMINISTRATIVO', 
        'ADMINISTRADOR'
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
                
                # Tarjeta 9: Si la solicitud ya contaba con reserva presupuestaria, liberamos los recursos
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
                                # Devolvemos de comprometido a disponible
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
    if not tiene_rol(request.user, ['JEFE_INMEDIATO', 'ADMINISTRADOR']):
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
    Genera el reporte PDF del "Pedido de Materiales y/o Bienes" oficial de la Gobernación (Pág. 9) [28].
    """
    solicitud = get_object_or_404(
        Solicitud.objects.prefetch_related('detalles__material__partida'),
        id=id
    )

    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="pedido_material_{solicitud.codigo}.pdf"'

    pdf = canvas.Canvas(response, pagesize=landscape(letter))
    width, height = landscape(letter)
    
    pdf.setTitle(f"Pedido de Material {solicitud.codigo}")  # <-- ESTA LÍNEA NOMBRA TU PESTAÑA AUTOMÁTICAMENTE [28]
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

    # =========================================================================
    # --- SISTEMA DE VALIDACIÓN QR DINÁMICO (Autenticidad Documental SABS) [28] ---
    # =========================================================================
    from reportlab.graphics.barcode import qr
    from reportlab.graphics.shapes import Drawing
    from reportlab.graphics import renderPDF

    # Si la solicitud completó la entrega física o se cerró, activamos el QR y el Hash [11, 28]
    if solicitud.estado in ['ENTREGADA', 'CERRADA']:
        # APUNTA EL QR A LA RUTA PÚBLICA (Usa tu IP local para la prueba) [2]
        qr_url = f"http://10.153.101.3:8000/solicitudes/verificar/{solicitud.codigo}/"
        qr_code = qr.QrCodeWidget(qr_url)
        
        # Calculamos dimensiones del gráfico en ReportLab
        bounds = qr_code.getBounds()
        width_qr = bounds[2] - bounds[0]
        height_qr = bounds[3] - bounds[1]
        
        # Creamos un bloque de dibujo de 55x55 puntos para colocar en la esquina inferior derecha
        d = Drawing(55, 55, transform=[55./width_qr, 0, 0, 55./height_qr, 0, 0])
        d.add(qr_code)
        
        # Pintamos el QR en las coordenadas de la esquina derecha (x=700, y=10)
        renderPDF.draw(d, pdf, 710, 15)
        
        # Pintamos el código Hash de Verificación al lado del QR
        pdf.setFont("Helvetica-Bold", 6.5)
        pdf.setFillColor(colors.HexColor('#16A34A')) # Verde éxito
        pdf.drawString(540, 15, f"CÓDIGO DE VALIDACIÓN: {solicitud.codigo}-2026-SABS-OK")
        pdf.setFillColor(colors.black)
    else:
        # Si está en trámite, no hay QR y mostramos una advertencia en rojo
        pdf.setFont("Helvetica-Bold", 8)
        pdf.setFillColor(colors.HexColor('#DC2626')) # Rojo advertencia
        pdf.drawString(540, 15, "DOCUMENTO EN TRÁMITE - SIN VALOR OFICIAL")
        pdf.setFillColor(colors.black)

    # Guardar cambios y cerrar PDF
    pdf.save()
    return response

@login_required
@rol_requerido(['SECRETARIO_SAF', 'ADMINISTRADOR'])
def validar_saf(request, id):
    solicitud = get_object_or_404(Solicitud.objects.select_for_update(), id=id)

    # Tarjeta 3 y Tarjeta 7: No intervenir en solicitudes de bienes con stock
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

    return redirect(request.META.get('HTTP_REFERER', 'solicitudes'))

@login_required
@rol_requerido(['RPA', 'ADMINISTRADOR'])
def validar_rpa(request, id):
    """
    Paso 4: El Responsable del Proceso de Contratación (RPA) aprueba (Estado: VALIDADA_RPA) [28].
    """
    solicitud = get_object_or_404(Solicitud.objects.select_for_update(), id=id)

    if solicitud.estado != 'VALIDADA_PRESUPUESTOS':
        messages.error(request, "Esta solicitud aún no cuenta con la aprobación presupuestaria.")
        return redirect(request.META.get('HTTP_REFERER', 'solicitudes'))

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

    return redirect(request.META.get('HTTP_REFERER', 'solicitudes'))


@login_required
@rol_requerido(['JEFE_ADMINISTRATIVO', 'ADMINISTRADOR'])
def validar_jefatura(request, id):
    """
    Paso 5: El Jefe Administrativo aprueba (Estado: VALIDADA_JEFATURA) [28].
    """
    solicitud = get_object_or_404(Solicitud.objects.select_for_update(), id=id)

    if solicitud.estado != 'VALIDADA_RPA':
        messages.error(request, "Esta solicitud aún no cuenta con la aprobación del RPA.")
        return redirect(request.META.get('HTTP_REFERER', 'solicitudes'))

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

    return redirect(request.META.get('HTTP_REFERER', 'solicitudes'))
@transaction.atomic
@login_required
@rol_requerido(['ADMINISTRADOR'])
def retroceder_estado_solicitud(request, id):
    """
    Permite únicamente al Administrador revertir de forma segura el estado de una solicitud 
    al paso inmediato anterior limpiando las firmas y marcas de tiempo del paso anulado [28].
    """
    solicitud = get_object_or_404(Solicitud.objects.select_for_update(), id=id)
    estado_actual = solicitud.estado

    # Mapa de retroceso secuencial: (Estado_Anterior, Atributo_Usuario, Atributo_Fecha)
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
            # 1. Retroceder el estado del flujo
            solicitud.estado = nuevo_estado
            
            # 2. Limpiar las firmas físicas/digitales registradas en el paso anulado
            setattr(solicitud, campo_usuario, None)
            setattr(solicitud, campo_fecha, None)
            solicitud.save()

            # 3. Registrar auditoría en la Bitácora
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
    Registra solicitudes de materiales que se encuentran catalogados y con stock.
    Sigue el Flujo Rápido de Almacén (SALIDA_ALMACEN) y no requiere cotización ni POA.
    """
    if request.method == 'POST':
        fecha = request.POST.get('fecha')
        payload_raw = request.POST.get("payload")

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
            messages.error(request, "Debe agregar al menos un material.")
            return redirect('nuevo_pedido_almacen')

        justificacion = request.POST.get('justificacion', '').strip()
        perfil = getattr(request.user, 'perfilusuario', None)
        unidad_solicitante = perfil.unidad if perfil else None

        if not unidad_solicitante:
            messages.error(request, "Su usuario no tiene asignada una Unidad Organizacional.")
            return redirect('nuevo_pedido_almacen')

        try:
            with transaction.atomic():
                ultima = Solicitud.objects.select_for_update().order_by('id').last()
                numero = (ultima.id + 1) if ultima else 1
                codigo = f'PED-{numero:05d}' # Prefijo distintivo para Pedido de Stock

                solicitud = Solicitud.objects.create(
                    codigo=codigo,
                    unidad_solicitante=unidad_solicitante,
                    solicitante=request.user,
                    fecha=fecha,
                    justificacion=justificacion,
                    tipo_requerimiento='BIEN',
                    flujo_atencion='SALIDA_ALMACEN', # Forzado directamente a salida de stock
                    estado='REGISTRADA'
                )

                for item_key, item_data in payload.items():
                    cantidad = int(item_data.get('cantidad', 1))

                    if cantidad <= 0:
                        raise ValueError("La cantidad solicitada debe ser mayor a cero.")

                    material = Material.objects.get(id=item_key)
                    
                    # Validación estricta de stock en el backend
                    if material.stock_actual < cantidad:
                        raise ValueError(f"No existe stock suficiente para el material: {material.nombre} (Disponible: {material.stock_actual}).")

                    DetalleSolicitud.objects.create(
                        solicitud=solicitud,
                        material=material,
                        cantidad_solicitada=cantidad,
                        precio_unitario_referencial=Decimal('0.00') # No requiere cotización previa
                    )

                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Solicitudes',
                    accion='Registrar Pedido Almacén',
                    descripcion=f'Se registró el Pedido de Consumo {codigo} para la unidad {unidad_solicitante.nombre}'
                )

            messages.success(request, f"Pedido de Almacén {codigo} registrado correctamente.")
            return redirect('solicitudes')

        except ValueError as e:
            messages.error(request, str(e))
            return redirect('nuevo_pedido_almacen')
        except DatabaseError:
            messages.error(request, "Hubo un error al procesar el guardado en la base de datos.")
            return redirect('nuevo_pedido_almacen')

    # Al ser consumo de stock, solo cargamos los materiales que tienen stock real > 0
    materiales = Material.objects.filter(stock_actual__gt=0).values('id', 'nombre', 'stock_actual')
    return render(request, 'solicitudes/nuevo_pedido_almacen.html', {
        'materiales': materiales
    })

def redirigir_despues_de_accion(request, solicitud):
    """
    Determina de forma dinámica adónde redirigir al usuario para no perder su contexto (UX) [28].
    Garantiza que si opera dentro de un folio, permanezca dentro de ese folio [28].
    """
    referer = request.META.get('HTTP_REFERER', '')
    
    # Si viene desde el detalle, o de los formularios de revisión/rechazo, lo mantiene en el detalle del folio [28]
    if 'detalle' in referer or 'revisar' in referer or 'rechazar' in referer:
        return redirect('detalle_solicitud', id=solicitud.id)
        
    # Si opera desde el listado principal, lo mantiene en la misma página del listado [28]
    return redirect(referer if referer else 'solicitudes')

def verificar_documento_publico(request, codigo):
    """
    Vista pública sin autenticación para verificar la validez de un documento impreso 
    escaneando el código QR (Garantiza autenticidad sin firma digital con token) [28].
    """
    solicitud = get_object_or_404(
        Solicitud.objects.prefetch_related('detalles__material'),
        codigo=codigo
    )

    # Control de seguridad: solo es verificable públicamente si ya concluyó el trámite [28]
    if solicitud.estado not in ['ENTREGADA', 'CERRADA']:
        return HttpResponseForbidden("Este documento se encuentra en trámite y no cuenta con certificación de verificación pública aún.")

    return render(
        request, 
        'solicitudes/verificar_publico.html', 
        {
            'solicitud': solicitud
        }
    )