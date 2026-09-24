from django.contrib import admin
from .models import Almacen, InventarioAlmacen

from .models import (
    Material,
    MovimientoInventario,
    PartidaPresupuestaria,
    UnidadMedida,
    AsignacionMaterialUnidad
)

admin.site.register(Material)
admin.site.register(MovimientoInventario)
admin.site.register(PartidaPresupuestaria)
admin.site.register(UnidadMedida)


@admin.register(Almacen)
class AlmacenAdmin(admin.ModelAdmin):
    """
    Tarjeta 2: Panel administrativo para la gestión física de Almacén Central y Subalmacenes
    """
    list_display = ('nombre', 'tipo', 'unidad_organizacional', 'responsable', 'is_active')
    list_filter = ('tipo', 'is_active', 'unidad_organizacional')
    search_fields = ('nombre', 'descripcion', 'responsable__username')
    raw_id_fields = ('responsable',)  # Optimiza la búsqueda si hay muchos usuarios registrados


@admin.register(InventarioAlmacen)
class InventarioAlmacenAdmin(admin.ModelAdmin):
    """
    Tarjeta 5: Visualización administrativa del inventario aislado por almacén
    """
    list_display = ('material', 'almacen', 'stock_disponible')
    list_filter = ('almacen',)
    search_fields = ('material__nombre', 'almacen__nombre')

@admin.register(AsignacionMaterialUnidad)
class AsignacionMaterialUnidadAdmin(admin.ModelAdmin):
    list_display = ('unidad', 'material', 'gestion', 'cantidad_asignada', 'cantidad_consumida', 'saldo_disponible')
    list_filter = ('gestion', 'unidad')
    search_fields = ('unidad__nombre', 'material__nombre', 'material__codigo')