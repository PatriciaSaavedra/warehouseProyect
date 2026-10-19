from datetime import date, timedelta
from django.db.models import F, Q, Sum
from inventario.models import Almacen, InventarioAlmacen, MovimientoInventario
from solicitudes.models import Solicitud
from compras.models import ProcesoAdquisicion
from inventario.views import obtener_almacenes_usuario


def theme_processor(request):
    """Inyecta la preferencia de tema (claro u oscuro)."""
    return {
        'theme': request.COOKIES.get('theme', 'light')
    }


def almacen_context(request):
    """
    Inyecta el almacén activo y el MOTOR DE NOTIFICACIONES INTELIGENTES (TARJETA 6)
    contextualizado según el rol del usuario logueado.
    """
    if not request.user.is_authenticated:
        return {}

    perfil = getattr(request.user, 'perfilusuario', None)
    if not perfil:
        return {}

    rol = perfil.rol
    almacen_usuario = None
    almacenes_disponibles = []
    es_admin_almacenes = rol in ['ADMINISTRADOR', 'ADMIN_ALMACENES'] or request.user.is_superuser

    # 1. Resolución de Almacén del Usuario
    if es_admin_almacenes:
        almacenes_disponibles = Almacen.objects.filter(is_active=True).order_by('nombre')
        almacen_usuario = almacenes_disponibles.filter(tipo='CENTRAL').first() or almacenes_disponibles.first()
    else:
        almacenes_autorizados = perfil.almacenes_autorizados.filter(is_active=True).order_by('nombre')
        if almacenes_autorizados.exists():
            almacenes_disponibles = almacenes_autorizados
            almacen_usuario = almacenes_autorizados.first()
        else:
            almacen_a_cargo = Almacen.objects.filter(responsable=request.user, is_active=True).first()
            if almacen_a_cargo:
                almacen_usuario = almacen_a_cargo
                almacenes_disponibles = [almacen_a_cargo]

    # =========================================================================
    # 2. MOTOR DE NOTIFICACIONES INTELIGENTES (MÁXIMO 5 DE ALTA PRIORIDAD)
    # =========================================================================
    notificaciones = []
    hoy = date.today()

    try:
        # A. NOTIFICACIONES PARA UNIDAD SOLICITANTE
        if rol == 'UNIDAD_SOLICITANTE':
            # Pedidos listos para retirar en ventanilla
            pedidos_listos = Solicitud.objects.filter(
                solicitante=request.user,
                estado='PREPARADA'
            ).order_by('-fecha_preparado')[:3]

            for p in pedidos_listos:
                notificaciones.append({
                    'titulo': f'Pedido Listo: {p.codigo}',
                    'mensaje': 'Tu material está preparado en ventanilla de almacén listo para retiro.',
                    'icono': 'inventory_2',
                    'color_bg': 'bg-emerald-50 text-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-300 border-emerald-200',
                    'url': f'/solicitudes/{p.id}/',
                    'tiempo': p.fecha_preparado.strftime('%d/%m %H:%M') if p.fecha_preparado else 'Listo',
                })

            # Pedidos rechazados recientes
            pedidos_rechazados = Solicitud.objects.filter(
                solicitante=request.user,
                estado='RECHAZADA'
            ).order_by('-id')[:2]

            for pr in pedidos_rechazados:
                notificaciones.append({
                    'titulo': f'Solicitud Observada: {pr.codigo}',
                    'mensaje': f'Motivo: {pr.motivo_rechazo or "Rechazado por autoridad"}',
                    'icono': 'cancel',
                    'color_bg': 'bg-red-50 text-red-800 dark:bg-red-950/60 dark:text-red-300 border-red-200',
                    'url': f'/solicitudes/{pr.id}/',
                    'tiempo': 'Observado',
                })

        # B. NOTIFICACIONES PARA ALMACENERO Y KARDISTA
        elif rol in ['ALMACENERO', 'KARDISTA', 'ADMINISTRADOR', 'ADMIN_ALMACENES']:
            # Almacén de jurisdicción
            alms_scope = almacenes_disponibles

            # 1. Pedidos pendientes de despacho en ventanilla
            pedidos_ventanilla = Solicitud.objects.filter(
                estado='PREPARADA'
            )
            if not es_admin_almacenes and almacen_usuario and almacen_usuario.tipo != 'CENTRAL':
                unidades_sub = set(almacen_usuario.unidades_atendidas.values_list('id', flat=True))
                if almacen_usuario.unidad_organizacional_id:
                    unidades_sub.add(almacen_usuario.unidad_organizacional_id)
                pedidos_ventanilla = pedidos_ventanilla.filter(unidad_solicitante_id__in=unidades_sub)

            for pv in pedidos_ventanilla[:3]:
                notificaciones.append({
                    'titulo': f'Ventanilla: {pv.codigo}',
                    'mensaje': f'{pv.unidad_solicitante.nombre}: Listo para entrega física.',
                    'icono': 'output',
                    'color_bg': 'bg-amber-50 text-amber-800 dark:bg-amber-950/60 dark:text-amber-300 border-amber-200',
                    'url': f'/solicitudes/entregar/{pv.id}/' if rol != 'KARDISTA' else f'/solicitudes/{pv.id}/',
                    'tiempo': 'En espera',
                })

            # 2. SEMÁFORO DE STOCK: Alertas Críticas (Rojo) y Preventivas (Amarillo)
            items_en_almacen = InventarioAlmacen.objects.filter(
                almacen__in=alms_scope,
                stock_fisico__gt=0
            ).select_related('material')

            # A. Rojo: Crítico (Menor o igual al mínimo)
            items_criticos = [
                inv for inv in items_en_almacen
                if inv.stock_fisico <= inv.material.stock_minimo
            ][:2]

            for ic in items_criticos:
                notificaciones.append({
                    'titulo': '🔴 Stock Crítico',
                    'mensaje': f'{ic.material.nombre}: Quedan solo {ic.stock_fisico} u. (Mínimo: {ic.material.stock_minimo})',
                    'icono': 'error',
                    'color_bg': 'bg-red-50 text-red-800 dark:bg-red-950/60 dark:text-red-300 border-red-200',
                    'url': f'/inventario/kardex/{ic.material_id}/',
                    'tiempo': 'Urgente',
                })

            # B. Amarillo: Punto de Reorden (Entre mínimo y medio)
            items_reorden = [
                inv for inv in items_en_almacen
                if inv.material.stock_minimo < inv.stock_fisico <= getattr(inv.material, 'stock_medio', 15)
            ][:2]

            for ir in items_reorden:
                notificaciones.append({
                    'titulo': '🟡 Punto de Reorden',
                    'mensaje': f'{ir.material.nombre}: Quedan {ir.stock_fisico} u. Planificar compra antes del mínimo.',
                    'icono': 'warning',
                    'color_bg': 'bg-amber-50 text-amber-800 dark:bg-amber-950/60 dark:text-amber-300 border-amber-200',
                    'url': f'/inventario/kardex/{ir.material_id}/',
                    'tiempo': 'Preventivo',
                })

        # C. NOTIFICACIONES PARA AUTORIDADES SABS (Fase correspondiente)
        elif rol == 'PRESUPUESTOS':
            pendientes_cert = ProcesoAdquisicion.objects.filter(estado='NOTA_SAF_EMITIDA')[:4]
            for pc in pendientes_cert:
                notificaciones.append({
                    'titulo': f'Certificación Pendiente: {pc.codigo}',
                    'mensaje': f'{pc.unidad_solicitante.nombre}: {pc.objeto_contratacion[:40]}...',
                    'icono': 'account_balance_wallet',
                    'color_bg': 'bg-blue-50 text-blue-800 dark:bg-blue-950/60 dark:text-blue-300 border-blue-200',
                    'url': f'/compras/adquisiciones/{pc.id}/',
                    'tiempo': 'Urgente',
                })

        elif rol == 'RPA':
            pendientes_rpa = ProcesoAdquisicion.objects.filter(estado='CERTIFICADA')[:4]
            for pr in pendientes_rpa:
                notificaciones.append({
                    'titulo': f'Aprobación RPA: {pr.codigo}',
                    'mensaje': f'Certificado por Bs. {pr.monto_certificado_bs:.2f}. Espera resolución de inicio.',
                    'icono': 'gavel',
                    'color_bg': 'bg-purple-50 text-purple-800 dark:bg-purple-950/60 dark:text-purple-300 border-purple-200',
                    'url': f'/compras/adquisiciones/{pr.id}/',
                    'tiempo': 'Firma RPA',
                })

        elif rol == 'BIENES_SERVICIOS':
            pendientes_orden = ProcesoAdquisicion.objects.filter(estado='APROBADA_RPA')[:4]
            for po in pendientes_orden:
                notificaciones.append({
                    'titulo': f'Emitir Orden: {po.codigo}',
                    'mensaje': f'Autorizado por RPA. Pendiente adjudicación y Orden de Compra.',
                    'icono': 'shopping_bag',
                    'color_bg': 'bg-emerald-50 text-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-300 border-emerald-200',
                    'url': f'/compras/adquisiciones/{po.id}/',
                    'tiempo': 'Adjudicar',
                })

    except Exception:
        notificaciones = []

    return {
        'almacen_usuario': almacen_usuario,
        'almacenes_disponibles_global': almacenes_disponibles,
        'tiene_multiples_almacenes': len(almacenes_disponibles) > 1,
        'es_admin_almacenes': es_admin_almacenes,
        # Variables de la Campana Interactiva
        'notificaciones_usuario': notificaciones[:5],
        'conteo_notificaciones': len(notificaciones[:5]),
    }