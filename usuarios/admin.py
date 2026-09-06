from django.contrib import admin
from .models import PerfilUsuario

@admin.register(PerfilUsuario)
class PerfilUsuarioAdmin(admin.ModelAdmin):
    """
    Tarjeta 3: Gestión administrativa de roles, unidades y almacenes autorizados para cada usuario
    """
    list_display = ('user', 'rol', 'unidad', 'secretaria', 'telefono')
    list_filter = ('rol', 'unidad', 'secretaria')
    search_fields = ('user__username', 'user__first_name', 'user__last_name', 'telefono')
    
    # Widget de selección horizontal de dos columnas muy limpio para relaciones Muchos a Muchos en Django
    filter_horizontal = ('almacenes_autorizados',)