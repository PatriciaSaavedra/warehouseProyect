from django.contrib import admin
from .models import Bitacora


@admin.register(Bitacora)
class BitacoraAdmin(admin.ModelAdmin):

    list_display = (
        'fecha',
        'usuario',
        'modulo',
        'accion'
    )

    search_fields = (
        'usuario__username',
        'accion',
        'modulo'
    )

    list_filter = (
        'modulo',
        'fecha'
    )