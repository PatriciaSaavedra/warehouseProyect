from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from .models import Solicitud, DetalleSolicitud
from inventario.models import Material, MovimientoInventario
from usuarios.utils import tiene_rol
from django.http import HttpResponseForbidden

@login_required
def solicitudes(request):

    solicitudes = Solicitud.objects.all().order_by('-id')

    return render(
        request,
        'solicitudes/index.html',
        {
            'solicitudes': solicitudes
        }
    )


@login_required
def nueva_solicitud(request):

    if request.method == 'POST':

        unidad = request.POST.get('unidad')

        fecha = request.POST.get('fecha')

        material_id = request.POST.get('material')

        material = Material.objects.get(id=material_id)

        cantidad = int(request.POST.get('cantidad'))

        justificacion = request.POST.get('justificacion')

        # GENERAR CODIGO
        ultima = Solicitud.objects.last()

        numero = ultima.id + 1 if ultima else 1

        codigo = f'SOL-{numero:03d}'

        perfil = request.user.perfilusuario

        # CREAR SOLICITUD
        solicitud = Solicitud.objects.create(

            codigo=codigo,

            unidad_solicitante=perfil.unidad,

            solicitante=request.user,

            fecha=fecha,

            justificacion=justificacion,

            estado='PENDIENTE_JEFE'

        )

        # CREAR DETALLE
        DetalleSolicitud.objects.create(

            solicitud=solicitud,

            material=material,

            cantidad=cantidad

        )

        return redirect('solicitudes')

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

    solicitud = get_object_or_404(Solicitud, id=id)

    return render(
        request,
        'solicitudes/detalle.html',
        {
            'solicitud': solicitud
        }
    )


@login_required
def aprobar_solicitud(request, id):

    if not tiene_rol(
        request.user,
        ['JEFE_INMEDIATO', 'ADMINISTRADOR']
    ):
        return HttpResponseForbidden(
            "No tiene permisos"
        )

    solicitud = get_object_or_404(
        Solicitud,
        id=id
    )

    # EVITAR APROBAR DOS VECES

    if solicitud.estado != 'PENDIENTE_JEFE':

        return redirect('solicitudes')

    detalle = solicitud.detalles.first()

    material = detalle.material

    # VALIDAR STOCK

    if material.stock_actual < detalle.cantidad:

        solicitud.estado = 'PENDIENTE_COMPRA'

        solicitud.save()

        return redirect('solicitudes')

    # SOLO VALIDAR, NO DESCONTAR STOCK

    solicitud.estado = 'VALIDADO'

    solicitud.aprobado_por = request.user.username

    solicitud.save()

    return redirect('solicitudes')
@login_required
def entregar_solicitud(request, id):

    if not tiene_rol(
        request.user,
        ['ALMACENERO', 'ADMINISTRADOR']
    ):
        return HttpResponseForbidden(
            "No tiene permisos"
        )

    solicitud = get_object_or_404(
        Solicitud,
        id=id
    )

    # SOLO SE PUEDEN ENTREGAR SOLICITUDES VALIDADAS

    if solicitud.estado != 'VALIDADO':

        return redirect('solicitudes')

    detalle = solicitud.detalles.first()

    material = detalle.material

    # VALIDAR STOCK

    if material.stock_actual < detalle.cantidad:

        solicitud.estado = 'PENDIENTE_COMPRA'

        solicitud.save()

        return redirect('solicitudes')

    # DESCONTAR STOCK

    material.stock_actual -= detalle.cantidad

    material.save()

    # REGISTRAR MOVIMIENTO DE SALIDA

    MovimientoInventario.objects.create(

        material=material,

        tipo='SALIDA',

        cantidad=detalle.cantidad,

        referencia=solicitud.codigo,

        usuario=request.user

    )

    # CAMBIAR ESTADO

    solicitud.estado = 'ENTREGADO'

    solicitud.save()

    return redirect('solicitudes')

@login_required
def rechazar_solicitud(request, id):

    if not tiene_rol(
        request.user,
        ['JEFE_INMEDIATO', 'ADMINISTRADOR']
    ):
        return HttpResponseForbidden(
            "No tiene permisos"
        )

    solicitud = get_object_or_404(
        Solicitud,
        id=id
    )

    if request.method == 'POST':

        motivo = request.POST.get(
            'motivo_predefinido'
        )

        if motivo == 'OTRO':

            motivo = request.POST.get(
                'motivo_personalizado'
            )

        solicitud.estado = 'RECHAZADO'

        solicitud.motivo_rechazo = motivo

        solicitud.aprobado_por = (
            request.user.username
        )

        solicitud.save()

        return redirect(
            'solicitudes'
        )

    return render(
        request,
        'solicitudes/rechazar.html',
        {
            'solicitud': solicitud
        }
    )