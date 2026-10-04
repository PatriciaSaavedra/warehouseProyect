from datetime import date, timedelta
from decimal import Decimal
from django.db import transaction
from django.db.models import F
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import Material, MovimientoInventario, InventarioAlmacen, Almacen
from presupuestos.models import POA, DetalleProgramacionPOA


# ========================================================
# 1. SALIDAS VALORADAS (PEPS)
# ========================================================

def registrar_salida_valorada_peps(material, almacen, cantidad_salida, tipo_movimiento, referencia, usuario, unidad_destino=None, descontar_reserva=False):
    """
    Descuenta stock físico aplicando el método PEPS (FIFO) de manera aislada por Almacén.
    Agota cronológicamente los lotes de ENTRADA en el almacén especificado con saldo disponible y calcula el costo real.
    Soporta consumo multicapa (ej. 5 de Lote 1 a 25 Bs. y 1 de Lote 2 a 30 Bs.).
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
            referencia=ref_final[:100],  # Respetar max_length
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
# 4. MOTOR CENTRALIZADO DE ALERTAS TEMPRANAS SABS (TARJETA 7)
# ========================================================

def obtener_alertas_tempranas(usuario=None):
    """
    Tarjeta 7: Motor centralizado de Alertas Tempranas del SGA (GAD Potosí).
    Retorna un diccionario con alertas de:
    - Vencimientos REALES por Lote físico en estantería (saldo_disponible_lote > 0)
    - Stock físico crítico / agotado
    - Techos presupuestarios POA (< 15%)
    - Cuotas físicas Formulario 005 (> 85%)
    """
    hoy = timezone.now().date()
    en_30_dias = hoy + timedelta(days=30)
    gestion_actual = hoy.year

    # 1. ALERTAS DE VENCIMIENTO REALES POR LOTE EN ESTANTERÍA CON EXISTENCIAS
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

    # 2. ALERTAS DE EXISTENCIAS (Agotados y Bajo Stock)
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

    # 3. ALERTAS DE PRESUPUESTO CRÍTICO EN POA (< 15% disponible)
    poas_criticos = []
    poas_activos = POA.objects.filter(
        gestion=gestion_actual,
        monto_inicial__gt=0
    ).select_related('unidad', 'unidad__secretaria', 'partida')

    for p in poas_activos:
        limite_15 = p.monto_inicial * Decimal('0.15')
        if p.monto_disponible <= limite_15:
            poas_criticos.append({
                'poa': p,
                'saldo_disponible': p.monto_disponible,
                'monto_inicial': p.monto_inicial,
                'pct_consumido': p.porcentaje_ejecucion,
                'es_cero': (p.monto_disponible <= Decimal('0.00'))
            })

    poas_criticos.sort(key=lambda x: x['saldo_disponible'])

    # 4. ALERTAS DE CUOTAS DEL FORMULARIO 005 (> 85% consumido)
    cuotas_criticas = []
    items_005 = DetalleProgramacionPOA.objects.filter(
        poa__gestion=gestion_actual,
        cantidad_programada__gt=0
    ).select_related('poa__unidad', 'material', 'poa__partida')

    for it in items_005:
        pct_cant = (it.cantidad_consumida / it.cantidad_programada) * 100
        if pct_cant >= 85:
            cuotas_criticas.append({
                'item': it,
                'unidad': it.poa.unidad,
                'material': it.material,
                'programado': it.cantidad_programada,
                'consumido': it.cantidad_consumida,
                'disponible': it.cantidad_disponible,
                'pct_consumido': round(pct_cant, 1),
                'agotado': (it.cantidad_disponible == 0)
            })

    cuotas_criticas.sort(key=lambda x: x['disponible'])

    # Conteo de Alertas
    total_criticas = (
        lotes_vencidos.count() +
        materiales_agotados.count() +
        len([p for p in poas_criticos if p['es_cero']]) +
        len([c for c in cuotas_criticas if c['agotado']])
    )

    total_advertencias = (
        lotes_por_vencer.count() +
        materiales_bajo_stock.count() +
        len([p for p in poas_criticos if not p['es_cero']]) +
        len([c for c in cuotas_criticas if not c['agotado']])
    )

    return {
        # Claves de lotes reales
        'lotes_vencidos': lotes_vencidos,
        'lotes_por_vencer': lotes_por_vencer,
        # Claves de compatibilidad
        'materiales_vencidos': lotes_vencidos,
        'materiales_por_vencer': lotes_por_vencer,
        'materiales_agotados': materiales_agotados,
        'materiales_bajo_stock': materiales_bajo_stock,
        'poas_criticos': poas_criticos,
        'cuotas_criticas': cuotas_criticas,
        'total_criticas': total_criticas,
        'total_advertencias': total_advertencias,
        'total_alertas': total_criticas + total_advertencias,
    }

