import json
import html
from django.utils import timezone 
from decimal import Decimal
from django.http import JsonResponse, HttpResponse, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.db import transaction, DatabaseError
from django.contrib import messages
from reportlab.pdfgen import canvas

from .models import Solicitud, DetalleSolicitud
from inventario.models import Material, MovimientoInventario
from inventario.models import PartidaPresupuestaria, UnidadMedida
from inventario.services import registrar_salida_valorada_peps  # Importamos nuestro servicio PEPS (FIFO)
from usuarios.decorators import tiene_rol, rol_requerido
from presupuestos.models import POA
from auditoria.models import Bitacora
GESTION_ACTUAL = 2026

@login_required
def solicitudes(request):
    perfil = getattr(request.user, 'perfilusuario', None)

    if not perfil:
        return render(request, 'solicitudes/index.html', {'solicitudes': []})

    rol = perfil.rol
    unidad = perfil.unidad

    # Filtrar solicitudes según el rol (RE-SABS)
    if rol == 'ADMINISTRADOR':
        solicitudes_query = Solicitud.objects.all()
    elif rol in ['JEFE_INMEDIATO', 'PRESUPUESTOS']:
        solicitudes_query = Solicitud.objects.filter(unidad_solicitante=unidad)
    else:
        solicitudes_query = Solicitud.objects.filter(solicitante=request.user)

    solicitudes_query = solicitudes_query.select_related('solicitante', 'unidad_solicitante').order_by('-id')

    hoy = timezone.now().date()        # Obtiene la fecha de hoy como objeto datetime.date
    primer_dia_mes = hoy.replace(day=1) # Obtiene el primer día del mes actual (seguro y sin errores)
    
    pendientes_count = Solicitud.objects.filter(estado='REGISTRADA').count()
    preparacion_count = Solicitud.objects.filter(estado__in=['APROBADA', 'PREPARADA']).count()
    entregas_hoy_count = Solicitud.objects.filter(estado='ENTREGADA', fecha=hoy).count()
    total_folios_count = Solicitud.objects.filter(fecha__gte=primer_dia_mes).count()

    return render(request, 'solicitudes/index.html', {
        'solicitudes': solicitudes_query,
        'rol': rol,
        # Métricas enviadas al HTML
        'kpi_pendientes': pendientes_count,
        'kpi_preparacion': preparacion_count,
        'kpi_entregas_hoy': entregas_hoy_count,
        'kpi_total_folios': total_folios_count,
    })

@login_required
def buscar_materiales(request):
    q = request.GET.get('q', '').strip()
    materiales = Material.objects.filter(nombre__icontains=q)[:10]

    data = [
        {
            'id': m.id,
            'nombre': m.nombre,
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

    if solicitud.estado != 'REGISTRADA':
        messages.error(request, "Esta solicitud ya no se encuentra en estado Registrada.")
        return redirect('solicitudes')

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
            return redirect('solicitudes')

        except DatabaseError:
            messages.error(request, "Error al procesar la revisión de la solicitud.")
            return redirect('solicitudes')

    return render(request, 'solicitudes/aprobar.html', {
        'solicitud': solicitud,
        'detalles': detalles
    })


@login_required
def aprobar_solicitud(request, id):
    """
    Paso 3: Presupuestos verifica la disponibilidad del POA antes del despacho físico (Pasa de 'REVISADA' a 'APROBADA') [28].
    """
    if not tiene_rol(request.user, ['PRESUPUESTOS', 'ADMINISTRADOR']):
        return HttpResponseForbidden("No autorizado.")

    solicitud = get_object_or_404(Solicitud.objects.select_for_update(), id=id)

    if solicitud.estado != 'REVISADA':
        messages.error(request, "Solo solicitudes en estado REVISADA pueden aprobarse presupuestariamente.")
        return redirect('solicitudes')

    detalles = solicitud.detalles.select_related('material__partida')

    try:
        with transaction.atomic():
            # Agrupar costos por partida presupuestaria para validar techos del POA
            costos_partidas = {}
            for detalle in detalles:
                # Estimamos el costo unitario basándonos en la última entrada PEPS
                last_entrada = MovimientoInventario.objects.filter(material=detalle.material, tipo='ENTRADA').order_by('-fecha').first()
                costo_u = last_entrada.costo_unitario if last_entrada else Decimal('0.00')
                costo_estimado = detalle.cantidad_aprobada * costo_u
                
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
                    # Si no hay presupuesto suficiente en el POA de la unidad
                    messages.error(request, f"Presupuesto insuficiente en el POA de la unidad para la partida {partida.codigo}. Disponible: {poa.monto_disponible if poa else 0.00} Bs.")
                    return redirect('solicitudes')

            # Si pasa la validación presupuestaria, cambia a 'APROBADA'
            solicitud.estado = 'APROBADA'
            solicitud.presupuestado_por = request.user            # <-- NUEVO: Guarda el usuario
            solicitud.fecha_presupuesto = timezone.now()           # <-- NUEVO: Guarda fecha/hora
            solicitud.save()

            Bitacora.objects.create(
                usuario=request.user,
                modulo='Solicitudes',
                accion='Aprobación Presupuestaria',
                descripcion=f'Se aprobó presupuestariamente la Solicitud {solicitud.codigo} contra el POA de la unidad {solicitud.unidad_solicitante.nombre}'
            )

        messages.success(request, f"Solicitud {solicitud.codigo} aprobada presupuestariamente de forma correcta.")
        return redirect('solicitudes')

    except DatabaseError:
        messages.error(request, "Error de base de datos al realizar el control presupuestario.")
        return redirect('solicitudes')


@login_required
def preparar_solicitud(request, id):
    """
    Paso 4: El Almacenero alista los paquetes físicamente en el depósito (Pasa de 'APROBADA' a 'PREPARADA').
    """
    if not tiene_rol(request.user, ['ALMACENERO', 'ADMINISTRADOR']):
        return HttpResponseForbidden("No autorizado.")

    solicitud = get_object_or_404(Solicitud, id=id)

    if solicitud.estado != 'APROBADA':
        messages.error(request, "Solo solicitudes APROBADAS presupuestariamente pueden prepararse.")
        return redirect('solicitudes')

    solicitud.estado = 'PREPARADA'
    solicitud.preparado_por = request.user                        # <-- NUEVO: Guarda el usuario
    solicitud.fecha_preparado = timezone.now()                     # <-- NUEVO: Guarda fecha/hora
    solicitud.save()

    Bitacora.objects.create(
        usuario=request.user,
        modulo='Solicitudes',
        accion='Preparación física',
        descripcion=f'El almacenero preparó y empaquetó físicamente los materiales de la solicitud {solicitud.codigo}'
    )

    messages.success(request, f"La solicitud {solicitud.codigo} ha sido marcada como PREPARADA para su despacho.")
    return redirect('solicitudes')


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
        return redirect('solicitudes')

    detalles = solicitud.detalles.select_related('material__partida')

    try:
        with transaction.atomic():
            solicitud = Solicitud.objects.select_for_update().get(id=id)
            costos_partidas = {}

            # A. Validar stock físico e importes reales antes de despachar
            for detalle in detalles:
                material = Material.objects.get(id=detalle.material.id)
                cantidad_despacho = detalle.cantidad_aprobada or 0

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
        return redirect('solicitudes')

    except Exception as e:
        messages.error(request, f"Error al procesar el despacho PEPS/POA: {str(e)}")
        return redirect('solicitudes')


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
        return redirect('solicitudes')

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
    return redirect('solicitudes')

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
    if not tiene_rol(request.user, ['JEFE_INMEDIATO', 'PRESUPUESTOS', 'ADMINISTRADOR']):
        return HttpResponseForbidden("No tiene permisos para realizar esta acción.")

    solicitud = get_object_or_404(Solicitud, id=id)

    if request.method == 'POST':
        motivo = request.POST.get('motivo_predefinido')
        if motivo == 'OTRO':
            motivo = request.POST.get('motivo_personalizado')

        solicitud.estado = 'RECHAZADA'
        solicitud.motivo_rechazo = motivo
        solicitud.aprobado_por = request.user.username
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
        return redirect('solicitudes')

    solicitud.estado = 'REGISTRADA'  # Devuelve a estado inicial
    solicitud.save()

    messages.info(request, f"La solicitud {solicitud.codigo} ha sido reabierta.")
    return redirect('solicitudes')


@login_required
def solicitud_pdf(request, id):
    solicitud = get_object_or_404(
        Solicitud.objects.prefetch_related('detalles__material'),
        id=id
    )

    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="solicitud_{solicitud.codigo}.pdf"'

    pdf = canvas.Canvas(response)

    # TÍTULO
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(180, 800, "SOLICITUD DE MATERIALES")

    # DATOS GENERALES
    pdf.setFont("Helvetica", 11)
    pdf.drawString(50, 760, f"Código: {solicitud.codigo}")
    pdf.drawString(50, 740, f"Unidad: {solicitud.unidad_solicitante}")
    pdf.drawString(50, 720, f"Solicitante: {solicitud.solicitante.username}")
    pdf.drawString(50, 700, f"Fecha: {solicitud.fecha}")
    pdf.drawString(50, 680, f"Estado: {solicitud.get_estado_display()}")

    # JUSTIFICACIÓN
    pdf.drawString(50, 650, "Justificación:")
    pdf.drawString(70, 630, (solicitud.justificacion or "")[:90])

    # DETALLE
    y = 580
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(50, y, "Material")
    pdf.drawString(250, y, "Cantidad Solicitada")
    pdf.drawString(400, y, "Cant. Entregada")

    y -= 20
    pdf.setFont("Helvetica", 10)

    for d in solicitud.detalles.all():
        pdf.drawString(50, y, d.material.nombre[:30])
        pdf.drawString(250, y, str(d.cantidad_solicitada))
        pdf.drawString(400, y, str(d.cantidad_entregada))

        y -= 20
        if y < 100:
            pdf.showPage()
            y = 750

    # FIRMA
    pdf.drawString(50, 100, "______________________")
    pdf.drawString(50, 85, "Firma Responsable")

    pdf.save()
    return response