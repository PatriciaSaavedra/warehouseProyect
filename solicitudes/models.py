from django.db import models
from django.contrib.auth.models import User
from inventario.models import Material
from organizacion.models import UnidadOrganizacional 

# =========================================
# FLUJO DE ESTADOS OFICIAL (RE-SABS / GAD POTOSÍ)
# =========================================
ESTADOS_SOLICITUD = [
    ('REGISTRADA', 'Creada'),
    ('VALIDADA_SAF', 'Validada por SAF'),
    ('VALIDADA_PRESUPUESTOS', 'Validada por Presupuestos'),
    ('VALIDADA_RPA', 'Validada por RPA'),
    ('VALIDADA_JEFATURA', 'Validada por Jefatura Administrativa'),
    ('PREPARADA', 'Preparada'),
    ('ENTREGADA', 'Entregada'),
    ('CERRADA', 'Cerrada'),
    ('RECHAZADA', 'Rechazada'),
]
class Solicitud(models.Model):
    codigo = models.CharField(max_length=20, unique=True)
    unidad_solicitante = models.ForeignKey('organizacion.UnidadOrganizacional', on_delete=models.CASCADE)
    solicitante = models.ForeignKey(User, on_delete=models.CASCADE)
    fecha = models.DateField()
    justificacion = models.TextField()
    estado = models.CharField(max_length=30, choices=ESTADOS_SOLICITUD, default='REGISTRADA')
    aprobado_por = models.CharField(max_length=200, blank=True, null=True)
    motivo_rechazo = models.TextField(blank=True, null=True)
    fecha_registro = models.DateTimeField(auto_now_add=True)

    # --- NUEVOS CAMPOS DE TRAZABILIDAD Y AUDITORÍA DE LA CADENA SABS ---
    revisado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='solicitudes_revisadas')
    fecha_revision = models.DateTimeField(null=True, blank=True)

    saf_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='saf_validados')
    fecha_saf = models.DateTimeField(null=True, blank=True)

    presupuestado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='presupuestos_validados')
    fecha_presupuesto = models.DateTimeField(null=True, blank=True)

    rpa_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='rpa_validados')
    fecha_rpa = models.DateTimeField(null=True, blank=True)

    jefatura_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='jefatura_validados')
    fecha_jefatura = models.DateTimeField(null=True, blank=True)

    preparado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='solicitudes_preparadas')
    fecha_preparado = models.DateTimeField(null=True, blank=True)

    entregado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='solicitudes_entregadas')
    fecha_entrega = models.DateTimeField(null=True, blank=True)

    cerrado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='solicitudes_cerradas')
    fecha_cierre = models.DateTimeField(null=True, blank=True)

    @property
    def progreso_porcentaje(self):
        """
        Retorna la anchura matemática exacta para el Stepper de 5 círculos en la interfaz [28].
        """
        map_estados = {
            'REGISTRADA': 0,              
            'REVISADA': 25,                
            'VALIDADA_SAF': 25,           
            'VALIDADA_RPA': 50,            
            'VALIDADA_PRESUPUESTOS': 50,   
            'VALIDADA_JEFATURA': 75,       
            'PREPARADA': 75,              
            'ENTREGADA': 100,             
            'CERRADA': 100,                
            'RECHAZADA': 0,
        }
        return map_estados.get(self.estado, 0)

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