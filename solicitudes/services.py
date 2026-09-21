# Tarjeta 5 — SUBALMACENES: DESPACHO LOCAL.
#
# Resuelve y congela el almacén operativo de una Solicitud (solicitud.almacen_operativo)
# para garantizar que TODA la cadena posterior (preparar, entregar, rechazar/liberar,
# cerrar, Nota de Salida, PEPS, InventarioAlmacen, MovimientoInventario) opere sobre el
# MISMO almacén de despacho.
#
# Fuente de verdad EXCLUSIVA de resolución: Almacen.unidades_atendidas (Tarjeta 4).
# NO existe "fallback" al Almacén Central:
#   - 0 almacenes activos -> error controlado (nunca caer al Central).
#   - >1 almacenes activos -> error de configuración (nunca .first()).
from django.db.models import Q, Count

from inventario.models import Almacen
from organizacion.models import UnidadOrganizacional


class AlmacenSolicitudError(ValueError):
    """Error controlado de configuración o resolución del almacén de despacho."""
    pass


def resolver_almacen_por_unidad(unidad_organizacional):
    """
    Resolución ESTRICTA del almacén de despacho de una Unidad organizacional.

    Devuelve el UNICO almacén activo que atiende la Unidad; en cualquier otro caso
    lanza AlmacenSolicitudError (configuración inválida o inexistente).
    """
    almacenes = list(
        Almacen.objects.filter(
            unidades_atendidas=unidad_organizacional,
            is_active=True
        ).order_by('nombre')
    )
    cantidad = len(almacenes)

    if cantidad == 1:
        return almacenes[0]

    if cantidad > 1:
        nombres = ', '.join(a.nombre for a in almacenes)
        raise AlmacenSolicitudError(
            f"Error de configuración: la unidad '{unidad_organizacional.nombre}' está "
            f"atendida por {cantidad} almacenes activos ({nombres}). Una Unidad solo "
            f"puede ser atendida por UN único almacén de despacho."
        )

    raise AlmacenSolicitudError(
        f"La unidad '{unidad_organizacional.nombre}' no tiene ningún almacén de "
        f"despacho activo configurado (Almacen.unidades_atendidas). Configure el "
        f"almacén que atiende esta Unidad antes de continuar."
    )


def q_bandeja_almacenero(perfil):
    """
    Bandeja de trabajo del ALMACENERO (Tarjeta 5), con prioridad histórica:

      A) Solicitudes con almacen_operativo: su almacén congelado pertenece a los
         almacenes operables del perfil.
      B) Solicitudes sin congelar (almacen_operativo NULL) pero CON NotaSalida:
         la fuente histórica es NotaSalida.almacen_origen (NO la asignación
         actual de la Unidad; una ENTREGADA histórica atribuida a UNASBA sigue
         perteneciendo al almacenero de UNASBA aunque la Unidad hoy sea FARMACIA).
      C) SOLO si almacen_operativo NULL, NO existe NotaSalida y estado REGISTRADA:
         resolver por Unidad -> Almacen.unidades_atendidas (unidades atendidas
         EXCLUSIVAMENTE por uno de sus almacenes operables).

    Quedan EXCLUIDAS las Unidades con 0 almacenes y las atendidas por >1 almacén
    (configuración inválida): se bloquea, nunca se resuelve por conveniencia ni
    se cae al Almacén Central.
    """
    operables = perfil.get_almacenes_operables()

    # A) Congelado.
    q = Q(almacen_operativo__in=operables)

    # B) Históricas con Nota de Salida (ENTREGADA consolidada).
    q |= Q(almacen_operativo__isnull=True, notas_salida__almacen_origen__in=operables)

    # C) Sin congelar, sin Nota de Salida y aún REGISTRADA -> resolver por Unidad.
    unidades_atendidas = UnidadOrganizacional.objects.filter(
        almacenes_que_atienden__in=operables
    )
    unidades_duplicadas = UnidadOrganizacional.objects.filter(
        almacenes_que_atienden__isnull=False,
        almacenes_que_atienden__is_active=True,
    ).annotate(
        n_almacenes=Count('almacenes_que_atienden')
    ).filter(n_almacenes__gt=1).values_list('id', flat=True)

    unidades_ok = unidades_atendidas.exclude(id__in=unidades_duplicadas)
    q |= Q(
        almacen_operativo__isnull=True,
        notas_salida__isnull=True,
        estado='REGISTRADA',
        unidad_solicitante__in=unidades_ok,
    )

    return q


def solicitud_operable_por_almacenero(solicitud, perfil):
    """
    ¿El ALMACENERO puede ver/operar esta solicitud? Prioridad histórica exacta:

      1. solicitud.almacen_operativo        (congelado)       -> acceso al mismo.
      2. NotaSalida.almacen_origen           (histórica)       -> acceso al mismo.
      3. resolver por Unidad SOLO si estado == 'REGISTRADA'    (sin congelar/aún no entregada).
      4. si no puede determinarse -> False.

    Sin fallback al Central y sin .first() ante múltiples almacenes organizacionales.
    """
    almacen = solicitud.almacen_operativo
    if not almacen:
        nota = solicitud.notas_salida.select_related('almacen_origen').order_by('-id').first()
        if nota and nota.almacen_origen:
            # La salida histórica impera sobre la asignación actual de la Unidad.
            almacen = nota.almacen_origen
        elif solicitud.estado == 'REGISTRADA':
            try:
                almacen = resolver_almacen_por_unidad(solicitud.unidad_solicitante)
            except AlmacenSolicitudError:
                return False
        else:
            return False

    return perfil.tiene_acceso_almacen(almacen)


def almacen_operativo_o_historial(solicitud):
    """
    Almacén que usó una solicitud ENTREGADA para regularización administrativa
    (celebración): prioriza el almacén operativo congelado; si la solicitud es
    histórica (sin congelar), recurre a la Nota de Salida registrada.
    Devuelve None si no puede determinarse.
    """
    almacen = solicitud.almacen_operativo
    if almacen:
        return almacen

    nota = solicitud.notas_salida.select_related('almacen_origen').order_by('-id').first()
    if nota and nota.almacen_origen:
        return nota.almacen_origen

    return None