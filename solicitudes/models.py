from django.db import models
from django.contrib.auth.models import User
from inventario.models import Material
from organizacion.models import UnidadOrganizacional 

# =========================================
# FLUJO DE ESTADOS OFICIAL (RE-SABS / GAD POTOSÍ)
# =========================================
ESTADOS_SOLICITUD = [
    ('REGISTRADA', 'Registrada'),       # Creada por el Funcionario Solicitante
    ('REVISADA', 'Revisada'),           # Validada por el Jefe Inmediato Superior
    ('APROBADA', 'Aprobada'),           # Aprobada por Presupuestos (POA) / Jefe de Almacenes
    ('PREPARADA', 'Preparada'),         # Alistada físicamente por el Almacenero
    ('ENTREGADA', 'Entregada'),         # Retirada físicamente (Descuenta Stock y POA)
    ('CERRADA', 'Cerrada'),             # Conclusión del trámite administrativo de almacén
    ('RECHAZADA', 'Rechazada'),         # Estado terminal en caso de observación presupuestaria o física
]


class Solicitud(models.Model):

    codigo = models.CharField(
        max_length=20,
        unique=True
    )

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
        default='REGISTRADA'  # <-- NUEVO ESTADO INICIAL
    )

    aprobado_por = models.CharField(
        max_length=200,
        blank=True,
        null=True
    )
    
    motivo_rechazo = models.TextField(
        blank=True,
        null=True
    )

    fecha_registro = models.DateTimeField(
        auto_now_add=True
    )

    def tiene_detalles(self):
        return self.detalles.exists()

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
    
    cantidad_solicitada = models.IntegerField()

    cantidad_aprobada = models.IntegerField(
        null=True,
        blank=True
    )

    cantidad_entregada = models.IntegerField(
        default=0
    )
    
    observacion = models.TextField(
        blank=True,
        null=True
    )

    def __str__(self):
        return f"{self.solicitud.codigo} - {self.material}"