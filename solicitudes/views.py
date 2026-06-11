from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.db import transaction, DatabaseError
from django.contrib import messages
from django.http import HttpResponseForbidden

from .models import Solicitud, DetalleSolicitud
from inventario.models import Material, MovimientoInventario
from usuarios.utils import tiene_rol

@login_required
def solicitudes(request):
    # Se optimiza la consulta precargando la relación 'solicitante'
    solicitudes = Solicitud.objects.all().select_related('solicitante').order_by('-id')
    return render(
        request,
        'solicitudes/index.html',
        {
            'solicitudes': solicitudes
        }
    )


@login_required
def nueva_solicitud(request):
    print("POST:", request.POST)

    print(
        request.POST.getlist('materiales')
    )

    print(
        request.POST.getlist('cantidades')
    )
    if request.method == 'POST':
        fecha = request.POST.get('fecha')
        materiales = request.POST.getlist(
            'materiales'
        )

        cantidades = request.POST.getlist(
            'cantidades'
        )

        if not materiales:

            messages.error(
                request,
                "Debe agregar al menos un material."
            )

            return redirect(
                'nueva_solicitud'
            )
        justificacion = request.POST.get('justificacion')



        perfil = getattr(request.user, 'perfilusuario', None)
        unidad_solicitante = perfil.unidad if perfil else None

        # 3. Operación atómica para evitar códigos duplicados y asegurar consistencia
        try:
            with transaction.atomic():
                # Bloqueamos el último registro para calcular el correlativo de forma segura
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
                for i in range(len(materiales)):

                    material = Material.objects.get(
                        id=materiales[i]
                    )

                    cantidad = int(
                        cantidades[i]
                    )

                    DetalleSolicitud.objects.create(

                        solicitud=solicitud,

                        material=material,

                        cantidad=cantidad

                    )
           
            messages.success(request, f"Solicitud {codigo} creada correctamente.")
            return redirect('solicitudes')

        except DatabaseError:
            messages.error(request, "Hubo un error al procesar el guardado en la base de datos.")
            return redirect('nueva_solicitud')

    # GET
    materiales = Material.objects.all()
    return render(
        request,
        'solicitudes/nueva.html',
        {
            'materiales': materiales
        }
    )


@login_required
def detalle_solicitud(request, id):
    # Se puede optimizar la carga del detalle y sus materiales relacionados
    solicitud = get_object_or_404(
        Solicitud.objects.prefetch_related('detalles__material'), 
        id=id
    )
    rol = request.user.perfilusuario.rol

    return render(
        request,
        'solicitudes/detalle.html',
        {
            'solicitud': solicitud,
            'rol': rol
        }
    )

@login_required
def aprobar_solicitud(request, id):
    if not tiene_rol(request.user, ['JEFE_INMEDIATO', 'ADMINISTRADOR']):
        return HttpResponseForbidden("No tiene permisos para aprobar solicitudes.")

    try:
        with transaction.atomic():
            # Bloquear la fila de la solicitud para que no sea modificada concurrentemente
            solicitud = get_object_or_404(Solicitud.objects.select_for_update(), id=id)

            if solicitud.estado != 'PENDIENTE_JEFE':
                return redirect('solicitudes')

        detalles = solicitud.detalles.select_related(
    'material'
)

        if not detalles.exists():

            return HttpResponseForbidden(
                "La solicitud no tiene materiales asociados."
            )

        stock_completo = True

        for detalle in detalles:

            material = Material.objects.select_for_update().get(
                id=detalle.material.id
            )

            if material.stock_actual < detalle.cantidad:

                stock_completo = False

                break
        if stock_completo:

            solicitud.estado = 'VALIDADO'

            solicitud.aprobado_por = request.user.username

        else:

            solicitud.estado = 'PENDIENTE_COMPRA'

        solicitud.save()

    except DatabaseError:
        messages.error(request, "No se pudo procesar la aprobación en este momento.")
        return redirect('solicitudes')

    return redirect('solicitudes')


@login_required
def entregar_solicitud(request, id):
    if not tiene_rol(request.user, ['ALMACENERO', 'ADMINISTRADOR']):
        return HttpResponseForbidden("No tiene permisos para entregar materiales.")

    try:
        with transaction.atomic():
            # Bloquear la solicitud para evitar doble entrega
            solicitud = get_object_or_404(Solicitud.objects.select_for_update(), id=id)

            if solicitud.estado != 'VALIDADO':
                messages.error(request, "Solo se pueden entregar solicitudes con estado VALIDADO.")
                return redirect('solicitudes')

            if not solicitud.tiene_detalles():
                return HttpResponseForbidden("La solicitud no tiene materiales asociados.")

            detalles = solicitud.detalles.select_related(
                'material'
            )

            if not detalles.exists():

                return HttpResponseForbidden(
                    "La solicitud no tiene materiales."
                )

            # Verificar stock de TODOS los materiales

            for detalle in detalles:

                material = Material.objects.select_for_update().get(
                    id=detalle.material.id
                )

                if material.stock_actual < detalle.cantidad:

                    solicitud.estado = 'PENDIENTE_COMPRA'

                    solicitud.save()

                    messages.warning(
                        request,
                        f"Stock insuficiente para {material.nombre}"
                    )

                    return redirect('solicitudes')

            for detalle in detalles:

                material = Material.objects.select_for_update().get(
                    id=detalle.material.id
                )

                material.stock_actual -= detalle.cantidad

                material.save()

                MovimientoInventario.objects.create(

                    material=material,

                    tipo='SALIDA',

                    cantidad=detalle.cantidad,

                    referencia=solicitud.codigo,

                    usuario=request.user

                )
            # Cambiar estado
            solicitud.estado = 'ENTREGADO'
            solicitud.save()

            messages.success(request, f"Solicitud {solicitud.codigo} entregada con éxito.")

    except DatabaseError:
        messages.error(request, "Ocurrió un error de base de datos al intentar procesar la entrega.")
        return redirect('solicitudes')

    return redirect('solicitudes')


@login_required
def rechazar_solicitud(request, id):
    if not tiene_rol(request.user, ['JEFE_INMEDIATO', 'ADMINISTRADOR']):
        return HttpResponseForbidden("No tiene permisos para realizar esta acción.")

    solicitud = get_object_or_404(Solicitud, id=id)

    if request.method == 'POST':
        print(request.POST)
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