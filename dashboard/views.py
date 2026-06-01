from django.shortcuts import render
from django.db import models

from inventario.models import Material, MovimientoInventario
from solicitudes.models import Solicitud


def dashboard_view(request):

    # ALERTAS

    materiales_bajos = Material.objects.filter(
        stock_actual__lte=models.F('stock_minimo')
    )

    # MÉTRICAS

    total_materiales = Material.objects.count()

    total_solicitudes = Solicitud.objects.count()

    solicitudes_pendientes = Solicitud.objects.filter(
        estado='PENDIENTE'
    ).count()

    total_movimientos = MovimientoInventario.objects.count()
    movimientos_recientes = MovimientoInventario.objects.all().order_by('-id')[:5]

    return render(
        request,
        'dashboard/index.html',
        {
            'materiales_bajos': materiales_bajos,

            'total_materiales': total_materiales,

            'total_solicitudes': total_solicitudes,

            'solicitudes_pendientes': solicitudes_pendientes,

            'total_movimientos': total_movimientos,
            'movimientos_recientes': movimientos_recientes
        }
    )