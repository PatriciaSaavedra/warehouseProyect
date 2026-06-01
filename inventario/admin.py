from django.contrib import admin
from .models import (
    Material,
    MovimientoInventario,
    PartidaPresupuestaria,
    UnidadMedida
)
admin.site.register(Material)
admin.site.register(MovimientoInventario)
admin.site.register(PartidaPresupuestaria)
admin.site.register(UnidadMedida)