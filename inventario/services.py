from datetime import date, timedelta
from decimal import Decimal
from django.db import transaction
from django.db.models import F
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import (
    Material, 
    MovimientoInventario, 
    InventarioAlmacen, 
    Almacen,
    AsignacionEntradaUnidad,
    AsignacionMaterialUnidad
)

# ========================================================
# 1. MOTOR DE CÁLCULO Y DESGLOSE PEPS (EN TIEMPO REAL)
# ========================================================

def calcular_desglose_peps(material, almacen, cantidad_solicitada):
    """
    Simula en tiempo real cómo se cubrirá el pedido según PEPS sin alterar la BD.
    Permite visualizar en pantalla exactamente qué lotes se consumirán, 
    sus cantidades y sus precios unitarios (ej. 2 de 30 Bs. y 1 de 45 Bs. = 105 Bs.).
    """
    if not material or not almacen or cantidad_solicitada <= 0:
        return {
            'desglose_lotes': [],
            'subtotal_real': Decimal('0.00'),
            'costo_unitario_ponderado': Decimal('0.00'),
            'cantidad_cubierta': 0,
            'cantidad_faltante': cantidad_solicitada,
            'alcanza_stock': False,
        }

    # Lotes de entrada con saldo físico en estantería ordenados por FIFO (fecha e id ascendente)
    lotes_disponibles = MovimientoInventario.objects.filter(
        material=material,
        almacen=almacen,
        tipo='ENTRADA',
        saldo_disponible_lote__gt=0
    ).order_by('fecha', 'id')

    cantidad_pendiente = cantidad_solicitada
    desglose_lotes = []
    subtotal_real = Decimal('0.00')

    for lote in lotes_disponibles:
        if cantidad_pendiente <= 0:
            break

        disponible = lote.saldo_disponible_lote
        a_tomar = min(cantidad_pendiente, disponible)
        costo_capa = Decimal(str(a_tomar)) * lote.costo_unitario

        # Referencia limpia de la Nota de Ingreso (ej. NI-00164)
        ref_limpia = lote.referencia.replace("INGRESO NOTA ", "").replace("INGRESO: ", "").strip()

        desglose_lotes.append({
            'lote_id': lote.id,
            'referencia': ref_limpia or f"Lote #{lote.id}",
            'cantidad': a_tomar,
            'costo_unitario': lote.costo_unitario,
            'subtotal': costo_capa,
            'fecha_ingreso': lote.fecha,
        })

        subtotal_real += costo_capa
        cantidad_pendiente -= a_tomar

    cantidad_cubierta = cantidad_solicitada - cantidad_pendiente
    costo_ponderado = (subtotal_real / Decimal(str(cantidad_cubierta))) if cantidad_cubierta > 0 else Decimal('0.00')

    return {
        'desglose_lotes': desglose_lotes,
        'subtotal_real': subtotal_real,
        'costo_unitario_ponderado': costo_ponderado,
        'cantidad_cubierta': cantidad_cubierta,
        'cantidad_faltante': max(0, cantidad_pendiente),
        'alcanza_stock': (cantidad_pendiente == 0),
    }


def registrar_salida_valorada_peps(material, almacen, cantidad_salida, tipo_movimiento, referencia, usuario, unidad_destino=None, descontar_reserva=False):
    """
    Descuenta stock físico aplicando el método PEPS (FIFO) de manera aislada por Almacén.
    Agota cronológicamente los lotes de ENTRADA en el almacén especificado con saldo disponible y calcula el costo real.
    Soporta consumo multicapa (ej. 2 de Lote 1 a 30 Bs. y 1 de Lote 2 a 45 Bs. = 105 Bs.).
    """
    if cantidad_salida <= 0:
        raise ValueError("La cantidad de salida debe ser estrictamente mayor a cero.")

    with transaction.atomic():
        try:
            inventario_almacen = InventarioAlmacen.objects.select_for_update().get(material=material, almacen=almacen)
        except InventarioAlmacen.DoesNotExist:
            raise ValueError(f"No existe un registro de inventario para {material.nombre} en el almacén {almacen.nombre}.")

        if cantidad_salida > inventario_almacen.stock_fisico:
            raise ValueError(
                f"No existe suficiente stock físico disponible en el almacén {almacen.nombre} "
                f"({inventario_almacen.stock_fisico} disponibles, solicitado: {cantidad_salida})."
            )

        # Lotes de entrada más antiguos con saldo disponible (PEPS)
        lotes_disponibles = MovimientoInventario.objects.select_for_update().filter(
            material=material,
            almacen=almacen,
            tipo='ENTRADA',
            saldo_disponible_lote__gt=0
        ).order_by('fecha', 'id')

        cantidad_restante = cantidad_salida
        costo_total_egreso = Decimal('0.00')
        desglose_lotes = []

        for lote in lotes_disponibles:
            if cantidad_restante <= 0:
                break

            if lote.saldo_disponible_lote >= cantidad_restante:
                subtotal_capa = Decimal(cantidad_restante) * lote.costo_unitario
                costo_total_egreso += subtotal_capa
                lote.saldo_disponible_lote -= cantidad_restante
                lote.save()
                
                desglose_lotes.append({
                    'lote_id': lote.id,
                    'fecha_ingreso': lote.fecha,
                    'cantidad': cantidad_restante,
                    'costo_unitario': lote.costo_unitario,
                    'subtotal': subtotal_capa
                })
                cantidad_restante = 0
            else:
                cant_tomada = lote.saldo_disponible_lote
                subtotal_capa = Decimal(cant_tomada) * lote.costo_unitario
                costo_total_egreso += subtotal_capa
                cantidad_restante -= cant_tomada
                lote.saldo_disponible_lote = 0
                lote.save()
                
                desglose_lotes.append({
                    'lote_id': lote.id,
                    'fecha_ingreso': lote.fecha,
                    'cantidad': cant_tomada,
                    'costo_unitario': lote.costo_unitario,
                    'subtotal': subtotal_capa
                })

        if cantidad_restante > 0:
            raise ValueError("Inconsistencia en el inventario: La suma de lotes valorados PEPS es menor al stock físico.")

        # Actualizar stock en el inventario del almacén
        stock_anterior = inventario_almacen.stock_fisico
        inventario_almacen.stock_fisico -= cantidad_salida
        
        if descontar_reserva:
            if inventario_almacen.stock_reservado >= cantidad_salida:
                inventario_almacen.stock_reservado -= cantidad_salida
            else:
                inventario_almacen.stock_reservado = 0

        inventario_almacen.save()

        costo_unitario_ponderado = costo_total_egreso / Decimal(cantidad_salida)

        # Si hubo consumo de múltiples capas con precios diferentes, agregar detalle a la referencia
        ref_final = referencia
        if len(desglose_lotes) > 1:
            resumen_peps = " + ".join([f"{d['cantidad']}u x Bs.{d['costo_unitario']:.2f}" for d in desglose_lotes])
            ref_final = f"{referencia} [PEPS: {resumen_peps}]"

        movimiento = MovimientoInventario.objects.create(
            material=material,
            almacen=almacen,
            tipo=tipo_movimiento,
            cantidad=cantidad_salida,
            costo_unitario=costo_unitario_ponderado,
            costo_total=costo_total_egreso,
            stock_anterior=stock_anterior,
            stock_resultante=inventario_almacen.stock_fisico,
            referencia=ref_final[:100],
            usuario=usuario,
            unidad_destino=unidad_destino,
        )

        movimiento.desglose_peps = desglose_lotes

    return movimiento


# ========================================================
# 2. CONSULTAS DE INVENTARIO Y ALMACENES
# ========================================================

def obtener_inventario_por_almacen(almacen):
    return InventarioAlmacen.objects.filter(almacen=almacen).select_related('material')


def obtener_inventario_para_unidad(unidad_organizacional):
    almacenes_autorizados = Almacen.objects.filter(unidades_atendidas=unidad_organizacional)
    return InventarioAlmacen.objects.filter(
        almacen__in=almacenes_autorizados
    ).select_related('almacen', 'material').distinct()


# ========================================================
# 3. VALIDACIONES DE SEGURIDAD Y PERMISOS
# ========================================================

def validar_operacion_almacen(usuario, almacen):
    if not usuario.is_authenticated:
        raise ValidationError("Debe iniciar sesión para operar el inventario.")

    if usuario.is_superuser:
        return True

    if almacen.responsable == usuario:
        return True

    if hasattr(usuario, 'perfilusuario'):
        if not usuario.perfilusuario.tiene_acceso_almacen(almacen):
            raise ValidationError(
                f"No tienes autorización para procesar operaciones en el almacén: {almacen.nombre}."
            )
    else:
        raise ValidationError("Tu usuario no tiene un perfil de roles asignado en el sistema.")
    
    return True


# ========================================================
# 4. MOTOR DE ALERTAS TEMPRANAS SABS (SIN POAS)
# ========================================================

def obtener_alertas_tempranas(usuario=None):
    """
    Alertas operativas de Almacén basadas en existencias reales y asignaciones directas.
    (Completamente independiente de POAs).
    """
    hoy = timezone.now().date()
    en_30_dias = hoy + timedelta(days=30)

    # 1. ALERTAS DE VENCIMIENTO REALES POR LOTE EN ESTANTERÍA
    lotes_vencidos = MovimientoInventario.objects.filter(
        tipo='ENTRADA',
        saldo_disponible_lote__gt=0,
        fecha_vencimiento__isnull=False,
        fecha_vencimiento__lt=hoy
    ).select_related('material', 'almacen', 'material__unidad_medida_fk').order_by('fecha_vencimiento')

    lotes_por_vencer = MovimientoInventario.objects.filter(
        tipo='ENTRADA',
        saldo_disponible_lote__gt=0,
        fecha_vencimiento__isnull=False,
        fecha_vencimiento__gte=hoy,
        fecha_vencimiento__lte=en_30_dias
    ).select_related('material', 'almacen', 'material__unidad_medida_fk').order_by('fecha_vencimiento')

    # 2. ALERTAS DE STOCK CRÍTICO / AGOTADO
    materiales_agotados = Material.objects.filter(
        is_active=True,
        stock_actual=0
    ).select_related('partida', 'unidad_medida_fk').order_by('partida__codigo', 'nombre')

    materiales_bajo_stock = (
        Material.objects.filter(
            is_active=True, stock_actual__gt=0, stock_actual__lte=F('stock_minimo')
        )
        .select_related('partida', 'unidad_medida_fk')
        .order_by('stock_actual')
    )

    # 3. ALERTAS DE CUOTAS POR ASIGNACIÓN DIRECTA (> 85% consumido por oficinas)
    cuotas_criticas = []
    asigs_directas = AsignacionEntradaUnidad.objects.filter(
        cantidad_asignada__gt=0
    ).select_related('unidad_organizacional', 'nota_ingreso_detalle__material')

    for asig in asigs_directas:
        pct_consumido = (asig.cantidad_retirada / asig.cantidad_asignada) * 100
        if pct_consumido >= 85:
            cuotas_criticas.append({
                'item': asig,
                'unidad': asig.unidad_organizacional,
                'material': asig.nota_ingreso_detalle.material,
                'programado': asig.cantidad_asignada,
                'consumido': asig.cantidad_retirada,
                'disponible': asig.saldo_disponible,
                'pct_consumido': round(pct_consumido, 1),
                'agotado': (asig.saldo_disponible == 0)
            })

    total_criticas = lotes_vencidos.count() + materiales_agotados.count() + len([c for c in cuotas_criticas if c['agotado']])
    total_advertencias = lotes_por_vencer.count() + materiales_bajo_stock.count() + len([c for c in cuotas_criticas if not c['agotado']])

    return {
        'lotes_vencidos': lotes_vencidos,
        'lotes_por_vencer': lotes_por_vencer,
        'materiales_agotados': materiales_agotados,
        'materiales_bajo_stock': materiales_bajo_stock,
        'cuotas_criticas': cuotas_criticas,
        'poas_criticos': [],  # Se mantiene vacío para compatibilidad con plantillas
        'total_criticas': total_criticas,
        'total_advertencias': total_advertencias,
        'total_alertas': total_criticas + total_advertencias,
    }