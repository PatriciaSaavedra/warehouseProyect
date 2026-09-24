from django.db import models
from django.contrib.auth.models import User
from decimal import Decimal

class CompraMenor(models.Model):
    """
    Gestionado por la Unidad de Bienes y Servicios (D.S. 0181 / RE-SABS).
    Centraliza Órdenes de Compra (Bienes) y Órdenes de Servicio (Hasta Bs. 50,000.00).
    """
    TIPOS_ORDEN = [
        ('COMPRA', 'Orden de Compra (Bienes / Materiales)'),
        ('SERVICIO', 'Orden de Servicio (Servicios Generales)'),
    ]

    ESTADOS_COMPRA = [
        ('EMITIDA', 'Emitida / Autorizada por RPA'),
        ('RECEPCIONADA', 'Recepcionada en Almacén (Solo Bienes)'),
        ('CONCLUIDA', 'Concluida / Conforme'),
        ('ANULADA', 'Anulada'),
    ]

    tipo_orden = models.CharField(
        max_length=20, 
        choices=TIPOS_ORDEN, 
        default='COMPRA',
        help_text="Define si ingresa físicamente a Almacén (Compra) o es prestación directa (Servicio)"
    )
    nro_orden = models.CharField(
        max_length=50, 
        unique=True, 
        help_text="Ej: BB.SS. N° 081/2026 o BB.SS. N° 026/2024"
    )
    proveedor = models.ForeignKey(
        'inventario.Proveedor', 
        on_delete=models.PROTECT, 
        related_name='compras_menores'
    )
    partida = models.ForeignKey(
        'inventario.PartidaPresupuestaria', 
        on_delete=models.PROTECT, 
        related_name='compras_menores'
    )
    solicitud_origen = models.ForeignKey(
        'solicitudes.Solicitud', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='compras_menores'
    )
    monto_total = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=Decimal('0.00')
    )
    gestion = models.IntegerField(default=2026)
    estado = models.CharField(
        max_length=20, 
        choices=ESTADOS_COMPRA, 
        default='EMITIDA'
    )
    completada = models.BooleanField(
        default=False, 
        help_text="Indica si los materiales ya fueron recepcionados en Almacén"
    )
    fecha_registro = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.get_tipo_orden_display()} - {self.nro_orden} ({self.proveedor.razon_social})"

    class Meta:
        verbose_name = "Orden de Compra / Servicio"
        verbose_name_plural = "Órdenes de Compra y Servicio"


class ActaConformidad(models.Model):
    """
    Acta formal de recepción conforme emitida para Servicios o adquisiciones directas.
    """
    compra_menor = models.OneToOneField(
        CompraMenor, 
        on_delete=models.CASCADE, 
        related_name='acta_conformidad'
    )
    codigo_acta = models.CharField(max_length=50, unique=True)
    fecha_firma = models.DateField()
    observaciones = models.TextField(blank=True, null=True)
    firmado_por = models.ForeignKey(User, on_delete=models.PROTECT)
    fecha_registro = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Acta {self.codigo_acta} - {self.compra_menor.nro_orden}"

    class Meta:
        verbose_name = "Acta de Conformidad"
        verbose_name_plural = "Actas de Conformidad"


class DetalleCompraMenor(models.Model):
    """
    Guarda cada ítem/concepto específico adjudicado en la Orden de Compra o Servicio.
    Permite asociar materiales existentes del catálogo o ítems nuevos/servicios libres.
    """
    compra = models.ForeignKey(
        CompraMenor, 
        on_delete=models.CASCADE, 
        related_name='detalles'
    )
    material = models.ForeignKey(
        'inventario.Material', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        help_text="Material del catálogo si ya existía en almacenes"
    )
    descripcion = models.CharField(max_length=300)
    unidad_medida = models.CharField(max_length=50, default='Pza')
    cantidad = models.IntegerField(default=1)
    precio_unitario = models.DecimalField(max_digits=12, decimal_places=2)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2)

    def save(self, *args, **kwargs):
        self.subtotal = Decimal(self.cantidad) * Decimal(str(self.precio_unitario))
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.compra.nro_orden} - {self.descripcion} ({self.cantidad} {self.unidad_medida})"

    class Meta:
        verbose_name = "Detalle de Orden de Compra/Servicio"
        verbose_name_plural = "Detalles de Orden de Compra/Servicio"