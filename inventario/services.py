# inventario/services.py

from decimal import Decimal
from django.db import transaction
from django.db.models import Prefetch
from django.core.exceptions import ValidationError

from .models import Material, MovimientoInventario, InventarioAlmacen, Almacen
from organizacion.models import UnidadOrganizacional

# ========================================================
# TARJETA 2: Regla funcional de asignación Unidad → Almacén
# ========================================================

def unidades_ya_asignadas(unidades_atendidas_ids, almacen_excluir_id=None):
    """
    Tarjeta 2: Regla funcional "una Unidad Organizacional solo puede ser atendida
    por UN almacén a la vez". Devuelve {unidad_id: (nombre_unidad, almacen_asignado)}
    para las unidades seleccionadas que ya estén asignadas a otro almacén.
    Se centraliza aquí para que vistas y Django Admin compartan la misma validación.
    """
    conflictos = {}
    ids = [int(i) for i in unidades_atendidas_ids if str(i).isdigit()]
    if not ids:
        return conflictos

    prefetch_unidades = Prefetch(
        'unidades_atendidas',
        queryset=UnidadOrganizacional.objects.filter(id__in=ids)
    )
    otros_almacenes = Almacen.objects.exclude(id=almacen_excluir_id) if almacen_excluir_id else Almacen.objects.all()
    otros_almacenes = (
        otros_almacenes
        .filter(unidades_atendidas__id__in=ids)
        .prefetch_related(prefetch_unidades)
        .distinct()
    )

    for almacen in otros_almacenes:
        for unidad in almacen.unidades_atendidas.all():
            conflictos.setdefault(unidad.id, (unidad.nombre, almacen.nombre))
    return conflictos

# ========================================================
# 1. SALIDAS VALORADAS (PEPS)
# ========================================================

def registrar_salida_valorada_peps(material, almacen, cantidad_salida, tipo_movimiento, referencia, usuario, unidad_destino=None, descontar_reserva=False):
    """
    Descuenta stock físico aplicando el método PEPS (FIFO) de manera aislada por Almacén.
    Agota cronológicamente los lotes de ENTRADA en el almacén especificado con saldo disponible y calcula el costo real.
    
    Adicionalmente, permite descontar del stock_reservado si la salida proviene de una solicitud aprobada.
    """
    # 1. Validar cantidad de salida estrictamente mayor a cero
    if cantidad_salida <= 0:
        raise ValueError("La cantidad de salida debe ser estrictamente mayor a cero.")

    # (Opcional) Validar que el usuario tenga permisos sobre este almacén
    # validar_operacion_almacen(usuario, almacen) 

    # 2. Transacción atómica integral para evitar inconsistencias y bloquear lecturas concurrentes
    with transaction.atomic():
        
        # Obtener y bloquear el registro del inventario del almacén específico
        try:
            inventario_almacen = InventarioAlmacen.objects.select_for_update().get(material=material, almacen=almacen)
        except InventarioAlmacen.DoesNotExist:
            raise ValueError(f"No existe un registro de inventario para {material.nombre} en el almacén {almacen.nombre}.")

        # Validar existencias físicas
        if cantidad_salida > inventario_almacen.stock_fisico:
            raise ValueError(
                f"No existe suficiente stock físico disponible en el almacén {almacen.nombre} "
                f"({inventario_almacen.stock_fisico} disponibles, solicitado: {cantidad_salida})."
            )

        # 3. Obtener y bloquear los lotes de entrada más antiguos con saldo disponible (PEPS)
        # Se agrega 'id' en el order_by como fallback determinista
        lotes_disponibles = MovimientoInventario.objects.select_for_update().filter(
            material=material,
            almacen=almacen,
            tipo='ENTRADA',
            saldo_disponible_lote__gt=0
        ).order_by('fecha', 'id')

        cantidad_restante = cantidad_salida
        costo_total_egreso = Decimal('0.00')

        # 4. Bucle secuencial de agotamiento de capas de costo (PEPS)
        for lote in lotes_disponibles:
            if cantidad_restante <= 0:
                break

            if lote.saldo_disponible_lote >= cantidad_restante:
                # El lote cubre por completo el resto del despacho
                costo_total_egreso += Decimal(cantidad_restante) * lote.costo_unitario
                lote.saldo_disponible_lote -= cantidad_restante
                lote.save()
                cantidad_restante = 0
            else:
                # El lote no alcanza; se agota por completo y se pasa al siguiente
                costo_total_egreso += Decimal(lote.saldo_disponible_lote) * lote.costo_unitario
                cantidad_restante -= lote.saldo_disponible_lote
                lote.saldo_disponible_lote = 0
                lote.save()

        # Validación de integridad física vs contable
        if cantidad_restante > 0:
            raise ValueError("Inconsistencia en el inventario: La suma de lotes valorados PEPS es menor al stock físico.")

        # 5. Actualizar stock en el inventario del almacén
        stock_anterior = inventario_almacen.stock_fisico
        inventario_almacen.stock_fisico -= cantidad_salida
        
        # Descontar de las reservas si corresponde (evitando saldos negativos en reserva)
        if descontar_reserva:
            if inventario_almacen.stock_reservado >= cantidad_salida:
                inventario_almacen.stock_reservado -= cantidad_salida
            else:
                inventario_almacen.stock_reservado = 0  # Previene inconsistencias negativas

        # Al guardar inventario_almacen, se actualiza el stock consolidado del Material automáticamente (vía save() del modelo)
        inventario_almacen.save()

        # Calcular el costo unitario real promedio ponderado para esta transacción de salida
        costo_unitario_ponderado = costo_total_egreso / Decimal(cantidad_salida)

        # 6. Registrar movimiento final en el Kardex
        movimiento = MovimientoInventario.objects.create(
            material=material,
            almacen=almacen,
            tipo=tipo_movimiento,  # 'SALIDA' o 'BAJA'
            cantidad=cantidad_salida,
            costo_unitario=costo_unitario_ponderado,
            costo_total=costo_total_egreso,
            stock_anterior=stock_anterior,
            stock_resultante=inventario_almacen.stock_fisico,
            referencia=referencia,
            usuario=usuario,
            unidad_destino=unidad_destino,
        )

    return movimiento


# ========================================================
# 2. CONSULTAS DE INVENTARIO Y ALMACENES
# ========================================================

def obtener_inventario_por_almacen(almacen):
    """
    REQUERIMIENTO 5: Consultar inventario según almacén.
    Retorna todo el stock de materiales asociado únicamente al almacén indicado.
    """
    return InventarioAlmacen.objects.filter(almacen=almacen).select_related('material')


def obtener_inventario_para_unidad(unidad_organizacional):
    """
    REQUERIMIENTO 6: Consultar inventario según Unidad Organizacional.
    Retorna el inventario de aquellos almacenes que tienen autorización
    para atender (despachar) a la Unidad Organizacional dada.
    """
    # Filtramos almacenes que contengan a esta unidad en sus unidades_atendidas
    almacenes_autorizados = Almacen.objects.filter(unidades_atendidas=unidad_organizacional)
    
    # Obtenemos el inventario de esos almacenes (evitando duplicados con distinct si fuera necesario)
    return InventarioAlmacen.objects.filter(
        almacen__in=almacenes_autorizados
    ).select_related('almacen', 'material').distinct()


# ========================================================
# 3. VALIDACIONES DE SEGURIDAD Y PERMISOS
# ========================================================

def validar_operacion_almacen(usuario, almacen):
    """
    REQUERIMIENTO 9: Validar que las operaciones se realicen 
    sobre el almacén correspondiente.
    """
    if not usuario.is_authenticated:
        raise ValidationError("Debe iniciar sesión para operar el inventario.")

    # Si es superusuario de Django, tiene acceso global
    if usuario.is_superuser:
        return True

    # Comprobar si es el responsable directo del almacén
    if almacen.responsable == usuario:
        return True

    # Comprobar el permiso en el perfil del usuario
    if hasattr(usuario, 'perfilusuario'):
        if not usuario.perfilusuario.tiene_acceso_almacen(almacen):
            raise ValidationError(
                f"No tienes autorización para procesar operaciones en el almacén: {almacen.nombre}. "
                "Contacta al administrador si necesitas acceso."
            )
    else:
        raise ValidationError("Tu usuario no tiene un perfil de roles asignado en el sistema.")
    
    return True


# ========================================================
# 4. TARJETA 4: RECEPCIÓN CONFORME DE TRANSFERENCIAS
# ========================================================

def confirmar_recepcion_transferencia(transferencia_id, usuario):
    """
    Tarjeta 4: Recepción CONFORME de una transferencia dirigida a un subalmacén.

    Significado funcional de "Conforme": se confirma que las cantidades recibidas
    coinciden exactamente con las cantidades enviadas (detalle.cantidad). NO admite
    recepción parcial ni discrepancias (el modelo no dispone de cantidad_recibida).

    Invariancias:
    - Solo recepciona desde el estado real EN_TRANSITO; el estado final real es RECIBIDA.
    - Atómica: bloquea la transferencia (select_for_update) y REVALIDA el estado después
      de adquirir el bloqueo, cerrando la ventana de doble recepción concurrente.
    - NO toca el stock del origen: Central ya descontó al emitir (enviar_transferencia).
    - Aumenta únicamente InventarioAlmacen del destino.
    - Cada detalle genera su lote PEPS (MovimientoInventario ENTRADA con saldo_disponible_lote).
    - Conserva el costo proveniente del Central: costo_unitario_transferencia del detalle.
    - Registra fecha (timezone-aware) y usuario receptor en la transferencia.
    - Cualquier excepción revierte TODO el bloque (rollback completo).
    """
    from django.utils import timezone
    from auditoria.models import Bitacora
    from .models import Transferencia, InventarioAlmacen, MovimientoInventario

    with transaction.atomic():
        transferencia = Transferencia.objects.select_for_update().select_related(
            'origen', 'destino'
        ).get(id=transferencia_id)

        # Revalidación AUTORITATIVA dentro del bloqueo: impide recibir dos veces,
        # incluso si dos requests concurrentes leyeron EN_TRANSITO antes del lock.
        if transferencia.estado != 'EN_TRANSITO':
            raise ValueError(
                f"La transferencia {transferencia.nro_transferencia} no está en tránsito "
                f"(estado actual: {transferencia.get_estado_display()})."
            )

        detalles = list(transferencia.detalles.select_related('material').all())
        if not detalles:
            raise ValueError("La transferencia no posee ítems para consolidar en el destino.")

        for detalle in detalles:
            # Validación de cantidades y costos: no se consolida una transferencia corrupta.
            if detalle.cantidad is None or detalle.cantidad <= 0:
                raise ValueError(
                    f"Cantidad inválida para '{detalle.material.nombre}': {detalle.cantidad}. "
                    "No se puede recibir una transferencia corrupta."
                )
            if detalle.costo_unitario_transferencia is None or detalle.costo_unitario_transferencia < 0:
                raise ValueError(
                    f"Costo unitario inválido para '{detalle.material.nombre}': "
                    f"{detalle.costo_unitario_transferencia}."
                )

            # Mismo motor multi-almacén existente: InventarioAlmacen del DESTINO.
            inventario_destino, _ = InventarioAlmacen.objects.get_or_create(
                material=detalle.material,
                almacen=transferencia.destino,
                defaults={'stock_fisico': 0, 'stock_reservado': 0},
            )
            # Bloquear la fila de existencias del destino para evitar pérdidas de
            # actualización entre recepciones concurrentes al mismo material/almacén.
            inventario_destino = InventarioAlmacen.objects.select_for_update().get(
                pk=inventario_destino.pk
            )

            stock_anterior = inventario_destino.stock_fisico
            inventario_destino.stock_fisico += detalle.cantidad
            inventario_destino.save()

            # Lote de entrada PEPS con el costo proveniente del Central.
            MovimientoInventario.objects.create(
                material=detalle.material,
                almacen=transferencia.destino,
                tipo='ENTRADA',
                cantidad=detalle.cantidad,
                costo_unitario=detalle.costo_unitario_transferencia,
                costo_total=detalle.costo_total_transferencia,
                stock_anterior=stock_anterior,
                stock_resultante=inventario_destino.stock_fisico,
                referencia=f"TRANSFERENCIA RECIBIDA {transferencia.nro_transferencia}",
                usuario=usuario,
                saldo_disponible_lote=detalle.cantidad,
            )

        # El estado cambia SOLO al final: si algo falló antes, ninguna escritura persiste.
        transferencia.estado = 'RECIBIDA'
        transferencia.fecha_recepcion = timezone.now()
        transferencia.usuario_recibe = usuario
        transferencia.save(update_fields=['estado', 'fecha_recepcion', 'usuario_recibe'])

        Bitacora.objects.create(
            usuario=usuario,
            modulo='Inventario',
            accion='Recibir Transferencia',
            descripcion=(
                f'Se recepcionó conforme la transferencia {transferencia.nro_transferencia} '
                f'en el almacén {transferencia.destino.nombre}'
            ),
        )

    return transferencia