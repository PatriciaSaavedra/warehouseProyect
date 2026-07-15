from .models import Material, MovimientoInventario

from decimal import Decimal
from django.db import transaction
from django.shortcuts import get_object_or_404


def registrar_salida_valorada_peps(material, cantidad_salida, tipo_movimiento, referencia, usuario,unidad_destino=None):
    """
    Descuenta stock físico aplicando el método PEPS (FIFO) [28].
    Agota cronológicamente los lotes de ENTRADA con saldo disponible y calcula el costo real.
    """
    if cantidad_salida > material.stock_actual:
        raise ValueError("No existe suficiente stock físico disponible en el almacén.")

    # 1. Obtener los lotes de entrada más antiguos que aún tengan existencias disponibles
    lotes_disponibles = MovimientoInventario.objects.filter(
        material=material,
        tipo='ENTRADA',
        saldo_disponible_lote__gt=0
    ).order_by('fecha')

    cantidad_restante = cantidad_salida
    costo_total_egreso = Decimal('0.00')

    with transaction.atomic():
        # 2. Bucle secuencial de agotamiento de capas de costo (PEPS)
        for lote in lotes_disponibles:
            if cantidad_restante <= 0:
                break

            if lote.saldo_disponible_lote >= cantidad_restante:
                # El lote cubre por completo el resto del despacho
                costo_total_egreso += cantidad_restante * lote.costo_unitario
                lote.saldo_disponible_lote -= cantidad_restante
                lote.save()
                cantidad_restante = 0
            else:
                # El lote no alcanza; se agota por completo y se pasa al siguiente lote más antiguo
                costo_total_egreso += lote.saldo_disponible_lote * lote.costo_unitario
                cantidad_restante -= lote.saldo_disponible_lote
                lote.saldo_disponible_lote = 0
                lote.save()

        if cantidad_restante > 0:
            raise ValueError("Inconsistencia en el inventario: La suma de lotes valorados PEPS es menor al stock físico.")

        # 3. Descontar stock del material
        stock_anterior = material.stock_actual
        material.stock_actual -= cantidad_salida
        material.save()

        # Calcular el costo unitario promedio ponderado de esta transacción de salida
        costo_unitario_ponderado = costo_total_egreso / Decimal(cantidad_salida)

        # 4. Registrar movimiento en el Kardex
        movimiento = MovimientoInventario.objects.create(
            material=material,
            tipo=tipo_movimiento,  # 'SALIDA' o 'BAJA'
            cantidad=cantidad_salida,
            costo_unitario=costo_unitario_ponderado,
            costo_total=costo_total_egreso,
            stock_anterior=stock_anterior,
            stock_resultante=material.stock_actual,
            referencia=referencia,
            usuario=usuario,
            unidad_destino=unidad_destino,
        )

    return movimiento
def registrar_entrada(material, cantidad, usuario, referencia):

    stock_anterior = material.stock_actual

    material.stock_actual += cantidad
    material.save()

    MovimientoInventario.objects.create(
        material=material,
        tipo='ENTRADA',
        cantidad=cantidad,
        stock_anterior=stock_anterior,
        stock_resultante=material.stock_actual,
        referencia=referencia,
        usuario=usuario
    )