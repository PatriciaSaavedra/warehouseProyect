from django.db import models
from django.contrib.auth.models import User
from inventario.models import Material
from organizacion.models import UnidadOrganizacional 
from decimal import Decimal
FLUJOS_ATENCION = [
    ('SALIDA_ALMACEN', 'Salida de Almacén (Stock Disponible)'),
    ('ADQUISICION', 'Proceso de Adquisición (Sin Stock / Compra)'),
    ('CONTRATACION_SERVICIO', 'Contratación de Servicio'),
]

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

# OPCIONES DE REQUERIMIENTO (No se incluye "ACTIVO" de acuerdo a la regla funcional)
TIPO_REQUERIMIENTO_CHOICES = [
    ('BIEN', 'Bien'),
    ('SERVICIO', 'Servicio'),
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
    
    # Tarjeta 1: Incorporar tipo de requerimiento
    tipo_requerimiento = models.CharField(
        max_length=15, 
        choices=TIPO_REQUERIMIENTO_CHOICES, 
        default='BIEN'
    )
    flujo_atencion = models.CharField(
        max_length=30, 
        choices=FLUJOS_ATENCION, 
        null=True, 
        blank=True
    
    )

    # --- CAMPOS DE TRAZABILIDAD Y AUDITORÍA DE LA CADENA SABS ---
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

    # Tarjeta 2: Calcular automáticamente el total referencial de la solicitud
    @property
    def total_referencial(self):
        return sum(detalle.subtotal_referencial for detalle in self.detalles.all())

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
        on_delete=models.CASCADE,
        null=True,
        blank=True
    )
    
    es_nueva_adquisicion = models.BooleanField(default=False)
    descripcion_material_no_catalogado = models.CharField(max_length=200, blank=True, null=True)
    
    cantidad_solicitada = models.IntegerField()
    cantidad_aprobada = models.IntegerField(null=True, blank=True)
    cantidad_entregada = models.IntegerField(default=0)
    observacion = models.TextField(blank=True, null=True)

    # Tarjeta 2: Agregar precio unitario referencial al detalle de la solicitud
    precio_unitario_referencial = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=Decimal('0.00')
    )

    # Tarjeta 2: Calcular automáticamente el subtotal
    @property
    def subtotal_referencial(self):
        return Decimal(self.cantidad_solicitada) * self.precio_unitario_referencial

    def __str__(self):
        if self.es_nueva_adquisicion:
            return f"{self.solicitud.codigo} - [No Catalogado] {self.descripcion_material_no_catalogado}"
        return f"{self.solicitud.codigo} - {self.material.nombre}"


def determinar_y_asignar_flujo(self):
        """
        Determina y guarda automáticamente el flujo de atención:
        - Si es un Servicio -> Contratación de Servicio.
        - Si es un Bien y hay stock de todos los ítems -> Salida de Almacén.
        - Si es un Bien y falta stock en al menos un ítem -> Adquisición.
        """
        if self.tipo_requerimiento == 'SERVICIO':
            self.flujo_atencion = 'CONTRATACION_SERVICIO'
        else:
            hay_insuficiencia_stock = False
            
            # Validamos el stock disponible de cada uno de los detalles
            for detalle in self.detalles.all():
                # Si es una nueva adquisición no catalogada, asumimos sin stock
                if detalle.es_nueva_adquisicion or not detalle.material:
                    hay_insuficiencia_stock = True
                    break
                
                # Tarjeta 5: Comparar cantidad solicitada contra cantidad disponible
                if detalle.material.stock_actual < detalle.cantidad_solicitada:
                    hay_insuficiencia_stock = True
                    break
            
            if hay_insuficiencia_stock:
                self.flujo_atencion = 'ADQUISICION'
            else:
                self.flujo_atencion = 'SALIDA_ALMACEN'
        
        self.save()