from django.contrib import admin
from django import forms
from django.core.exceptions import ValidationError

from .models import Almacen, InventarioAlmacen

from .models import (
    Material,
    MovimientoInventario,
    PartidaPresupuestaria,
    UnidadMedida
)
from .services import unidades_ya_asignadas

admin.site.register(Material)
admin.site.register(MovimientoInventario)
admin.site.register(PartidaPresupuestaria)
admin.site.register(UnidadMedida)


class AlmacenAdminForm(forms.ModelForm):
    """
    Tarjeta 2: Reutiliza la validación centralizada unidades_ya_asignadas() para
    que Django Admin honre la regla "una Unidad solo puede ser atendida por UN almacén".
    """
    class Meta:
        model = Almacen
        fields = '__all__'

    def clean_unidades_atendidas(self):
        unidades = self.cleaned_data.get('unidades_atendidas', [])
        conflictos = unidades_ya_asignadas(
            [u.id for u in unidades],
            almacen_excluir_id=self.instance.id if self.instance and self.instance.id else None,
        )
        if conflictos:
            detalle = '; '.join(f'"{n}" ya está asignada a "{a}"' for n, a in conflictos.values())
            raise ValidationError(
                f'{detalle}. Una Unidad Organizacional solo puede ser atendida por UN almacén a la vez. '
                'Retire primero la unidad del almacén actual si desea cambiarla.'
            )
        return unidades


@admin.register(Almacen)
class AlmacenAdmin(admin.ModelAdmin):
    """
    Tarjeta 2: Panel administrativo para la gestión física de Almacén Central y Subalmacenes
    """
    form = AlmacenAdminForm
    list_display = ('nombre', 'tipo', 'unidad_organizacional', 'responsable', 'is_active')
    list_filter = ('tipo', 'is_active', 'unidad_organizacional')
    search_fields = ('nombre', 'descripcion', 'responsable__username')
    raw_id_fields = ('responsable',)  # Optimiza la búsqueda si hay muchos usuarios registrados
    filter_horizontal = ('unidades_atendidas',)


@admin.register(InventarioAlmacen)
class InventarioAlmacenAdmin(admin.ModelAdmin):
    """
    Tarjeta 5: Visualización administrativa del inventario aislado por almacén
    """
    list_display = ('material', 'almacen', 'stock_disponible')
    list_filter = ('almacen',)
    search_fields = ('material__nombre', 'almacen__nombre')