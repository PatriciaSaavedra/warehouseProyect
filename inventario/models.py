from django.db import models
from django.contrib.auth.models import User


class PartidaPresupuestaria(models.Model):

    codigo = models.CharField(
        max_length=20,
        unique=True
    )

    nombre = models.CharField(
        max_length=300
    )

    descripcion = models.TextField(
        blank=True,
        null=True
    )

    def __str__(self):
        return f"{self.codigo} - {self.nombre}"

class UnidadMedida(models.Model):

    codigo = models.CharField(
        max_length=10,
        unique=True
    )

    nombre = models.CharField(
        max_length=100
    )

    def __str__(self):
        return f"{self.codigo} - {self.nombre}"
class Material(models.Model):

    partida = models.ForeignKey(
        'PartidaPresupuestaria',
        on_delete=models.PROTECT,
        null=True,
        blank=True
    )

    codigo = models.CharField(
        max_length=50,
        unique=True
    )

    nombre = models.CharField(
        max_length=200
    )

    descripcion = models.TextField(
        blank=True,
        null=True
    )

    stock_actual = models.IntegerField(
        default=0
    )

    stock_minimo = models.IntegerField(
        default=5
    )

    unidad_medida = models.CharField(
        max_length=50
    )
    unidad_medida_fk = models.ForeignKey(
        'UnidadMedida',
        on_delete=models.PROTECT,
        null=True,
        blank=True
    )

    fecha_registro = models.DateTimeField(
        auto_now_add=True
    )

    def __str__(self):
        return self.nombre
    
    class Meta:

        constraints = [

            models.UniqueConstraint(
                fields=['partida', 'nombre'],
                name='material_unico_por_partida'
            )

    ]

TIPOS_MOVIMIENTO = [

    ('ENTRADA', 'Entrada'),

    ('SALIDA', 'Salida'),

]


class MovimientoInventario(models.Model):

    material = models.ForeignKey(
        Material,
        on_delete=models.CASCADE
    )

    tipo = models.CharField(
        max_length=20,
        choices=TIPOS_MOVIMIENTO
    )

    cantidad = models.IntegerField()

    stock_anterior = models.IntegerField(
        default=0
    )

    stock_resultante = models.IntegerField(
        default=0
    )

    referencia = models.CharField(
        max_length=100
    )

    fecha = models.DateTimeField(
        auto_now_add=True
    )

    usuario = models.ForeignKey(
        User,
        on_delete=models.CASCADE
    )
