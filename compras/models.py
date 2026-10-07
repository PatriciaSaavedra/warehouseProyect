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


# ========================================================
# 1. AUTORIDADES VIGENTES (RPA, SAF)
# ========================================================
class AutoridadInstitucional(models.Model):
    CARGOS_CHOICES = [
        ('RPA', 'Responsable del Proceso de Contratación (RPA)'),
        ('SAF', 'Secretario(a) Dptal. Administrativo y Financiero'),
        ('MAE', 'Máxima Autoridad Ejecutiva (Gobernador)'),
    ]

    cargo = models.CharField(max_length=10, choices=CARGOS_CHOICES)
    nombre_completo = models.CharField(max_length=200, help_text="Ej: Lic. Flora Colque Calizaya")
    resolucion_designacion = models.CharField(max_length=150, help_text="Ej: Resolución Administrativa N° 045/2026")
    fecha_designacion = models.DateField()
    fecha_vencimiento = models.DateField(blank=True, null=True, help_text="Vigencia de designación")
    is_active = models.BooleanField(default=True, help_text="Marca si esta autoridad está en funciones")

    def __str__(self):
        return f"{self.get_cargo_display()}: {self.nombre_completo}"

    class Meta:
        verbose_name = "Autoridad Institucional"
        verbose_name_plural = "Autoridades Institucionales"


# ========================================================
# 2. CATÁLOGO DE REQUISITOS DEL CHECKLIST
# ========================================================
class RequisitoCatalogo(models.Model):
    APLICA_A = [('BIEN', 'Solo Bienes'), ('SERVICIO', 'Solo Servicios'), ('AMBOS', 'Bienes y Servicios')]

    nombre = models.CharField(max_length=200)
    descripcion = models.CharField(max_length=300, blank=True, null=True)
    aplica_a = models.CharField(max_length=10, choices=APLICA_A, default='AMBOS')
    es_obligatorio = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.nombre} ({self.get_aplica_a_display()})"

    class Meta:
        verbose_name = "Requisito de Checklist"
        verbose_name_plural = "Requisitos de Checklist"


# ========================================================
# 3. PROCESO DE ADQUISICIÓN (CABECERA DEL FLUJO 2)
# ========================================================
class ProcesoAdquisicion(models.Model):
    TIPO_CHOICES = [
        ('BIEN', 'Bien (Adquisición Directa / Sin Stock)'),
        ('SERVICIO', 'Servicio (Contratación de Servicios)'),
    ]

    ESTADOS_CHOICES = [
        ('INICIADA', '1. Registrada'),
        ('CHECKLIST_VERIFICADO', '2. Checklist Aprobado'),
        ('NOTA_SAF_EMITIDA', '3. Nota Enviada a SAF'),
        ('CERTIFICADA', '4. Presupuesto Certificado'),
        ('APROBADA_RPA', '5. Aprobado por RPA'),
        ('ORDEN_EMITIDA', '6. Orden Emitida (Precio Oficial)'),
        ('RECEPCIONADA', '7. Recepcionado en Almacén (Documental)'),
        ('CONCLUIDA', '8. Trámite Concluido'),
        ('ANULADA', 'Anulada'),
    ]

    codigo = models.CharField(max_length=50, unique=True, help_text="Ej: ADQ-2026-0001")
    tipo = models.CharField(max_length=15, choices=TIPO_CHOICES, default='BIEN')
    unidad_solicitante = models.ForeignKey('organizacion.UnidadOrganizacional', on_delete=models.PROTECT)
    solicitante = models.ForeignKey(User, on_delete=models.PROTECT, related_name='adquisiciones_solicitadas')
    fecha_solicitud = models.DateField(auto_now_add=True)
    # Categoría Programática Oficial
    programa = models.CharField(max_length=20, default='000', help_text="Ej: 000")
    proyecto_actividad = models.CharField(max_length=20, default='001', help_text="Ej: 001 o código de proyecto")
    fuente_financiamiento = models.CharField(max_length=50, default='20', help_text="Ej: 20 - Recursos Específicos")
    organismo_financiador = models.CharField(max_length=50, default='220', help_text="Ej: 220 - Regalías")

    objeto_contratacion = models.CharField(max_length=300, help_text="Objeto de la contratación")
    justificacion = models.TextField()

    # CITEs y Trazabilidad
    cite_solicitud = models.CharField(max_length=100, blank=True, null=True, help_text="CITE inicial de la oficina")
    cite_nota_saf = models.CharField(max_length=100, blank=True, null=True, help_text="CITE de nota a SAF")
    cite_aprobacion_rpa = models.CharField(max_length=100, blank=True, null=True, help_text="CITE de nota/proveído RPA")

    # Autoridades que intervienen
    autoridad_saf = models.ForeignKey(AutoridadInstitucional, on_delete=models.SET_NULL, null=True, blank=True, related_name='adquisiciones_saf')
    autoridad_rpa = models.ForeignKey(AutoridadInstitucional, on_delete=models.SET_NULL, null=True, blank=True, related_name='adquisiciones_rpa')

    # Presupuestos (Certificación Presupuestaria Externa)
    certificacion_nro = models.CharField(max_length=50, blank=True, null=True, help_text="N° de Certificación Presupuestaria")
    partida = models.ForeignKey('inventario.PartidaPresupuestaria', on_delete=models.SET_NULL, null=True, blank=True)
    fuente_financiamiento = models.CharField(max_length=100, blank=True, null=True, help_text="Ej: 20 - Recursos Específicos")
    organismo_financiador = models.CharField(max_length=100, blank=True, null=True, help_text="Ej: 111 - Tesoro General")
    monto_certificado_bs = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))

    # Bienes y Servicios (Hecho en Bolivia y Adjudicación)
    hecho_en_bolivia = models.CharField(
        max_length=20,
        choices=[('SI', 'Sí, existe producción nacional'), ('NO', 'No existe producción nacional'), ('NO_APLICA', 'No Aplica')],
        default='NO_APLICA'
    )
    documento_hecho_en_bolivia = models.CharField(max_length=150, blank=True, null=True, help_text="N° de verificación digital Promueve Bolivia")

    proveedor = models.ForeignKey('inventario.Proveedor', on_delete=models.SET_NULL, null=True, blank=True)
    nro_orden_compra = models.CharField(max_length=100, blank=True, null=True, help_text="N° Orden de Compra o Servicio oficial")
    precio_oficial_adjudicado_bs = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))

    # Almacenes (DOCUMENTAL: CERO STOCK)
    recepcion_conforme = models.BooleanField(default=False)
    fecha_recepcion = models.DateField(blank=True, null=True)
    nro_nota_recepcion = models.CharField(max_length=50, blank=True, null=True)
    observaciones_recepcion = models.TextField(blank=True, null=True)

    estado = models.CharField(max_length=25, choices=ESTADOS_CHOICES, default='INICIADA')
    fecha_registro = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.codigo} ({self.get_tipo_display()}) - {self.unidad_solicitante.nombre}"

    class Meta:
        verbose_name = "Proceso de Adquisición (Flujo 2)"
        verbose_name_plural = "Procesos de Adquisición (Flujo 2)"
        ordering = ['-id']


# ========================================================
# 4. ÍTEMS DEL CHECKLIST POR PROCESO
# ========================================================
class ProcesoChecklistDetalle(models.Model):
    # NUEVO: Vinculado directamente al ítem específico
    item_adquisicion = models.ForeignKey(
        'ProcesoItemDetalle', 
        on_delete=models.CASCADE, 
        related_name='checklist_respuestas',
        null=True,
        blank=True
    )
    # Mantenemos proceso por compatibilidad
    proceso = models.ForeignKey(
        'ProcesoAdquisicion', 
        on_delete=models.CASCADE, 
        related_name='checklist_respuestas'
    )
    requisito = models.ForeignKey('RequisitoCatalogo', on_delete=models.PROTECT)
    estado = models.CharField(
        max_length=15,
        choices=[('CUMPLE', 'Cumple'), ('NO_CUMPLE', 'No Cumple / Pendiente'), ('NO_APLICA', 'No Aplica')],
        default='NO_CUMPLE'
    )
    documento_respaldo = models.CharField(max_length=200, blank=True, null=True, help_text="N° CITE / Folio de respaldo")
    observaciones = models.CharField(max_length=300, blank=True, null=True)

    def __str__(self):
        desc = self.item_adquisicion.descripcion[:20] if self.item_adquisicion else "General"
        return f"[{desc}] {self.requisito.nombre}: {self.estado}"

# ========================================================
# 5. ÍTEMS DEMANDADOS (BIENES O SERVICIOS) - NO STOCKEAN
# ========================================================
class ProcesoItemDetalle(models.Model):
    proceso = models.ForeignKey(ProcesoAdquisicion, on_delete=models.CASCADE, related_name='items')
    material = models.ForeignKey('inventario.Material', on_delete=models.SET_NULL, null=True, blank=True, help_text="Opcional si existe en catálogo")
    descripcion = models.TextField(help_text="Especificaciones técnicas o Términos de Referencia")
    unidad_medida = models.CharField(max_length=50, default='PIEZA')
    cantidad = models.IntegerField(default=1)
    precio_referencial_estimado = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    
    # --- CERTIFICACIÓN PRESUPUESTARIA POR ÍTEM ---
    partida = models.ForeignKey('inventario.PartidaPresupuestaria', on_delete=models.SET_NULL, null=True, blank=True)
    fuente_financiamiento = models.CharField(max_length=100, default='20 - Recursos Específicos')
    organismo_financiador = models.CharField(max_length=100, default='111 - TGN')
    monto_certificado = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))

    # --- ADJUDICACIÓN FINAL ---
    precio_oficial_unitario = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))

    @property
    def subtotal_referencial(self):
        return Decimal(self.cantidad) * self.precio_referencial_estimado

    @property
    def subtotal_oficial(self):
        return Decimal(self.cantidad) * self.precio_oficial_unitario

    def __str__(self):
        return f"{self.descripcion[:40]} ({self.cantidad} {self.unidad_medida})"