from django.core.exceptions import ValidationError
from django.db.models import Sum

from inventario.models import (
    InventarioAlmacen,
    AsignacionEntradaUnidad,
)


def validar_conteo_fisico(almacen, material, cantidad_fisica):
    """
    Valida un conteo físico sin modificar inventario.

    No registra movimientos ni aprueba conciliaciones.
    """

    if not isinstance(cantidad_fisica, int) or isinstance(cantidad_fisica, bool):
        raise ValidationError(
            "La cantidad física debe ser un número entero."
        )

    if cantidad_fisica < 0:
        raise ValidationError(
            "La cantidad física no puede ser negativa."
        )

    inventario = InventarioAlmacen.objects.filter(
        almacen=almacen,
        material=material
    ).first()

    if inventario is None:
        raise ValidationError(
            "El material no tiene un registro de inventario "
            "en el almacén seleccionado."
        )

    stock_sistema = inventario.stock_fisico
    reservado = inventario.stock_reservado

    if stock_sistema < 0 or reservado < 0:
        raise ValidationError(
            "El inventario contiene cantidades negativas."
        )

    if reservado > stock_sistema:
        raise ValidationError(
            "Las reservas actuales superan el stock registrado."
        )

    asignaciones = AsignacionEntradaUnidad.objects.filter(
        nota_ingreso_detalle__material=material,
        nota_ingreso_detalle__nota_ingreso__almacen_destino=almacen
    ).aggregate(
        asignado=Sum("cantidad_asignada"),
        retirado=Sum("cantidad_retirada")
    )

    total_asignado = asignaciones["asignado"] or 0
    total_retirado = asignaciones["retirado"] or 0

    if total_retirado > total_asignado:
        raise ValidationError(
            "Las cantidades retiradas superan las asignadas."
        )

    pendiente = total_asignado - total_retirado

    diferencia = cantidad_fisica - stock_sistema

    if diferencia < 0:
        if cantidad_fisica < reservado:
            raise ValidationError(
                "El conteo físico es inferior a las reservas vigentes."
            )

        if cantidad_fisica < pendiente:
            raise ValidationError(
                "El conteo físico es inferior a las "
                "asignaciones directas pendientes."
            )

    return {
        "almacen_id": almacen.pk,
        "material_id": material.pk,
        "cantidad_sistema": stock_sistema,
        "cantidad_fisica": cantidad_fisica,
        "diferencia": diferencia,
        "stock_reservado": reservado,
        "asignacion_pendiente": pendiente,
        "requiere_ajuste": diferencia != 0,
    }

def validar_creacion_borrador(usuario, almacen, gestion, fecha_corte):
    """
    Comprueba permisos y coherencia temporal para crear
    una conciliación física en estado BORRADOR.

    No realiza operaciones de escritura.
    """
    from datetime import date
    from django.utils import timezone
    from inventario.models import MovimientoInventario

    roles_permitidos = {
        "ADMINISTRADOR",
        "ADMIN_ALMACENES",
        "ALMACENERO",
    }

    if not getattr(usuario, "is_authenticated", False):
        raise ValidationError("El usuario debe iniciar sesión.")

    if not getattr(usuario, "is_active", False):
        raise ValidationError("La cuenta de usuario está inactiva.")

    perfil = getattr(usuario, "perfilusuario", None)

    if perfil is None:
        raise ValidationError("El usuario no tiene perfil institucional.")

    if perfil.rol not in roles_permitidos:
        raise ValidationError(
            "El usuario no tiene un rol autorizado para "
            "registrar conciliaciones físicas."
        )

    if not almacen.is_active:
        raise ValidationError("El almacén seleccionado está inactivo.")

    if not perfil.puede_operar_almacen(almacen):
        raise ValidationError(
            "El usuario no tiene autorización para operar este almacén."
        )

    if not isinstance(gestion, int) or isinstance(gestion, bool):
        raise ValidationError("La gestión debe ser un año entero.")

    if not isinstance(fecha_corte, date) or isinstance(
        fecha_corte, __import__("datetime").datetime
    ):
        raise ValidationError("La fecha de corte debe ser una fecha válida.")

    if fecha_corte.year != gestion:
        raise ValidationError(
            "La fecha de corte no corresponde a la gestión indicada."
        )

    if fecha_corte > timezone.localdate():
        raise ValidationError(
            "No se permiten fechas de corte futuras."
        )

    movimientos_posteriores = MovimientoInventario.objects.filter(
        almacen=almacen,
        fecha__date__gt=fecha_corte,
    ).exists()

    if movimientos_posteriores:
        raise ValidationError(
            "Existen movimientos posteriores a la fecha de corte. "
            "No se puede utilizar el stock actual como saldo histórico."
        )

    return {
        "almacen_id": almacen.pk,
        "gestion": gestion,
        "fecha_corte": fecha_corte,
        "usuario_id": usuario.pk,
        "validado": True,
    }


def validar_conteo_completo(almacen, conteos):
    """
    Valida que el conteo incluya todos los materiales registrados
    en el inventario del almacén.

    conteos: diccionario {material_id: cantidad_fisica}

    No modifica existencias, movimientos ni conciliaciones.
    """
    from inventario.models import InventarioAlmacen

    if not isinstance(conteos, dict):
        raise ValidationError(
            "El conteo debe proporcionarse como un diccionario."
        )

    inventarios = list(
        InventarioAlmacen.objects.filter(
            almacen=almacen
        ).values(
            "material_id",
            "stock_fisico",
            "stock_reservado",
        )
    )

    inventario_por_material = {
        fila["material_id"]: fila
        for fila in inventarios
    }

    if len(inventario_por_material) != len(inventarios):
        raise ValidationError(
            "Existen registros duplicados de inventario."
        )

    materiales_esperados = set(inventario_por_material)
    materiales_recibidos = set(conteos)

    faltantes = materiales_esperados - materiales_recibidos
    ajenos = materiales_recibidos - materiales_esperados

    if faltantes:
        raise ValidationError(
            f"Faltan {len(faltantes)} materiales en el conteo."
        )

    if ajenos:
        raise ValidationError(
            f"Se recibieron {len(ajenos)} materiales "
            "que no pertenecen al inventario del almacén."
        )

    detalles = []

    for material_id in sorted(materiales_esperados):
        cantidad_fisica = conteos[material_id]

        if (
            not isinstance(cantidad_fisica, int)
            or isinstance(cantidad_fisica, bool)
        ):
            raise ValidationError(
                f"La cantidad física del material {material_id} "
                "debe ser un número entero."
            )

        if cantidad_fisica < 0:
            raise ValidationError(
                f"La cantidad física del material {material_id} "
                "no puede ser negativa."
            )

        inventario = inventario_por_material[material_id]

        cantidad_sistema = inventario["stock_fisico"]
        cantidad_reservada = inventario["stock_reservado"]

        if cantidad_sistema < 0 or cantidad_reservada < 0:
            raise ValidationError(
                f"El material {material_id} tiene cantidades "
                "negativas registradas en SIGAL."
            )

        if cantidad_reservada > cantidad_sistema:
            raise ValidationError(
                f"Las reservas del material {material_id} "
                "superan su stock físico."
            )

        diferencia = cantidad_fisica - cantidad_sistema

        detalles.append({
            "material_id": material_id,
            "cantidad_sistema": cantidad_sistema,
            "cantidad_fisica": cantidad_fisica,
            "diferencia": diferencia,
            "stock_reservado": cantidad_reservada,
            "requiere_ajuste": diferencia != 0,
        })

    return {
        "almacen_id": almacen.pk,
        "total_materiales": len(detalles),
        "materiales_con_diferencia": sum(
            1 for detalle in detalles
            if detalle["requiere_ajuste"]
        ),
        "detalles": detalles,
    }
