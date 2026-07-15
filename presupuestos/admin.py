from django.contrib import admin
from .models import POA  

@admin.register(POA)
class POAAdmin(admin.ModelAdmin):
    """
    Configuración para visualizar las asignaciones del POA en el panel de Django [28].
    """
    list_display = ('unidad', 'partida', 'gestion', 'monto_inicial', 'monto_disponible')
    list_filter = ('gestion', 'unidad', 'partida')
    search_fields = ('unidad__nombre', 'partida__codigo', 'partida__nombre')
    ordering = ('unidad__nombre', 'partida__codigo')