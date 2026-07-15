from django.db import models
from organizacion.models import UnidadOrganizacional
from inventario.models import PartidaPresupuestaria

class POA(models.Model):
    """
    Plan Operativo Anual (POA) - Controla el techo presupuestario asignado 
    a cada Unidad para cada Partida Presupuestaria específica (Pág. 12 del Diagrama de Objetos) [28].
    """
    unidad = models.ForeignKey(
        UnidadOrganizacional, 
        on_delete=models.PROTECT, 
        related_name='poas'
    )
    partida = models.ForeignKey(
        PartidaPresupuestaria, 
        on_delete=models.PROTECT, 
        related_name='poas'
    )
    gestion = models.IntegerField(
        default=2026, 
        help_text="Año de gestión fiscal de la Gobernación"
    )
    monto_inicial = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=0.00,
        help_text="Presupuesto de apertura asignado"
    )
    monto_disponible = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=0.00,
        help_text="Saldo presupuestario remanente"
    )

    def __str__(self):
        return f"{self.unidad.nombre} - Partida {self.partida.codigo} ({self.gestion})"

    class Meta:
        verbose_name = "POA"
        verbose_name_plural = "POAs"
        unique_together = ('unidad', 'partida', 'gestion')