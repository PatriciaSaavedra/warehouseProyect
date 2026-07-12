import json
import html
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.db import transaction, DatabaseError
from django.contrib import messages
from django.http import HttpResponseForbidden, JsonResponse, HttpResponse
from reportlab.pdfgen import canvas

from .models import Solicitud, DetalleSolicitud
from inventario.models import Material, MovimientoInventario
from usuarios.decorators import tiene_rol
from presupuestos.models import POA


@login_required
@login_required
def solicitudes(request):

    perfil = getattr(request.user, 'perfilusuario', None)

    if not perfil:
        return render(request, 'solicitudes/index.html', {
            'solicitudes': []
        })

    rol = perfil.rol
    unidad = perfil.unidad

    # ================= ADMIN =================
    if rol == 'ADMINISTRADOR':
        solicitudes = Solicitud.objects.all()

    # ================= JEFE =================
    elif rol == 'JEFE_INMEDIATO':
        solicitudes = Solicitud.objects.filter(
            unidad_solicitante=unidad
        )

    # ================= USUARIO NORMAL =================
    else:
        solicitudes = Solicitud.objects.filter(
            solicitante=request.user
        )

    solicitudes = solicitudes.select_related('solicitante').order_by('-id')

    return render(request, 'solicitudes/index.html', {
        'solicitudes': solicitudes
    })


@login_required
def buscar_materiales(request):
    q = request.GET.get('q', '')
    materiales = Material.objects.filter(
        nombre__icontains=q
    )[:10]

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
    if request.method == 'POST':
        fecha = request.POST.get('fecha')

        payload_raw = request.POST.get("payload")

        if not payload_raw:
            messages.error(request, "No se recibió información.")
            return redirect('editar_solicitud', id=id)

        payload_raw = html.unescape(payload_raw)

        try:
            payload = json.loads(payload_raw)
        except json.JSONDecodeError:
            messages.error(request, "Error en formato de datos enviados.")
            return redirect('editar_solicitud', id=id)
        if not payload:
            messages.error(request, "Debe agregar al menos un material.")
            return redirect('nueva_solicitud')

        justificacion = request.POST.get('justificacion')
        perfil = getattr(request.user, 'perfilusuario', None)
        unidad_solicitante = perfil.unidad if perfil else None

        try:
            with transaction.atomic():

                ultima = Solicitud.objects.select_for_update().order_by('id').last()
                numero = (ultima.id + 1) if ultima else 1
                codigo = f'SOL-{numero:03d}'

                solicitud = Solicitud.objects.create(
                    codigo=codigo,
                    unidad_solicitante=unidad_solicitante,
                    solicitante=request.user,
                    fecha=fecha,
                    justificacion=justificacion,
                    estado='PENDIENTE_JEFE'
                )

                for material_id, cantidad in payload.items():
                    material = Material.objects.get(id=material_id)
                    cantidad = int(cantidad)

                    if cantidad > material.stock_actual:
                        raise ValueError(
                            f"No hay stock suficiente para {material.nombre}. Disponible: {material.stock_actual}"
                        )

                    DetalleSolicitud.objects.create(
                        solicitud=solicitud,
                        material=material,
                        cantidad_solicitada=cantidad
                    )

            messages.success(request, f"Solicitud {codigo} creada correctamente.")
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
def detalle_solicitud(request, id):

    solicitud = get_object_or_404(Solicitud, id=id)

    perfil = request.user.perfilusuario

    # 🔴 CONTROL DE ACCESO
    if perfil.rol != 'ADMINISTRADOR':

        if perfil.rol == 'JEFE_INMEDIATO':
            if solicitud.unidad_solicitante != perfil.unidad:
                return HttpResponseForbidden("No autorizado")

        else:
            if solicitud.solicitante != request.user:
                return HttpResponseForbidden("No autorizado")

    rol = perfil.rol

    return render(request, 'solicitudes/detalle.html', {
        'solicitud': solicitud,
        'rol': rol
    })

@login_required
def aprobar_solicitud(request, id):

    if not tiene_rol(request.user, ['JEFE_INMEDIATO', 'ADMINISTRADOR']):
        return HttpResponseForbidden("No tiene permisos.")

    solicitud = get_object_or_404(
        Solicitud.objects.select_for_update(),
        id=id
    )

    if solicitud.estado != 'PENDIENTE_JEFE':
        return redirect('solicitudes')

    detalles = solicitud.detalles.select_related('material')

    if request.method == 'POST':

        try:
            with transaction.atomic():

                stock_ok = True

                for detalle in detalles:
                    material = Material.objects.select_for_update().get(id=detalle.material.id)

                    # 🔴 CONSISTENCIA: fallback correcto
                    aprobada = int(request.POST.get(f"aprobado_{detalle.id}") or 0)

                    if aprobada < 0:
                        aprobada = 0

                    detalle.cantidad_aprobada = aprobada
                    detalle.save()

                    if material.stock_actual < aprobada:
                        stock_ok = False

                if stock_ok:
                    solicitud.estado = 'VALIDADO'
                    solicitud.aprobado_por = request.user.username
                else:
                    solicitud.estado = 'PENDIENTE_COMPRA'

                solicitud.save()

            messages.success(request, "Solicitud procesada correctamente.")
            return redirect('solicitudes')

        except DatabaseError:
            messages.error(request, "Error al procesar la aprobación.")
            return redirect('solicitudes')

    return render(request, 'solicitudes/aprobar.html', {
        'solicitud': solicitud,
        'detalles': detalles
    })
@login_required
def editar_solicitud(request, id):

    solicitud = get_object_or_404(Solicitud, id=id)

    perfil = request.user.perfilusuario

    # 🔴 CONTROL CENTRALIZADO
    if perfil.rol != 'ADMINISTRADOR':
        if solicitud.unidad_solicitante != perfil.unidad:
            return HttpResponseForbidden("No autorizado")

    if solicitud.estado != 'PENDIENTE_JEFE':
        return HttpResponseForbidden("No editable")

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

    try:
        data = json.loads(request.body.decode("utf-8"))

        payload = data.get("payload", {})
        justificacion = data.get("justificacion", "")

        if not payload:
            return JsonResponse({
                "ok": False,
                "error": "Debe agregar al menos un material"
            }, status=400)

        with transaction.atomic():

            solicitud.justificacion = justificacion
            solicitud.save()

            solicitud.detalles.all().delete()

            for material_id, cantidad in payload.items():
                material = Material.objects.get(id=material_id)
                cantidad = int(cantidad)

                if cantidad > material.stock_actual:
                    return JsonResponse({
                        "ok": False,
                        "error": f"Stock insuficiente para {material.nombre}"
                    }, status=400)

                DetalleSolicitud.objects.create(
                    solicitud=solicitud,
                    material=material,
                    cantidad_solicitada=cantidad
                )

        return JsonResponse({"ok": True})

    except json.JSONDecodeError:
        return JsonResponse({
            "ok": False,
            "error": "JSON inválido"
        }, status=400)
@login_required
def entregar_solicitud(request, id):

    if not tiene_rol(request.user, ['ALMACENERO', 'ADMINISTRADOR']):
        return HttpResponseForbidden()

    solicitud = get_object_or_404(Solicitud, id=id)

    if solicitud.estado != 'VALIDADO':
        messages.error(request, "Solo solicitudes VALIDADAS.")
        return redirect('solicitudes')

    detalles = solicitud.detalles.select_related('material')

    try:
        with transaction.atomic():

            solicitud = Solicitud.objects.select_for_update().get(id=id)

            materiales = []

            for detalle in detalles:
                material = Material.objects.select_for_update().get(id=detalle.material.id)

                cantidad = detalle.cantidad_solicitada

                if material.stock_actual < cantidad:
                    solicitud.estado = 'PENDIENTE_COMPRA'
                    solicitud.save()

                    messages.warning(request, f"Stock insuficiente en {material.nombre}")
                    return redirect('solicitudes')

                materiales.append((material, cantidad))

            for material, cantidad in materiales:
                material.stock_actual -= cantidad
                material.save()

                MovimientoInventario.objects.create(
                    material=material,
                    tipo='SALIDA',
                    cantidad=cantidad,
                    referencia=solicitud.codigo,
                    usuario=request.user
                )

            solicitud.estado = 'ENTREGADO'
            solicitud.save()

            messages.success(request, "Entrega realizada correctamente.")

    except DatabaseError:
        messages.error(request, "Error de base de datos.")

    return redirect('solicitudes')
@login_required
def rechazar_solicitud(request, id):
    if not tiene_rol(request.user, ['JEFE_INMEDIATO', 'ADMINISTRADOR']):
        return HttpResponseForbidden("No tiene permisos para realizar esta acción.")

    solicitud = get_object_or_404(Solicitud, id=id)

    if request.method == 'POST':
        motivo = request.POST.get('motivo_predefinido')
        if motivo == 'OTRO':
            motivo = request.POST.get('motivo_personalizado')

        solicitud.estado = 'RECHAZADO'
        solicitud.motivo_rechazo = motivo
        solicitud.aprobado_por = request.user.username
        solicitud.save()

        messages.info(request, f"La solicitud {solicitud.codigo} ha sido rechazada.")
        return redirect('solicitudes')

    return render(
        request,
        'solicitudes/rechazar.html',
        {
            'solicitud': solicitud
        }
    )


@login_required
def reabrir_solicitud(request, id):
    if not tiene_rol(request.user, ['JEFE_INMEDIATO', 'ADMINISTRADOR']):
        return HttpResponseForbidden("No tiene permisos para realizar esta acción.")

    solicitud = get_object_or_404(Solicitud, id=id)

    if solicitud.estado != 'RECHAZADO':
        return redirect('solicitudes')

    solicitud.estado = 'PENDIENTE_JEFE'
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
    pdf.drawString(50, 680, f"Estado: {solicitud.estado}")

    # JUSTIFICACIÓN
    pdf.drawString(50, 650, "Justificación:")
    # Nota: Si el texto es muy largo, se truncará a 90 caracteres para evitar desbordes en el lienzo
    pdf.drawString(70, 630, (solicitud.justificacion or "")[:90])

    # DETALLE
    y = 580
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(50, y, "Material")
    pdf.drawString(250, y, "Cantidad")

    y -= 20
    pdf.setFont("Helvetica", 10)

    for d in solicitud.detalles.all():
        # Nuevamente, ten en cuenta el nombre del campo del modelo para la cantidad.
        cantidad = getattr(d, 'cantidad', None) or getattr(d, 'cantidad_solicitada', 0)
        pdf.drawString(50, y, d.material.nombre)
        pdf.drawString(250, y, str(cantidad))

        y -= 20
        if y < 100:
            pdf.showPage()
            y = 750

    # FIRMA
    pdf.drawString(50, 100, "______________________")
    pdf.drawString(50, 85, "Firma Responsable")

    pdf.save()
    return response