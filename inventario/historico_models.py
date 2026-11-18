from django.conf import settings
from django.db import models


class MovimientoHistoricoPendiente(models.Model):
    """
    Registro documental de movimientos históricos.

    No modifica existencias ni participa en el cálculo PEPS.
    """

    class Estado(models.TextChoices):
        PENDIENTE = "PENDIENTE", "Pendiente"
        EN_REVISION = "EN_REVISION", "En revisión"
        BLOQUEADO = "BLOQUEADO", "Bloqueado"
        VALIDADO = "VALIDADO", "Validado"
        INCORPORADO = "INCORPORADO", "Incorporado"

    codigo_material = models.CharField(max_length=100)
    descripcion_material = models.CharField(
        max_length=300,
        blank=True,
        default=""
    )

    material = models.ForeignKey(
        "inventario.Material",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="movimientos_historicos_pendientes"
    )

    fecha_original = models.DateTimeField(
        null=True,
        blank=True
    )

    tipo_original = models.CharField(
        max_length=100,
        blank=True,
        default=""
    )

    cantidad = models.DecimalField(
        max_digits=16,
        decimal_places=4,
        null=True,
        blank=True
    )

    precio_unitario = models.DecimalField(
        max_digits=16,
        decimal_places=4,
        null=True,
        blank=True
    )

    valor_total = models.DecimalField(
        max_digits=18,
        decimal_places=4,
        null=True,
        blank=True
    )

    documento_original = models.CharField(
        max_length=200,
        blank=True,
        default=""
    )

    destino_proveedor_original = models.TextField(
        blank=True,
        default=""
    )

    archivo_origen = models.CharField(max_length=500)
    hoja_origen = models.CharField(
        max_length=200,
        blank=True,
        default=""
    )
    fila_origen = models.PositiveIntegerField()

    huella_origen = models.CharField(
        max_length=64,
        unique=True
    )

    estado = models.CharField(
        max_length=20,
        choices=Estado.choices,
        default=Estado.PENDIENTE
    )

    observaciones = models.TextField(
        blank=True,
        default=""
    )

    movimiento_operativo = models.ForeignKey(
        "inventario.MovimientoInventario",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="registros_historicos_origen"
    )

    revisado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    fecha_registro = models.DateTimeField(auto_now_add=True)
    fecha_revision = models.DateTimeField(
        null=True,
        blank=True
    )

    class Meta:
        ordering = ["codigo_material", "fecha_original", "id"]
        verbose_name = "Movimiento histórico pendiente"
        verbose_name_plural = "Movimientos históricos pendientes"

    def __str__(self):
        return (
            f"{self.codigo_material} - "
            f"{self.tipo_original} - "
            f"{self.estado}"
        )
