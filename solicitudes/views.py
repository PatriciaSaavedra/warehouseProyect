import json
import html
from django.utils import timezone 
from decimal import Decimal
from django.http import JsonResponse, HttpResponse, HttpResponseForbidden, request
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.db import transaction, DatabaseError
from django.contrib import messages
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter, landscape
from reportlab.platypus import Table, TableStyle
from reportlab.lib import colors
from .models import Solicitud, DetalleSolicitud, ESTADOS_SOLICITUD 
from inventario.models import Material, MovimientoInventario
from inventario.models import PartidaPresupuestaria, UnidadMedida
from inventario.services import registrar_salida_valorada_peps  # Importamos nuestro servicio PEPS (FIFO)
from usuarios.decorators import tiene_rol, rol_requerido
from presupuestos.models import POA
from auditoria.models import Bitacora
from django.db.models import Q
GESTION_ACTUAL = 2026

@login_required
def solicitudes(request):
    """
    Listado principal de solicitudes con soporte de búsqueda y filtros avanzados (RE-SABS) [11, 28].
    """
    perfil = getattr(request.user, 'perfilusuario', None)

    if not perfil:
        return render(request, 'solicitudes/index.html', {'solicitudes': []})

    rol = perfil.rol
    unidad = perfil.unidad

    # 1. Consulta base según el rol del usuario (Seguridad RE-SABS)
    if rol == 'ADMINISTRADOR':
        solicitudes_query = Solicitud.objects.all()
    elif rol in ['JEFE_INMEDIATO', 'PRESUPUESTOS']:
        solicitudes_query = Solicitud.objects.filter(unidad_solicitante=unidad)
    else:
        solicitudes_query = Solicitud.objects.filter(solicitante=request.user)

    # 2. Capturar parámetros de filtrado desde GET [28]
    query = request.GET.get('q', '').strip()
    filtro_estado = request.GET.get('estado', '').strip()
    desde_str = request.GET.get('desde', '').strip()
    hasta_str = request.GET.get('hasta', '').strip()

    # Filtro A: Búsqueda por texto (Folio, Solicitante, Unidad o Justificación) [28]
    if query:
        solicitudes_query = solicitudes_query.filter(
            Q(codigo__icontains=query) |
            Q(solicitante__username__icontains=query) |
            Q(solicitante__first_name__icontains=query) |
            Q(solicitante__last_name__icontains=query) |
            Q(unidad_solicitante__nombre__icontains=query) |
            Q(justificacion__icontains=query)
        )

    # Filtro B: Por Estado del Flujo [28]
    if filtro_estado:
        solicitudes_query = solicitudes_query.filter(estado=filtro_estado)

    # Filtro C: Rango de Fechas [28]
    if desde_str:
        solicitudes_query = solicitudes_query.filter(fecha__gte=desde_str)
    if hasta_str:
        solicitudes_query = solicitudes_query.filter(fecha__lte=hasta_str)

    # Ordenar por el más reciente
    solicitudes_query = solicitudes_query.select_related('solicitante', 'unidad_solicitante').order_by('-id')

    # 3. Cálculo de metricas superiores (KPIs) de la gestión actual
    hoy = timezone.now().date()
    primer_dia_mes = hoy.replace(day=1)
    
    pendientes_count = Solicitud.objects.filter(estado='REGISTRADA').count()
    preparacion_count = Solicitud.objects.filter(estado__in=['APROBADA', 'PREPARADA']).count()
    
    # CORRECCIÓN 1: Contar solicitudes cuya entrega física real (fecha_entrega) haya sido hoy [28]
    entregas_hoy_count = Solicitud.objects.filter(estado='ENTREGADA', fecha_entrega__date=hoy).count()
    
    # CORRECCIÓN 2: Contar folios registrados automáticamente en el servidor durante este mes [28]
    total_folios_count = Solicitud.objects.filter(fecha_registro__date__gte=primer_dia_mes).count()

    return render(request, 'solicitudes/index.html', {
        'solicitudes': solicitudes_query,
        'rol': rol,
        'kpi_pendientes': pendientes_count,
        'kpi_preparacion': preparacion_count,
        'kpi_entregas_hoy': entregas_hoy_count,
        'kpi_total_folios': total_folios_count,
        # Mantener el estado de los campos de filtro en el HTML
        'query': query,
        'filtro_estado': filtro_estado,
        'desde': desde_str,
        'hasta': hasta_str,
        'estados': ESTADOS_SOLICITUD
    })

@login_required
@login_required
def buscar_materiales(request):
    """
    Busca materiales en tiempo real por Nombre, Código de Material o Código de Partida (RE-SABS) [28].
    """
    q = request.GET.get('q', '').strip()
    
    if not q:
        return JsonResponse([], safe=False)

    # Buscaremos coincidencias usando operadores OR ( | ) de Django [28]
    materiales = Material.objects.filter(
        Q(nombre__icontains=q) |
        Q(codigo__icontains=q) |
        Q(partida__codigo__icontains=q)
    ).select_related('partida')[:10]  # Optimizamos con select_related para traer la partida rápido

    # Pre-formateamos el nombre en el JSON para que el usuario vea el código al buscar (Ej: 32100-0001 - Papel Bond) [28]
    data = [
        {
            'id': m.id,
            'nombre': f"{m.codigo} - {m.nombre}",  # Mostramos Código + Nombre en el buscador [28]
            'stock': m.stock_actual
        }
        for m in materiales
    ]
    return JsonResponse(data, safe=False)


@login_required
def nueva_solicitud(request):
    """
    Registra solicitudes soportando materiales existentes y nuevas adquisiciones no catalogadas [11, 28].
    """
    if request.method == 'POST':
        fecha = request.POST.get('fecha')
        payload_raw = request.POST.get("payload")

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
                    estado='REGISTRADA'
                )

                for item_key, item_data in payload.items():
                    cantidad = int(item_data.get('cantidad', 1))
                    es_nuevo = item_data.get('es_nuevo', False)

                    if cantidad <= 0:
                        raise ValueError("La cantidad solicitada debe ser mayor a cero.")

                    if es_nuevo:
                        # Si es un ítem no catalogado, se guarda sin material_id
                        DetalleSolicitud.objects.create(
                            solicitud=solicitud,
                            material=None,
                            es_nueva_adquisicion=True,
                            descripcion_material_no_catalogado=item_data.get('nombre'),
                            cantidad_solicitada=cantidad
                        )
                    else:
                        material = Material.objects.get(id=item_key)
                        DetalleSolicitud.objects.create(
                            solicitud=solicitud,
                            material=material,
                            cantidad_solicitada=cantidad
                        )

                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Solicitudes',
                    accion='Registrar Solicitud',
                    descripcion=f'Se registró la Solicitud {codigo} con ítems personalizados para la unidad {unidad_solicitante.nombre}'
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

        # Autogenerar código correlativo de manera automática para el nuevo material
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
                # 1. Crear el material oficial con stock en 0 para habilitar su posterior compra
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

                # 2. Asociar el material al detalle de la solicitud y desactivar bandera
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
    solicitud = get_object_or_404(Solicitud, id=id)
    perfil = request.user.perfilusuario

    # Control de accesos de seguridad
    if perfil.rol != 'ADMINISTRADOR':
        if perfil.rol in ['JEFE_INMEDIATO', 'PRESUPUESTOS']:
            if solicitud.unidad_solicitante != perfil.unidad:
                return HttpResponseForbidden("No autorizado para ver solicitudes de otras unidades.")
        else:
            if solicitud.solicitante != request.user:
                return HttpResponseForbidden("No autorizado.")

    return render(request, 'solicitudes/detalle.html', {
        'solicitud': solicitud,
        'rol': perfil.rol
    })


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
        return redirect(request.META.get('HTTP_REFERER', 'solicitudes'))

    if solicitud.estado != 'REGISTRADA':
        messages.error(request, "Esta solicitud ya no se encuentra en estado Registrada.")
        return redirect('solicitudes')
    
    if solicitud.estado != 'REGISTRADA':
        messages.error(request, "Esta solicitud ya no se encuentra en estado Registrada.")
        return redirect(request.META.get('HTTP_REFERER', 'solicitudes'))

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
                solicitud.revisado_por = request.user            # <-- NUEVO: Guarda el usuario
                solicitud.fecha_revision = timezone.now()         # <-- NUEVO: Guarda fecha/hora
                solicitud.save()

                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Solicitudes',
                    accion='Revisar Solicitud',
                    descripcion=f'El jefe inmediato revisó y autorizó cantidades para la solicitud {solicitud.codigo}'
                )

            messages.success(request, f"Solicitud {solicitud.codigo} revisada y autorizada correctamente.")
            return redirect(request.META.get('HTTP_REFERER', 'solicitudes'))

        except DatabaseError:
            messages.error(request, "Error al procesar la revisión de la solicitud.")
            return redirect(request.META.get('HTTP_REFERER', 'solicitudes'))

    return render(request, 'solicitudes/aprobar.html', {
        'solicitud': solicitud,
        'detalles': detalles
    })


@login_required
@rol_requerido(['PRESUPUESTOS', 'ADMINISTRADOR'])
def aprobar_solicitud(request, id):
    """
    Paso 4: Presupuestos verifica la disponibilidad del POA antes del despacho físico [28].
    Solo permite validar solicitudes que ya cuenten con la validación de la SAF.
    """
    solicitud = get_object_or_404(Solicitud.objects.select_for_update(), id=id)

    # CORRECCIÓN DE SEGURIDAD SABS: Espera el estado 'VALIDADA_SAF' [28]
    if solicitud.estado != 'VALIDADA_SAF':
        messages.error(request, "Solo solicitudes validadas por la SAF pueden aprobarse presupuestariamente.")
        return redirect(request.META.get('HTTP_REFERER', 'solicitudes')) # <-- REDIRECCIÓN SEGURA (No te saca del folio)

    if not solicitud.tiene_detalles():
        messages.error(request, "La solicitud no contiene ningún material registrado y no puede ser aprobada presupuestariamente.")
        return redirect(request.META.get('HTTP_REFERER', 'solicitudes'))

    detalles = solicitud.detalles.select_related('material__partida')

    try:
        with transaction.atomic():
            costos_partidas = {}
            for detalle in detalles:
                last_entrada = MovimientoInventario.objects.filter(material=detalle.material, tipo='ENTRADA').order_by('-fecha').first()
                costo_u = last_entrada.costo_unitario if last_entrada else Decimal('0.00')
                
                # Respaldo seguro contra nulos
                cant_para_calculo = detalle.cantidad_aprobada if detalle.cantidad_aprobada is not None else detalle.cantidad_solicitada
                costo_estimado = cant_para_calculo * costo_u
                
                partida = detalle.material.partida
                costos_partidas[partida] = costos_partidas.get(partida, Decimal('0.00')) + costo_estimado

            # Verificar la disponibilidad en el POA para cada partida involucrada
            for partida, costo in costos_partidas.items():
                poa = POA.objects.filter(
                    unidad=solicitud.unidad_solicitante,
                    partida=partida,
                    gestion=GESTION_ACTUAL
                ).first()

                if not poa or poa.monto_disponible < costo:
                    messages.error(request, f"Presupuesto insuficiente en el POA de la unidad para la partida {partida.codigo}. Disponible: {poa.monto_disponible if poa else 0.00} Bs.")
                    return redirect(request.META.get('HTTP_REFERER', 'solicitudes')) # <-- REDIRECCIÓN SEGURA (No te saca del folio)

            # Si pasa la validación, cambia a 'VALIDADA_PRESUPUESTOS' (Aprobada presupuestariamente) [28]
            solicitud.estado = 'VALIDADA_PRESUPUESTOS'
            solicitud.presupuestado_por = request.user
            solicitud.fecha_presupuesto = timezone.now()
            solicitud.save()

            Bitacora.objects.create(
                usuario=request.user,
                modulo='Solicitudes',
                accion='Aprobación Presupuestaria',
                descripcion=f'Se aprobó presupuestariamente la Solicitud {solicitud.codigo} contra el POA de la unidad {solicitud.unidad_solicitante.nombre}'
            )

        messages.success(request, f"Solicitud {solicitud.codigo} aprobada presupuestariamente de forma correcta.")
        return redirect(request.META.get('HTTP_REFERER', 'solicitudes')) # <-- REDIRECCIÓN SEGURA

    except DatabaseError:
        messages.error(request, "Error de base de datos al realizar el control presupuestario.")
        return redirect(request.META.get('HTTP_REFERER', 'solicitudes'))


@login_required
@rol_requerido(['ALMACENERO', 'ADMINISTRADOR'])
def preparar_solicitud(request, id):
    """
    Paso 7: El Almacenero alista los paquetes físicamente en el depósito [28].
    Solo permite preparar solicitudes que ya cuenten con la aprobación de la Jefatura Administrativa.
    """
    solicitud = get_object_or_404(Solicitud, id=id)

    # CORRECCIÓN DE SEGURIDAD SABS: Espera la aprobación de la Jefatura Administrativa [28]
    if solicitud.estado != 'VALIDADA_JEFATURA':
        messages.error(request, "Solo solicitudes con aprobación de la Jefatura Administrativa pueden prepararse físicamente.")
        return redirect(request.META.get('HTTP_REFERER', 'solicitudes'))

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
    return redirect(request.META.get('HTTP_REFERER', 'solicitudes')) 
@login_required
def entregar_solicitud(request, id):
    """
    Paso 5: Entrega física de los materiales. Descuenta stock por PEPS y reduce el POA de la unidad solicitante [28].
    """
    if not tiene_rol(request.user, ['ALMACENERO', 'ADMINISTRADOR']):
        return HttpResponseForbidden("No autorizado.")

    solicitud = get_object_or_404(Solicitud, id=id)

    if solicitud.estado != 'PREPARADA':
        messages.error(request, "Solo solicitudes en estado PREPARADA pueden entregarse físicamente.")
        return redirect(request.META.get('HTTP_REFERER', 'solicitudes'))

    detalles = solicitud.detalles.select_related('material__partida')

    try:
        with transaction.atomic():
            solicitud = Solicitud.objects.select_for_update().get(id=id)
            costos_partidas = {}

            for detalle in detalles:
                material = Material.objects.get(id=detalle.material.id)
                
                # --- RESPALDO SEGURO CONTRA NULOS ---
                # Si cantidad_aprobada es nula, despachamos la cantidad solicitada original
                cantidad_despacho = detalle.cantidad_aprobada if detalle.cantidad_aprobada is not None else detalle.cantidad_solicitada

                if material.stock_actual < cantidad_despacho:
                    messages.error(request, f"Inconsistencia: Stock insuficiente en {material.nombre} para despachar la solicitud.")
                    return redirect('solicitudes')

                # Consumir y calcular costo real PEPS
                mov = registrar_salida_valorada_peps(
                    material=material,
                    cantidad_salida=cantidad_despacho,
                    tipo_movimiento='SALIDA',
                    referencia=f"DESPACHO: {solicitud.codigo}",
                    usuario=request.user,
                    unidad_destino=solicitud.unidad_solicitante
                )

                # Guardamos la cantidad entregada real
                detalle.cantidad_entregada = cantidad_despacho
                detalle.save()

                # Acumular el costo real de salida para restar del POA
                partida = material.partida
                costos_partidas[partida] = costos_partidas.get(partida, Decimal('0.00')) + mov.costo_total

            # B. Descontar el presupuesto real consumido del POA de la unidad
            for partida, costo_real in costos_partidas.items():
                poa = POA.objects.get(
                    unidad=solicitud.unidad_solicitante,
                    partida=partida,
                    gestion=GESTION_ACTUAL
                )
                poa.monto_disponible -= costo_real
                poa.save()

            # C. Cambiar estado a 'ENTREGADA'
            solicitud.estado = 'ENTREGADA'
            solicitud.entregado_por = request.user                # <-- NUEVO: Guarda el usuario
            solicitud.fecha_entrega = timezone.now()               # <-- NUEVO: Guarda fecha/hora
            solicitud.save()

            Bitacora.objects.create(
                usuario=request.user,
                modulo='Solicitudes',
                accion='Entregar Solicitud',
                descripcion=f'Despacho y entrega física de la Solicitud {solicitud.codigo}. Se afectó el stock y el POA.'
            )

        messages.success(request, f"Entrega física procesada y stock/POA deducidos de forma exitosa.")
        return redirect(request.META.get('HTTP_REFERER', 'solicitudes'))

    except Exception as e:
        messages.error(request, f"Error al procesar el despacho PEPS/POA: {str(e)}")
        return redirect(request.META.get('HTTP_REFERER', 'solicitudes'))


@login_required
def cerrar_solicitud(request, id):
    """
    Paso 6: Concluye administrativamente la carpeta de solicitud (Pasa de 'ENTREGADA' a 'CERRADA').
    """
    if not tiene_rol(request.user, ['ALMACENERO', 'ADMINISTRADOR']):
        return HttpResponseForbidden("No autorizado.")

    solicitud = get_object_or_404(Solicitud, id=id)

    if solicitud.estado != 'ENTREGADA':
        messages.error(request, "Solo solicitudes ENTREGADAS pueden marcarse como CERRADAS.")
        return redirect(request.META.get('HTTP_REFERER', 'solicitudes'))

    solicitud.estado = 'CERRADA'
    solicitud.cerrado_por = request.user                          # <-- NUEVO: Guarda el usuario
    solicitud.fecha_cierre = timezone.now()                        # <-- NUEVO: Guarda fecha/hora
    solicitud.save()

    Bitacora.objects.create(
        usuario=request.user,
        modulo='Solicitudes',
        accion='Cerrar trámite',
        descripcion=f'Se archivó y cerró el trámite de la Solicitud {solicitud.codigo}'
    )

    messages.success(request, f"La solicitud {solicitud.codigo} ha sido CERRADA y archivada correctamente.")
    return redirect(request.META.get('HTTP_REFERER', 'solicitudes'))

@login_required
def editar_solicitud(request, id):
    """
    Permite modificar una solicitud existente únicamente si se encuentra en el primer paso del flujo ('REGISTRADA') [11, 28].
    """
    solicitud = get_object_or_404(Solicitud, id=id)
    perfil = request.user.perfilusuario

    # Control de accesos de seguridad
    if perfil.rol != 'ADMINISTRADOR':
        if solicitud.unidad_solicitante != perfil.unidad:
            return HttpResponseForbidden("No tiene permisos para modificar solicitudes de otra unidad.")

    # Control de flujo: Solo es editable en estado inicial 'REGISTRADA' [28]
    if solicitud.estado != 'REGISTRADA':
        return HttpResponseForbidden("Esta solicitud ya se encuentra en proceso de revisión y no es editable.")

    if request.method == "GET":
        detalles = solicitud.detalles.select_related('material')

        detalles_json = json.dumps([
            {
                "id": d.material.id,
                "nombre": d.material.nombre,
                "cantidad": d.cantidad_solicitada,
                "stock": d.material.stock_actual
            }
            for d in detalles
        ])

        return render(request, "solicitudes/editar.html", {
            "solicitud": solicitud,
            "detalles_json": detalles_json
        })

    # Procesar actualización mediante carga de payload JSON (POST/PUT)
    try:
        data = json.loads(request.body.decode("utf-8"))

        payload = data.get("payload", {})
        justificacion = data.get("justificacion", "")

        if not payload:
            return JsonResponse({
                "ok": False,
                "error": "Debe agregar al menos un material a la solicitud"
            }, status=400)

        with transaction.atomic():
            solicitud.justificacion = justificacion
            solicitud.save()

            # Eliminar el detalle antiguo para registrar los nuevos cambios
            solicitud.detalles.all().delete()

            for material_id, cantidad in payload.items():
                material = Material.objects.get(id=material_id)
                cantidad = int(cantidad)

                if cantidad <= 0:
                    return JsonResponse({
                        "ok": False,
                        "error": "La cantidad de los ítems debe ser mayor a cero."
                    }, status=400)

                DetalleSolicitud.objects.create(
                    solicitud=solicitud,
                    material=material,
                    cantidad_solicitada=cantidad
                )

            Bitacora.objects.create(
                usuario=request.user,
                modulo='Solicitudes',
                accion='Editar Solicitud',
                descripcion=f'Se modificaron las cantidades o datos de la solicitud {solicitud.codigo}'
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
    Permite a cualquier rol revisor de la cadena SABS rechazar y archivar el requerimiento [28].
    """
    # --- ACTUALIZAMOS LA LISTA DE ROLES AUTORIZADOS ---
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

        solicitud.estado = 'RECHAZADA'
        solicitud.motivo_rechazo = motivo
        solicitud.aprobado_por = request.user.get_full_name() or request.user.username
        solicitud.save()

        Bitacora.objects.create(
            usuario=request.user,
            modulo='Solicitudes',
            accion='Rechazar Solicitud',
            descripcion=f'Se rechazó la solicitud {solicitud.codigo} por: {motivo}'
        )

        messages.info(request, f"La solicitud {solicitud.codigo} ha sido rechazada.")
        return redirect('solicitudes')

    return render(request, 'solicitudes/rechazar.html', {'solicitud': solicitud})

@login_required
def reabrir_solicitud(request, id):
    if not tiene_rol(request.user, ['JEFE_INMEDIATO', 'ADMINISTRADOR']):
        return HttpResponseForbidden("No tiene permisos para realizar esta acción.")

    solicitud = get_object_or_404(Solicitud, id=id)

    if solicitud.estado != 'RECHAZADA':
       return redirect(request.META.get('HTTP_REFERER', 'solicitudes'))

    solicitud.estado = 'REGISTRADA'  # Devuelve a estado inicial
    solicitud.save()

    messages.info(request, f"La solicitud {solicitud.codigo} ha sido reabierta.")
    return redirect(request.META.get('HTTP_REFERER', 'solicitudes'))


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
    response['Content-Disposition'] = f'attachment; filename="pedido_material_{solicitud.codigo}.pdf"'

    # Formato Horizontal (Landscape) para acomodar todas las columnas oficiales del GAD Potosí
    pdf = canvas.Canvas(response, pagesize=landscape(letter))
    width, height = landscape(letter)

    # --- DIBUJAR CABECERA INSTITUCIONAL (Lado izquierdo) ---
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(50, height - 40, "ESTADO PLURINACIONAL DE BOLIVIA")
    pdf.drawString(50, height - 52, "GOBIERNO AUTÓNOMO DEPARTAMENTAL DE POTOSÍ")
    pdf.setFont("Helvetica", 9)
    pdf.drawString(50, height - 64, "ALMACÉN CENTRAL")

    # TÍTULO DEL DOCUMENTO
    pdf.setFont("Helvetica-Bold", 15)
    pdf.drawString(50, height - 95, "PEDIDO DE MATERIALES y/o BIENES")

    # --- DIBUJAR METADATOS DE PLANIFICACIÓN (Lado derecho) ---
    pdf.setFont("Helvetica", 8)
    right_x = 480
    pdf.drawString(right_x, height - 35, "Programa: _____________________________________")
    pdf.drawString(right_x, height - 47, "Subprograma: __________________________________")
    pdf.drawString(right_x, height - 59, "Proyecto: _____________________________________")
    pdf.drawString(right_x, height - 71, "Act. u Obra: __________________________________")
    pdf.drawString(right_x, height - 83, f"Unid. Ejec.: {solicitud.unidad_solicitante.nombre[:25]}")
    pdf.drawString(right_x, height - 95, f"Código Presup: _______________ Código Nº: {solicitud.codigo}")

    # Fecha de pedido
    pdf.setFont("Helvetica-Bold", 9)
    fecha_pedido = solicitud.fecha.strftime('%d / %m / %Y') if solicitud.fecha else "__ / __ / ____"
    pdf.drawString(50, height - 120, f"Fecha del Pedido: {fecha_pedido}")

    # --- CONSTRUIR LA TABLA DE MOVIMIENTOS ---
    # Cabeceras divididas de la tabla oficial
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
            
            # Buscar el último costo de ingreso
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

    # Configurar anchos de columna para Landscape (Ancho total disponible: 692 pt)
    col_widths = [75, 192, 55, 45, 45, 80, 100, 100]
    t = Table(data, colWidths=col_widths)

    t_style = TableStyle([
        # Uniones de celdas superiores para doble encabezado
        ('SPAN', (0, 0), (0, 1)),  # Codigo
        ('SPAN', (1, 0), (1, 1)),  # Descripcion
        ('SPAN', (2, 0), (2, 1)),  # Unidad de Manejo
        ('SPAN', (3, 0), (4, 0)),  # Cantidades (Pedida vs Entrega)
        ('SPAN', (5, 0), (5, 1)),  # Partida Presupuestaria
        ('SPAN', (6, 0), (7, 0)),  # Costo (Unidad vs Total)

        # Alineación y estilos
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

    # --- SECCIÓN DE FIRMAS REGLAMENTARIAS EN LA PARTE INFERIOR ---
    pdf.setFont("Helvetica", 7.5)
    
    # Fila 1 de Firmas (Pedido por, Autorizado por, Entregado por, Recibido por)
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

    # Fila 2 de Firmas (Control Existencias, Presupuestos)
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

    # Fecha de salida física real
    pdf.setFont("Helvetica-Bold", 8)
    fecha_salida = solicitud.fecha_entrega.strftime('%d / %m / %Y') if solicitud.fecha_entrega else "__ / __ / ____"
    pdf.drawString(540, y_firma_2, f"Fecha de salida física: {fecha_salida}")

    pdf.save()
    return response
# FILE: solicitudes/views.py (Reemplazar el bloque duplicado por esta única función)

@login_required
@rol_requerido(['SECRETARIO_SAF', 'ADMINISTRADOR'])
def validar_saf(request, id):
    """
    Paso 3: El Secretario de la SAF valida la solicitud (Estado: VALIDADA_SAF) [28].
    Solo permite validar solicitudes que ya fueron previamente revisadas por el Jefe de Unidad.
    """
    solicitud = get_object_or_404(Solicitud.objects.select_for_update(), id=id)

    # CORRECCIÓN DE FLUJO: Valida solo si ya fue revisada por su jefe de unidad [28]
    if solicitud.estado != 'REVISADA':
        messages.error(request, "La solicitud aún no ha sido revisada ni autorizada por su Jefe de Unidad.")
        return redirect(request.META.get('HTTP_REFERER', 'solicitudes'))

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