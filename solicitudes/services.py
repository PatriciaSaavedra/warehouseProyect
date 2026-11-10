
from inventario.models import Almacen


class AlmacenNoDisponible(Exception):
    """No existe un almacén autorizado para atender la unidad."""
    pass


class ConfiguracionAlmacenAmbigua(Exception):
    """Existe más de un almacén con la misma prioridad."""
    pass


def resolver_almacen_para_unidad(unidad):
    """
    Selecciona automáticamente el almacén autorizado.

    Prioridad:
    1. Subalmacén propio y autorizado.
    2. Otro almacén autorizado.
    3. Si no existe o hay ambigüedad, informa el problema.

    Nunca combina inventarios de diferentes almacenes.
    """

    if not unidad:
        raise AlmacenNoDisponible(
            "No se especificó una unidad organizacional."
        )

    autorizados = (
        Almacen.objects
        .filter(
            is_active=True,
            unidades_atendidas=unidad
        )
        .distinct()
    )

    # Primera prioridad: subalmacén propio.
    propios = autorizados.filter(
        tipo='SUBALMACEN',
        unidad_organizacional=unidad
    )

    cantidad_propios = propios.count()

    if cantidad_propios > 1:
        raise ConfiguracionAlmacenAmbigua(
            f"La unidad '{unidad.nombre}' tiene varios "
            "subalmacenes propios autorizados."
        )

    if cantidad_propios == 1:
        return propios.get()

    # Segunda prioridad: cualquier otro almacén autorizado.
    restantes = autorizados.order_by('id')
    cantidad_restantes = restantes.count()

    if cantidad_restantes == 0:
        raise AlmacenNoDisponible(
            f"No existe un almacén activo autorizado "
            f"para atender a '{unidad.nombre}'."
        )

    if cantidad_restantes > 1:
        raise ConfiguracionAlmacenAmbigua(
            f"La unidad '{unidad.nombre}' tiene varios "
            "almacenes autorizados y no existe una "
            "prioridad definida entre ellos."
        )

    return restantes.get()

from django.db import transaction
from django.core.exceptions import ValidationError
from inventario.models import InventarioAlmacen


def liberar_reservas_solicitud(solicitud):
    """
    Libera las reservas administrativas de una solicitud
    de salida de almacén.

    Debe ejecutarse dentro de la misma transacción
    que modifica el estado de la solicitud.
    """

    if solicitud.flujo_atencion != 'SALIDA_ALMACEN':
        raise ValidationError(
            "La solicitud no corresponde a una salida de almacén."
        )

    if solicitud.estado not in ['REVISADA', 'PREPARADA']:
        raise ValidationError(
            "La solicitud no está en un estado con reservas activas."
        )

    almacen = solicitud.almacen_origen

    if almacen is None:
        raise ValidationError(
            "La solicitud no tiene un almacén de origen válido."
        )

    cantidades_por_material = {}

    for detalle in solicitud.detalles.all():
        cantidad = detalle.cantidad_aprobada

        if cantidad is None:
            raise ValidationError(
                "Una solicitud aprobada tiene cantidades sin definir."
            )

        if cantidad < 0:
            raise ValidationError(
                "La solicitud contiene cantidades aprobadas negativas."
            )

        if cantidad > 0 and detalle.material_id is None:
            raise ValidationError(
                "Existe una cantidad aprobada sin material asociado."
            )

        if detalle.material_id is not None:
            cantidades_por_material[detalle.material_id] = (
                cantidades_por_material.get(detalle.material_id, 0)
                + cantidad
            )

    with transaction.atomic():
        inventarios = {}

        for material_id in sorted(cantidades_por_material):
            cantidad = cantidades_por_material[material_id]

            if cantidad == 0:
                continue

            inventario = (
                InventarioAlmacen.objects
                .select_for_update()
                .get(
                    material_id=material_id,
                    almacen=almacen
                )
            )

            if inventario.stock_reservado < cantidad:
                raise ValidationError(
                    "No se puede liberar la reserva del material "
                    f"{inventario.material_id}: saldo insuficiente."
                )

            inventarios[material_id] = inventario

        for material_id, inventario in inventarios.items():
            inventario.stock_reservado -= (
                cantidades_por_material[material_id]
            )
            inventario.save(update_fields=['stock_reservado'])
