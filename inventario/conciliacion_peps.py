"""
Filtro provisional de conciliacion PEPS para SIGAL.

Excluye diez entradas historicas identificadas mediante referencias
documentales verificadas en la base de reconstruccion.

No elimina movimientos ni modifica existencias.
Pendiente: clasificacion persistente y auditable antes de produccion.
"""

from inventario.historico_models import MovimientoHistoricoPendiente

REFERENCIAS_HISTORICAS_DUPLICADAS = (
    "HISTORICO-MASIVO-91",
    "HISTORICO-MASIVO-114",
    "HISTORICO-MASIVO-157",
    "HISTORICO-MASIVO-196",
    "HISTORICO-MASIVO-204",
    "HISTORICO-MASIVO-217",
    "HISTORICO-MASIVO-284",
    "HISTORICO-MASIVO-301",
    "HISTORICO-MASIVO-364",
    "HISTORICO-MASIVO-424",
)


def lotes_peps_operativos(queryset):
    """Excluye las entradas historicas documentadas."""
    return queryset.exclude(
        tipo="ENTRADA",
        referencia__in=REFERENCIAS_HISTORICAS_DUPLICADAS,
    )


def verificar_lotes_duplicados():
    """Comprueba la vinculacion historica de las referencias."""
    from inventario.models import MovimientoInventario

    lotes = MovimientoInventario.objects.filter(
        tipo="ENTRADA",
        referencia__in=REFERENCIAS_HISTORICAS_DUPLICADAS,
    )

    if lotes.count() != len(REFERENCIAS_HISTORICAS_DUPLICADAS):
        return False

    for lote in lotes:
        if MovimientoHistoricoPendiente.objects.filter(
            estado="INCORPORADO",
            movimiento_operativo_id=lote.pk,
        ).count() != 1:
            return False

    return True
