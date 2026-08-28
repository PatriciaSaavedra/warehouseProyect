from django.contrib import admin
from organizacion.models import Secretaria, UnidadOrganizacional

@admin.register(Secretaria)
class SecretariaAdmin(admin.ModelAdmin):
    list_display = ('id', 'nombre', 'codigo')
    search_fields = ('nombre', 'codigo')
    ordering = ('nombre',)

@admin.register(UnidadOrganizacional)
class UnidadOrganizacionalAdmin(admin.ModelAdmin):
    list_display = ('id', 'nombre', 'codigo_sigep', 'secretaria')
    search_fields = ('nombre', 'codigo_sigep')
    list_filter = ('secretaria',)
    ordering = ('nombre',)