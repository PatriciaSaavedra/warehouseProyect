# --- TU ARCHIVO presupuestos/models.py CORREGIDO ---

from django.db import models
from django.db import transaction
from django.core.exceptions import ValidationError
from django.contrib.auth.models import User
from organizacion.models import UnidadOrganizacional
from inventario.models import PartidaPresupuestaria
from auditoria.models import Bitacora  # Para registrar la trazabilidad en la bitácora

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
    monto_comprometido = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=0.00,
        help_text="Presupuesto reservado provisionalmente para adquisiciones aprobadas"
    )
    monto_ejecutado = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=0.00,
        help_text="Presupuesto gastado de forma definitiva tras la entrega/compra real"
    )

    def __str__(self):
        return f"{self.unidad.nombre} - Partida {self.partida.codigo} ({self.gestion})"

    class Meta:
        verbose_name = "POA"
        verbose_name_plural = "POAs"
        unique_together = ('unidad', 'partida', 'gestion')


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

    def save(self, *args, **kwargs):
        """
        Automatiza la actualización del POA de forma segura dentro de una transacción.
        Evita reducciones que dejen el saldo disponible del POA en valores negativos.
        """
        if self.monto <= 0:
            raise ValidationError("El monto de la modificación debe ser estrictamente mayor a cero.")

        with transaction.atomic():
            # Bloqueamos el POA para actualización en base de datos para evitar colisiones
            poa_update = POA.objects.select_for_update().get(id=self.poa.id)

            if self.pk is None:  # Solo aplica al crear un registro nuevo (no al editar)
                if self.tipo == 'INCREMENTO':
                    poa_update.monto_disponible += self.monto
                elif self.tipo == 'REDUCCION':
                    # Validación estricta de la Tarjeta 10
                    if poa_update.monto_disponible < self.monto:
                        raise ValidationError(
                            f"No es posible realizar la reducción. El monto solicitado ({self.monto:.2f} Bs.) "
                            f"supera el saldo disponible actual del POA ({poa_update.monto_disponible:.2f} Bs.)."
                        )
                    poa_update.monto_disponible -= self.monto

                # Guardamos el POA con su saldo recalculado
                poa_update.save()

                # Guardamos la modificación actual
                super().save(*args, **kwargs)

                # Registrar trazabilidad en la Bitácora de Auditoría (Tarjeta 23)
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
                # Si es una edición, no permitimos recalcular saldos para evitar inconsistencias históricas
                super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.tipo} - Bs. {self.monto} (POA ID: {self.poa.id})"

    class Meta:
        verbose_name = "Modificación Presupuestaria"
        verbose_name_plural = "Modificaciones Presupuestarias"