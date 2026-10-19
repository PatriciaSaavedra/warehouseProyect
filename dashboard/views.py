from decimal import Decimal
from datetime import date, timedelta
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.db.models import F, Q, Sum
from django.utils import timezone

from inventario.models import Material, MovimientoInventario, InventarioAlmacen, Almacen, AsignacionEntradaUnidad
from solicitudes.models import Solicitud
from compras.models import ProcesoAdquisicion
from inventario.views import obtener_almacenes_usuario


@login_required
def dashboard_view(request):
    """
    TARJETA 5: Centro de Mando / Dashboard Contextualizado por Rol:
    - UNIDAD_SOLICITANTE: Sus materiales asignados, saldo de cupos y seguimiento de trámites.
    - ALMACENERO / KARDISTA: Existencias en su depósito, stock crítico, lotes por vencer y cola de ventanilla.
    - AUTORIDADES (RPA, SAF, Presupuestos, BBSS): Trámites que esperan su acción inmediata.
    - ADMINISTRADOR: Vista consolidada departamental.
    """
    perfil = getattr(request.user, 'perfilusuario', None)
    rol = perfil.rol if perfil else 'UNIDAD_SOLICITANTE'
    hoy = timezone.now().date()

    es_autoridad_sabs = rol in ['RPA', 'SECRETARIO_SAF', 'PRESUPUESTOS', 'BIENES_SERVICIOS', 'JEFE_ADMINISTRATIVO']
    es_almacen_o_admin = rol in ['ALMACENERO', 'KARDISTA', 'ADMINISTRADOR', 'ADMIN_ALMACENES'] or request.user.is_superuser

    context = {
        'rol': rol,
        'hoy': hoy,
    }

    # =========================================================================
    # CASO 1: UNIDAD SOLICITANTE (Oficinas de la Gobernación)
    # =========================================================================
    if rol == 'UNIDAD_SOLICITANTE':
        unidad_user = perfil.unidad if perfil else None

        # 1. Materiales asignados físicamente a esta oficina en compras
        asignaciones_oficina = []
        if unidad_user:
            asigs_qs = AsignacionEntradaUnidad.objects.filter(
                unidad_organizacional=unidad_user,
                nota_ingreso_detalle__material__is_active=True
            ).select_related(
                'nota_ingreso_detalle__material',
                'nota_ingreso_detalle__material__unidad_medida_fk'
            )

            # Agrupar por material para sumar lotes
            materiales_agrupados = {}
            for a in asigs_qs:
                mat = a.nota_ingreso_detalle.material
                if mat.id not in materiales_agrupados:
                    u_med = mat.unidad_medida_fk.codigo if mat.unidad_medida_fk else mat.unidad_medida
                    materiales_agrupados[mat.id] = {
                        'id': mat.id,
                        'codigo': mat.codigo,
                        'nombre': mat.nombre,
                        'unidad_medida': u_med,
                        'total_asignado': 0,
                        'total_retirado': 0,
                        'saldo_disponible': 0,
                    }
                materiales_agrupados[mat.id]['total_asignado'] += a.cantidad_asignada
                materiales_agrupados[mat.id]['total_retirado'] += a.cantidad_retirada
                materiales_agrupados[mat.id]['saldo_disponible'] += a.saldo_disponible

            asignaciones_oficina = [m for m in materiales_agrupados.values() if m['saldo_disponible'] > 0]

        # 2. Seguimiento de trámites propios
        pedidos_propios = Solicitud.objects.filter(solicitante=request.user).order_by('-id')[:5]
        adquisiciones_propias = ProcesoAdquisicion.objects.filter(solicitante=request.user).order_by('-id')[:5]

        # 3. KPIs de la oficina
        total_asignados = len(asignaciones_oficina)
        tramites_activos = (
            Solicitud.objects.filter(solicitante=request.user).exclude(estado__in=['ENTREGADA', 'CERRADA', 'RECHAZADA']).count() +
            ProcesoAdquisicion.objects.filter(solicitante=request.user).exclude(estado__in=['CONCLUIDA', 'RECHAZADA']).count()
        )
        tramites_completados = (
            Solicitud.objects.filter(solicitante=request.user, estado__in=['ENTREGADA', 'CERRADA']).count() +
            ProcesoAdquisicion.objects.filter(solicitante=request.user, estado='CONCLUIDA').count()
        )

        context.update({
            'unidad_user': unidad_user,
            'asignaciones_oficina': asignaciones_oficina,
            'pedidos_propios': pedidos_propios,
            'adquisiciones_propias': adquisiciones_propias,
            'total_asignados': total_asignados,
            'tramites_activos': tramites_activos,
            'tramites_completados': tramites_completados,
        })

    # =========================================================================
    # CASO 2: ALMACENERO / KARDISTA / ADMINISTRADOR
    # =========================================================================
    elif es_almacen_o_admin:
        almacenes_user = obtener_almacenes_usuario(request.user)
        es_central_o_admin = almacenes_user.filter(tipo='CENTRAL').exists() or rol in ['ADMINISTRADOR', 'ADMIN_ALMACENES']
        almacen_principal = almacenes_user.first()

        # 1. Existencias e ítems en almacén
        if rol in ['ADMINISTRADOR', 'ADMIN_ALMACENES']:
            inventario_items = InventarioAlmacen.objects.filter(stock_fisico__gt=0)
            movimientos_base = MovimientoInventario.objects.all()
            pedidos_almacen = Solicitud.objects.all()
        else:
            inventario_items = InventarioAlmacen.objects.filter(almacen__in=almacenes_user, stock_fisico__gt=0)
            movimientos_base = MovimientoInventario.objects.filter(almacen__in=almacenes_user)
            
            # Subalmacén: solo pedidos de sus unidades atendidas
            unidades_subalmacen = set(almacen_principal.unidades_atendidas.values_list('id', flat=True)) if almacen_principal else set()
            if almacen_principal and almacen_principal.unidad_organizacional_id:
                unidades_subalmacen.add(almacen_principal.unidad_organizacional_id)
            pedidos_almacen = Solicitud.objects.filter(unidad_solicitante_id__in=unidades_subalmacen)

        total_materiales = inventario_items.values('material_id').distinct().count()
        total_stock_fisico = inventario_items.aggregate(t=Sum('stock_fisico'))['t'] or 0

# 2. Semáforo de Stock: Críticos (Rojo) y Punto de Reorden (Amarillo)
        items_con_material = list(inventario_items.select_related('material', 'material__partida', 'material__unidad_medida_fk'))

        materiales_criticos = []
        materiales_reorden = []

        for inv in items_con_material:
            mat = inv.material
            stock_med = getattr(mat, 'stock_medio', 15)
            
            if inv.stock_fisico <= mat.stock_minimo:
                materiales_criticos.append({
                    'material': mat,
                    'stock_fisico': inv.stock_fisico,
                    'stock_minimo': mat.stock_minimo,
                    'almacen': inv.almacen
                })
            elif mat.stock_minimo < inv.stock_fisico <= stock_med:
                materiales_reorden.append({
                    'material': mat,
                    'stock_fisico': inv.stock_fisico,
                    'stock_medio': stock_med,
                    'stock_minimo': mat.stock_minimo,
                    'almacen': inv.almacen
                })

        materiales_bajos_count = len(materiales_criticos) + len(materiales_reorden)
        # 3. Alertas de Lotes Perecederos (< 30 días o vencidos)
        fecha_umbral = hoy + timedelta(days=30)
        lotes_activos = movimientos_base.filter(
            tipo='ENTRADA',
            saldo_disponible_lote__gt=0,
            fecha_vencimiento__isnull=False
        ).select_related('material')

        lotes_vencidos = lotes_activos.filter(fecha_vencimiento__lt=hoy)[:5]
        lotes_por_vencer = lotes_activos.filter(fecha_vencimiento__gte=hoy, fecha_vencimiento__lte=fecha_umbral)[:5]

        # 4. Cola de Ventanilla: Pedidos listos para despacho físico
        pedidos_por_entregar = pedidos_almacen.filter(
            estado='PREPARADA'
        ).select_related('solicitante', 'unidad_solicitante').order_by('-fecha_preparado')[:6]

        pedidos_pendientes_preparar = pedidos_almacen.filter(
            estado='REVISADA'
        ).count()

        # 5. Gráfica de Entradas vs Salidas del período
        total_entradas = movimientos_base.filter(tipo='ENTRADA', fecha__year=hoy.year).aggregate(t=Sum('cantidad'))['t'] or 0
        total_salidas = movimientos_base.filter(tipo='SALIDA', fecha__year=hoy.year).aggregate(t=Sum('cantidad'))['t'] or 0

        movimientos_recientes = movimientos_base.select_related('material', 'almacen', 'usuario').order_by('-id')[:6]

        context.update({
            'almacen_principal': almacen_principal,
            'es_central_o_admin': es_central_o_admin,
            'total_materiales': total_materiales,
            'total_stock_fisico': total_stock_fisico,
            'materiales_criticos': materiales_criticos[:5],
            'materiales_reorden': materiales_reorden[:5],
            'materiales_bajos_count': materiales_bajos_count,
            'lotes_vencidos': lotes_vencidos,
            'lotes_por_vencer': lotes_por_vencer,
            'pedidos_por_entregar': pedidos_por_entregar,
            'pedidos_pendientes_preparar': pedidos_pendientes_preparar,
            'total_entradas': total_entradas,
            'total_salidas': total_salidas,
            'movimientos_recientes': movimientos_recientes,
        })

    # =========================================================================
    # CASO 3: AUTORIDADES SABS (RPA, SAF, Presupuestos, Bienes y Servicios)
    # =========================================================================
    if es_autoridad_sabs:
        # Trámites urgentes según la fase de competencia del rol
        if rol == 'RPA':
            tramites_urgentes = ProcesoAdquisicion.objects.filter(estado='CERTIFICADA').order_by('-id')[:8]
            accion_requerida = "Aprobación de Inicio RPA (Resolución)"
        elif rol == 'PRESUPUESTOS':
            tramites_urgentes = ProcesoAdquisicion.objects.filter(estado='NOTA_SAF_EMITIDA').order_by('-id')[:8]
            accion_requerida = "Certificación Presupuestaria de Partida"
        elif rol == 'SECRETARIO_SAF':
            tramites_urgentes = ProcesoAdquisicion.objects.filter(estado='CHECKLIST_VERIFICADO').order_by('-id')[:8]
            accion_requerida = "Autorización y Emisión de Nota a Presupuestos"
        elif rol == 'BIENES_SERVICIOS':
            tramites_urgentes = ProcesoAdquisicion.objects.filter(estado='APROBADA_RPA').order_by('-id')[:8]
            accion_requerida = "Emisión de Orden de Compra / Servicio"
        else:
            tramites_urgentes = ProcesoAdquisicion.objects.filter(estado__in=['CHECKLIST_VERIFICADO', 'CERTIFICADA', 'APROBADA_RPA']).order_by('-id')[:8]
            accion_requerida = "Revisión Administrativa"

        # KPIs globales de contrataciones del año
        adquisiciones_año = ProcesoAdquisicion.objects.filter(fecha_solicitud__year=hoy.year)
        total_adquisiciones = adquisiciones_año.count()
        en_curso = adquisiciones_año.exclude(estado__in=['CONCLUIDA', 'RECHAZADA']).count()
        concluidas = adquisiciones_año.filter(estado='CONCLUIDA').count()

        context.update({
            'tramites_urgentes': tramites_urgentes,
            'accion_requerida': accion_requerida,
            'total_adquisiciones': total_adquisiciones,
            'en_curso': en_curso,
            'concluidas': concluidas,
        })

    return render(request, 'dashboard/index.html', context)