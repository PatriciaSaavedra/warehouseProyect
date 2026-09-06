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

    @property
    def progreso_porcentaje_sabs(self):
        """
        Retorna la anchura matemática exacta para el Stepper de 8 círculos del SABS (7 segmentos) [28].
        """
        map_estados = {
            'REGISTRADA': 0,              # Paso 1
            'REVISADA': 14,               # Paso 2
            'VALIDADA_SAF': 28,           # Paso 3
            'VALIDADA_PRESUPUESTOS': 42,   # Paso 4
            'VALIDADA_RPA': 57,            # Paso 5
            'VALIDADA_JEFATURA': 71,       # Paso 6
            'PREPARADA': 85,              # Paso 7
            'ENTREGADA': 100,             # Paso 8
            'CERRADA': 100,
            'RECHAZADA': 0,
        }
        return map_estados.get(self.estado, 0)
    @property
    def almacen_origen(self):
        """
        Tarjeta 9: Resuelve dinámicamente el almacén de origen del requerimiento.
        Si la Unidad Solicitante tiene un Subalmacén asignado, se usa ese;
        de lo contrario, se deriva automáticamente al Almacén Central de la Gobernación.
        """
        from inventario.models import Almacen
        
        # 1. Buscar un subalmacén activo asociado a la unidad solicitante
        subalmacen = Almacen.objects.filter(
            unidad_organizacional=self.unidad_solicitante, 
            is_active=True
        ).first()
        
        if subalmacen:
            return subalmacen
            
        # 2. Fallback al Almacén Central de la Gobernación
        return Almacen.objects.filter(tipo='CENTRAL', is_active=True).first()
    # Tarjeta 2: Calcular automáticamente el total referencial de la solicitud
    @property
    def total_referencial(self):
        return sum(detalle.subtotal_referencial for detalle in self.detalles.all())

    def tiene_detalles(self):
        return self.detalles.exists()

    def __str__(self):
        return self.codigo

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
    
    # CORREGIDO: Se alineó correctamente la sangría de este campo
    partida = models.ForeignKey(
        'inventario.PartidaPresupuestaria',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='detalles_solicitud'
    )
    
    es_nueva_adquisicion = models.BooleanField(default=False)
    descripcion_material_no_catalogado = models.CharField(max_length=200, blank=True, null=True)
    
    cantidad_solicitada = models.IntegerField()
    cantidad_aprobada = models.IntegerField(null=True, blank=True)
    cantidad_entregada = models.IntegerField(default=0)
    observacion = models.TextField(blank=True, null=True)
    
    precio_unitario_referencial = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=Decimal('0.00')
    )

    @property
    def subtotal_referencial(self):
        return Decimal(self.cantidad_solicitada) * self.precio_unitario_referencial

    # Tarjeta 8: Propiedad para obtener la partida independientemente de si el ítem es catalogado o no
    @property
    def partida_afectada(self):
        if self.material and self.material.partida:
            return self.material.partida
        return self.partida

    def __str__(self):
        if self.es_nueva_adquisicion:
            return f"{self.solicitud.codigo} - [No Catalogado] {self.descripcion_material_no_catalogado}"
        return f"{self.solicitud.codigo} - {self.material.nombre}"