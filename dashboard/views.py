from decimal import Decimal
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.db import models
from django.db.models import F, Q

from inventario.models import Material, MovimientoInventario, InventarioAlmacen, Almacen
from solicitudes.models import Solicitud
from inventario.services import obtener_alertas_tempranas  # <-- IMPORT DEL MOTOR DE ALERTAS


@login_required
def dashboard_view(request):
    """
    Panel de Control contextualizado con Centro de Alertas Tempranas SABS:
    - Administrador / SAF: Métricas macro y alertas de toda la Gobernación.
    - Almacenero Seccional (Pedro en UNASBA): Métricas y alertas aisladas de su depósito.
    - Unidad Solicitante (Sebastián en Archivo): Resumen y alertas de su oficina.
    """
    perfil = getattr(request.user, 'perfilusuario', None)
    rol = perfil.rol if perfil else 'UNIDAD_SOLICITANTE'

    # 1. Determinar el alcance del usuario
    es_admin_global = (
        rol in ['ADMINISTRADOR', 'ADMIN_ALMACENES', 'SECRETARIO_SAF', 'PRESUPUESTOS'] or 
        request.user.is_superuser
    )

    # 2. Obtener las alertas del motor centralizado (Tarjeta 7)
    alertas = obtener_alertas_tempranas(request.user)

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

        items_almacen = InventarioAlmacen.objects.filter(
            almacen__in=almacenes_user
        ).select_related('material', 'material__unidad_medida_fk')

        ids_bajos = [
            inv.material_id for inv in items_almacen 
            if inv.stock_fisico <= inv.material.stock_minimo
        ]
        materiales_bajos = Material.objects.filter(id__in=ids_bajos)[:5]

        total_materiales = items_almacen.filter(stock_fisico__gt=0).values('material_id').distinct().count()

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

        # Contextualizar alertas de almacén: solo mostrar agotados que afecten sus depósitos
        ids_almacen = list(almacenes_user.values_list('id', flat=True))
        alertas['materiales_agotados'] = alertas['materiales_agotados'].filter(
            inventarios_almacen__almacen_id__in=ids_almacen
        ).distinct()

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

        # Contextualizar alertas para el solicitante: solo las alertas de su propia oficina
        if perfil and perfil.unidad:
            alertas['poas_criticos'] = [p for p in alertas['poas_criticos'] if p['poa'].unidad == perfil.unidad]
            alertas['cuotas_criticas'] = [c for c in alertas['cuotas_criticas'] if c['unidad'] == perfil.unidad]
            alertas['materiales_vencidos'] = Material.objects.none()
            alertas['materiales_por_vencer'] = Material.objects.none()
            alertas['total_criticas'] = len([p for p in alertas['poas_criticos'] if p['es_cero']]) + len([c for c in alertas['cuotas_criticas'] if c['agotado']])
            alertas['total_advertencias'] = len([p for p in alertas['poas_criticos'] if not p['es_cero']]) + len([c for c in alertas['cuotas_criticas'] if not c['agotado']])

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
            'alertas': alertas,  # <-- Inyectado en el context para el bloque de semáforos
            'rol': rol,
        }
    )