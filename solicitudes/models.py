from django.db import models
from django.contrib.auth.models import User
from inventario.models import Material


# =========================================
# SOLICITUD
# =========================================
ESTADOS_SOLICITUD = [

    ('PENDIENTE_JEFE', 'Pendiente Jefe'),

    ('VALIDADO', 'Validado'),

    ('RECHAZADO', 'Rechazado'),

    ('PENDIENTE_COMPRA', 'Pendiente Compra'),

    ('ENTREGADO', 'Entregado'),

]


class Solicitud(models.Model):

    codigo = models.CharField(
        max_length=20,
        unique=True
    )
    from organizacion.models import UnidadOrganizacional
    unidad_solicitante = models.ForeignKey(
        UnidadOrganizacional,
        on_delete=models.CASCADE
    )

    solicitante = models.ForeignKey(
        User,
        on_delete=models.CASCADE
    )

    fecha = models.DateField()

    justificacion = models.TextField()

    estado = models.CharField(
        max_length=20,
        choices=ESTADOS_SOLICITUD,
        default='PENDIENTE_JEFE'
    )

    aprobado_por = models.CharField(
        max_length=200,
        blank=True,
        null=True
    )

    fecha_registro = models.DateTimeField(
        auto_now_add=True
    )

    def __str__(self):

        return self.codigo


# =========================================
# DETALLE SOLICITUD
# =========================================

class DetalleSolicitud(models.Model):

    solicitud = models.ForeignKey(
        Solicitud,
        on_delete=models.CASCADE,
        related_name='detalles'
    )

    material = models.ForeignKey(
        Material,
        on_delete=models.CASCADE
    )
    cantidad = models.IntegerField()

    observacion = models.TextField(
        blank=True,
        null=True
    )

    def __str__(self):

        return f"{self.solicitud.codigo} - {self.material}"