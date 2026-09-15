from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.db import models
from django.db.models import F, Q

from inventario.models import Material, MovimientoInventario, InventarioAlmacen, Almacen
from solicitudes.models import Solicitud


@login_required
def dashboard_view(request):
    """
    Panel de Control contextualizado:
    - Administrador / Admin de Almacenes: Métricas macro de toda la Gobernación.
    - Almacenero Seccional (Pedro en UNASBA): Métricas aisladas de su depósito y despachos locales.
    - Unidad Solicitante (Sebastián en Archivo): Resumen de sus requerimientos personales.
    """
    perfil = getattr(request.user, 'perfilusuario', None)
    rol = perfil.rol if perfil else 'UNIDAD_SOLICITANTE'

    # 1. Determinar el alcance del usuario
    es_admin_global = (
        rol in ['ADMINISTRADOR', 'ADMIN_ALMACENES', 'SECRETARIO_SAF'] or 
        request.user.is_superuser
    )

    if es_admin_global:
        # ========================================================
        # A. VISIÓN GLOBAL INSTITUCIONAL
        # ========================================================
        materiales_bajos = Material.objects.filter(
            is_active=True,
            stock_actual__lte=F('stock_minimo')
        ).select_related('partida', 'unidad_medida_fk')[:5]

        total_materiales = Material.objects.filter(is_active=True).count()
        total_solicitudes = Solicitud.objects.count()

        # CORREGIDO: En tu modelo el estado inicial real es 'REGISTRADA'
        solicitudes_pendientes = Solicitud.objects.filter(estado='REGISTRADA').count()

        total_movimientos = MovimientoInventario.objects.count()
        movimientos_recientes = MovimientoInventario.objects.select_related(
            'material', 'almacen', 'usuario'
        ).order_by('-id')[:5]

    elif rol in ['ALMACENERO', 'KARDISTA']:
        # ========================================================
        # B. VISIÓN SECCIONAL AISLADA (Pedro en UNASBA)
        # ========================================================
        almacenes_user = Almacen.objects.filter(
            Q(responsable=request.user) | 
            Q(id__in=perfil.almacenes_autorizados.all()) |
            Q(unidad_organizacional=perfil.unidad),
            is_active=True
        ).distinct()

        # Existencias físicas del almacén asignado
        items_almacen = InventarioAlmacen.objects.filter(
            almacen__in=almacenes_user
        ).select_related('material', 'material__unidad_medida_fk')

        # Alerta: materiales en bajo stock físico en su depósito
        ids_bajos = [
            inv.material_id for inv in items_almacen 
            if inv.stock_fisico <= inv.material.stock_minimo
        ]
        materiales_bajos = Material.objects.filter(id__in=ids_bajos)[:5]

        total_materiales = items_almacen.filter(stock_fisico__gt=0).values('material_id').distinct().count()

        # Solicitudes que deben ser atendidas por su almacén
        unidades_atendidas_ids = almacenes_user.values_list('unidades_atendidas', flat=True)
        solicitudes_almacen = Solicitud.objects.filter(
            Q(unidad_solicitante__in=unidades_atendidas_ids) |
            Q(unidad_solicitante__in=almacenes_user.values_list('unidad_organizacional', flat=True))
        )
        total_solicitudes = solicitudes_almacen.count()
        solicitudes_pendientes = solicitudes_almacen.filter(estado='REGISTRADA').count()

        total_movimientos = MovimientoInventario.objects.filter(almacen__in=almacenes_user).count()
        movimientos_recientes = MovimientoInventario.objects.filter(
            almacen__in=almacenes_user
        ).select_related('material', 'almacen', 'usuario').order_by('-id')[:5]

    else:
        # ========================================================
        # C. VISIÓN DE UNIDAD SOLICITANTE (Oficinas)
        # ========================================================
        materiales_bajos = Material.objects.none()
        total_materiales = Material.objects.filter(is_active=True).count()

        solicitudes_propias = Solicitud.objects.filter(solicitante=request.user)
        total_solicitudes = solicitudes_propias.count()
        solicitudes_pendientes = solicitudes_propias.filter(estado='REGISTRADA').count()

        total_movimientos = 0
        movimientos_recientes = []

    return render(
        request,
        'dashboard/index.html',
        {
            'materiales_bajos': materiales_bajos,
            'total_materiales': total_materiales,
            'total_solicitudes': total_solicitudes,
            'solicitudes_pendientes': solicitudes_pendientes,
            'total_movimientos': total_movimientos,
            'movimientos_recientes': movimientos_recientes,
            'rol': rol,
        }
    )