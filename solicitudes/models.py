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
    @property
    def progreso_porcentaje(self):
        """
        Retorna el porcentaje numérico de avance del trámite para pintar el stepper en HTML sin ensuciar el CSS [28].
        """
        map_estados = {
            'REGISTRADA': 0,
            'REVISADA': 20,
            'APROBADA': 40,
            'PREPARADA': 60,
            'ENTREGADA': 80,
            'CERRADA': 100,
            'RECHAZADA': 0,
        }
        return map_estados.get(self.estado, 0)
    revisado_por = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='solicitudes_revisadas'
    )
    fecha_revision = models.DateTimeField(null=True, blank=True)
    
    presupuestado_por = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='solicitudes_presupuestadas'
    )
    fecha_presupuesto = models.DateTimeField(null=True, blank=True)
    
    preparado_por = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='solicitudes_preparadas'
    )
    fecha_preparado = models.DateTimeField(null=True, blank=True)
    
    entregado_por = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='solicitudes_entregadas'
    )
    fecha_entrega = models.DateTimeField(null=True, blank=True)
    
    cerrado_por = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='solicitudes_cerradas'
    )
    fecha_cierre = models.DateTimeField(null=True, blank=True)


# =========================================
# DETALLE SOLICITUD
# =========================================

class DetalleSolicitud(models.Model):
    solicitud = models.ForeignKey(
        Solicitud,
        on_delete=models.CASCADE,
        related_name='detalles'
    )
    # Hacemos el material opcional en la BD
    material = models.ForeignKey(
        Material,
        on_delete=models.CASCADE,
        null=True,
        blank=True
    )
    
    es_nueva_adquisicion = models.BooleanField(default=False)
    descripcion_material_no_catalogado = models.CharField(max_length=200, blank=True, null=True)
    
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
        if self.es_nueva_adquisicion:
            return f"{self.solicitud.codigo} - [No Catalogado] {self.descripcion_material_no_catalogado}"
        return f"{self.solicitud.codigo} - {self.material.nombre}"