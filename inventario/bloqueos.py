from django.core.exceptions import ValidationError
from django.db import connection, transaction

from inventario.models import Almacen


def bloquear_almacen(almacen_id):
    """
    Bloquea el registro del almacén durante una transacción.

    Todas las operaciones que modifiquen inventario deberán
    adquirir este bloqueo antes de acceder a existencias o lotes.

    No modifica registros.
    """
    if not connection.in_atomic_block:
        raise ValidationError(
            "El bloqueo de almacén requiere una transacción activa."
        )

    if (
        not isinstance(almacen_id, int)
        or isinstance(almacen_id, bool)
        or almacen_id <= 0
    ):
        raise ValidationError(
            "El identificador del almacén no es válido."
        )

    try:
        return (
            Almacen.objects
            .select_for_update()
            .get(pk=almacen_id, is_active=True)
        )
    except Almacen.DoesNotExist:
        raise ValidationError(
            "El almacén no existe o se encuentra inactivo."
        )