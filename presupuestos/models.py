from django.db import models
from inventario.models import PartidaPresupuestaria


class POA(models.Model):

    partida = models.ForeignKey(
        PartidaPresupuestaria,
        on_delete=models.CASCADE
    )

    gestion = models.IntegerField()

    monto_asignado = models.DecimalField(
        max_digits=12,
        decimal_places=2
    )

    monto_ejecutado = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0
    )

    def disponible(self):
        return self.monto_asignado - self.monto_ejecutado

    def ejecutar(self, monto):
        self.monto_ejecutado += monto
        self.save()

    def __str__(self):
        return f"{self.partida.codigo} - {self.gestion}"