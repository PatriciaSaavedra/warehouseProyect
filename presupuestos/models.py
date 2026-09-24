from django.db import models
from django.db import transaction
from django.core.exceptions import ValidationError
from django.contrib.auth.models import User
from decimal import Decimal

from organizacion.models import UnidadOrganizacional
from inventario.models import PartidaPresupuestaria
from auditoria.models import Bitacora


class POA(models.Model):
    """
    Plan Operativo Anual (POA) - Controla el techo presupuestario asignado 
    a cada Unidad para cada Partida Presupuestaria específica.
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
        default=Decimal('0.00'),
        help_text="Presupuesto de apertura asignado"
    )
    monto_disponible = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=Decimal('0.00'),
        help_text="Saldo presupuestario remanente"
    )
    monto_comprometido = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=Decimal('0.00'),
        help_text="Presupuesto reservado provisionalmente para adquisiciones aprobadas"
    )
    monto_ejecutado = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=Decimal('0.00'),
        help_text="Presupuesto gastado de forma definitiva tras la entrega/compra real"
    )

    class Meta:
        verbose_name = "POA"
        verbose_name_plural = "POAs"
        unique_together = ('unidad', 'partida', 'gestion')

    def __str__(self):
        return f"{self.unidad.nombre} - Partida {self.partida.codigo} ({self.gestion})"

    @property
    def total_modificaciones(self):
        inc = self.modificaciones.filter(tipo='INCREMENTO').aggregate(s=models.Sum('monto'))['s'] or Decimal('0.00')
        red = self.modificaciones.filter(tipo='REDUCCION').aggregate(s=models.Sum('monto'))['s'] or Decimal('0.00')
        return inc - red

    @property
    def presupuesto_vigente(self):
        return self.monto_inicial + self.total_modificaciones

    @property
    def porcentaje_ejecucion(self):
        vigente = self.presupuesto_vigente
        if vigente > Decimal('0.00'):
            return round((float(self.monto_ejecutado) / float(vigente)) * 100, 2)
        return 0.0

    @property
    def porcentaje_comprometido(self):
        vigente = self.presupuesto_vigente
        if vigente > Decimal('0.00'):
            return round((float(self.monto_comprometido) / float(vigente)) * 100, 2)
        return 0.0


class DetalleProgramacionPOA(models.Model):
    """
    Representa cada fila detallada del Formulario 005 de la Gobernación de Potosí.
    Desglosa el requerimiento anual por insumo, actividad POA, cantidad y precio unitario estimado.
    """
    poa = models.ForeignKey(
        POA, 
        on_delete=models.CASCADE, 
        related_name='programacion_items'
    )
    codigo_actividad_poa = models.CharField(
        max_length=50, 
        default='13.1 - 13.4',
        help_text="Código de operación/actividad en el POA (Columna 1 Form. 005)"
    )
    material = models.ForeignKey(
        'inventario.Material', 
        on_delete=models.PROTECT,
        related_name='programaciones_poa'
    )
    unidad_medida = models.CharField(max_length=50, blank=True, null=True)
    cantidad_programada = models.IntegerField(
        help_text="Cantidad anual demandada en el Formulario 005"
    )
    cantidad_consumida = models.IntegerField(
        default=0,
        help_text="Cantidad física ya retirada mediante notas de salida"
    )
    precio_unitario_estimado = models.DecimalField(
        max_digits=12, 
        decimal_places=2,
        help_text="Precio referencial unitario presupuestado (Bs.)"
    )
    subtotal = models.DecimalField(
        max_digits=12, 
        decimal_places=2,
        help_text="Monto total presupuestado para este material (Cantidad x P. Unitario)"
    )

    @property
    def cantidad_disponible(self):
        return max(0, self.cantidad_programada - self.cantidad_consumida)

    def save(self, *args, **kwargs):
        self.subtotal = Decimal(str(self.cantidad_programada)) * Decimal(str(self.precio_unitario_estimado))
        if self.material and not self.unidad_medida:
            self.unidad_medida = self.material.unidad_medida_fk.codigo if self.material.unidad_medida_fk else self.material.unidad_medida
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.poa.unidad.nombre} - {self.material.nombre} ({self.cantidad_programada} {self.unidad_medida})"

    class Meta:
        verbose_name = "Detalle de Programación POA (Form. 005)"
        verbose_name_plural = "Detalles de Programación POA (Form. 005)"


class ModificacionPresupuestaria(models.Model):
    TIPO_MODIFICACION = [
        ('INCREMENTO', 'Incremento Presupuestario'),
        ('REDUCCION', 'Reducción Presupuestaria'),
    ]

    poa = models.ForeignKey(
        POA,
        on_delete=models.CASCADE,
        related_name='modificaciones',
        help_text="POA al que se le aplicará el ajuste"
    )
    tipo = models.CharField(
        max_length=15,
        choices=TIPO_MODIFICACION,
        help_text="Tipo de ajuste presupuestario"
    )
    monto = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Monto del ajuste en bolivianos"
    )
    justificacion = models.TextField(
        help_text="Justificación técnica o resolución administrativa del traspaso"
    )
    fecha_registro = models.DateTimeField(
        auto_now_add=True
    )
    usuario = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        help_text="Analista de presupuestos que registra la modificación"
    )

    class Meta:
        verbose_name = "Modificación Presupuestaria"
        verbose_name_plural = "Modificaciones Presupuestarias"

    def __str__(self):
        return f"{self.tipo} - Bs. {self.monto:.2f} (POA ID: {self.poa.id})"

    def save(self, *args, **kwargs):
        if self.monto <= Decimal('0.00'):
            raise ValidationError("El monto de la modificación debe ser estrictamente mayor a cero.")

        with transaction.atomic():
            poa_update = POA.objects.select_for_update().get(id=self.poa.id)

            if self.pk is None:
                if self.tipo == 'INCREMENTO':
                    poa_update.monto_disponible += self.monto
                elif self.tipo == 'REDUCCION':
                    if poa_update.monto_disponible < self.monto:
                        raise ValidationError(
                            f"No es posible realizar la reducción. El monto solicitado ({self.monto:.2f} Bs.) "
                            f"supera el saldo disponible actual del POA ({poa_update.monto_disponible:.2f} Bs.)."
                        )
                    poa_update.monto_disponible -= self.monto

                poa_update.save()
                super().save(*args, **kwargs)

                Bitacora.objects.create(
                    usuario=self.usuario,
                    modulo='Presupuestos',
                    accion=f'Modificación Presupuestaria ({self.tipo})',
                    descripcion=(
                        f'Se registró un {self.get_tipo_display()} por Bs. {self.monto:.2f} '
                        f'en la partida {poa_update.partida.codigo} de la unidad {poa_update.unidad.nombre}. '
                        f'Justificación: {self.justificacion[:100]}...'
                    )
                )
            else:
                super().save(*args, **kwargs)