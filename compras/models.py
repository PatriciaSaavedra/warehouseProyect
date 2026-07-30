from django.db import models
from django.contrib.auth.models import User

class CompraMenor(models.Model):
    """
    Gestionado por la Unidad de Bienes y Servicios (NB-SABS).
    Registra adquisiciones directas por el RPA (Máx: 50,000.00 Bs.) [28].
    """
    nro_orden = models.CharField(max_length=50, unique=True, help_text="Número de Orden de Compra/Servicio")
    proveedor = models.ForeignKey('inventario.Proveedor', on_delete=models.PROTECT, related_name='compras_menores')
    partida = models.ForeignKey('inventario.PartidaPresupuestaria', on_delete=models.PROTECT, related_name='compras_menores')
    solicitud_origen = models.ForeignKey(
        'solicitudes.Solicitud', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='compras_menores'
    )
    monto_total = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    gestion = models.IntegerField(default=2026)
    completada = models.BooleanField(default=False, help_text="Indica si los materiales ya fueron recepcionados en Almacén")
    fecha_registro = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Orden {self.nro_orden} - {self.proveedor.razon_social}"

    class Meta:
        verbose_name = "Compra Menor"
        verbose_name_plural = "Compras Menores"


class ActaConformidad(models.Model):
    """
    Acta formal de recepción conforme de la compra firmada por Bienes y Servicios [28].
    """
    compra_menor = models.OneToOneField(CompraMenor, on_delete=models.CASCADE, related_name='acta_conformidad')
    codigo_acta = models.CharField(max_length=50, unique=True)
    fecha_firma = models.DateField()
    observaciones = models.TextField(blank=True, null=True)
    firmado_por = models.ForeignKey(User, on_delete=models.PROTECT)
    fecha_registro = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Acta {self.codigo_acta} - Orden {self.compra_menor.nro_orden}"

    class Meta:
        verbose_name = "Acta de Conformidad"
        verbose_name_plural = "Actas de Conformidad"