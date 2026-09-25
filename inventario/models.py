from django.db import models
from django.contrib.auth.models import User
from decimal import Decimal

# ========================================================
# 1. MODELOS DE CONFIGURACIÓN Y CATÁLOGOS BASE
# ========================================================

class PartidaPresupuestaria(models.Model):
    codigo = models.CharField(max_length=20, unique=True)
    nombre = models.CharField(max_length=300)
    descripcion = models.CharField(max_length=500, blank=True, null=True)

    def __str__(self):
        return f"{self.codigo} - {self.nombre}"


class UnidadMedida(models.Model):
    codigo = models.CharField(max_length=10, unique=True)
    nombre = models.CharField(max_length=100)

    def __str__(self):
        return f"{self.codigo} - {self.nombre}"


class Almacen(models.Model):
    """
    Tarjeta 2: Gestionar Almacenes (Central y Subalmacenes vinculados a la estructura organizacional)
    """
    TIPO_ALMACEN = [
        ('CENTRAL', 'Almacén Central'),
        ('SUBALMACEN', 'Subalmacén / Seccional'),
    ]
    
    nombre = models.CharField(max_length=150, unique=True)
    descripcion = models.TextField(blank=True, null=True)
    tipo = models.CharField(max_length=20, choices=TIPO_ALMACEN, default='SUBALMACEN')
    
    # --- REQUERIMIENTO 10: Estructura para múltiples subalmacenes ---
    almacen_padre = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='subalmacenes',
        help_text="Si está en blanco, es almacén principal. Si tiene valor, depende de ese almacén."
    )
    
    # --- REQUERIMIENTO 1: Asociar almacén con Unidad Organizacional (A quién pertenece) ---
    unidad_organizacional = models.ForeignKey(
        'organizacion.UnidadOrganizacional',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='almacenes'
    )
    
    # --- REQUERIMIENTO 2: Definir unidad(es) organizacional(es) atendidas (A quiénes despacha) ---
    unidades_atendidas = models.ManyToManyField(
        'organizacion.UnidadOrganizacional',
        related_name='almacenes_que_atienden',
        blank=True,
        help_text="Unidades a las que este almacén está autorizado a despachar materiales."
    )

    # --- REQUERIMIENTO 8: Permitir identificar el responsable del almacén ---
    responsable = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='almacenes_bajo_responsabilidad'
    )
    is_active = models.BooleanField(default=True)

    def __str__(self):
        padre = f" (Sub de {self.almacen_padre.nombre})" if self.almacen_padre else ""
        return f"{self.nombre}{padre}"

    class Meta:
        verbose_name = "Almacén"
        verbose_name_plural = "Almacenes"
        

class Material(models.Model):
    partida = models.ForeignKey(PartidaPresupuestaria, on_delete=models.PROTECT, null=True, blank=True)
    codigo = models.CharField(max_length=50, unique=True)
    nombre = models.CharField(max_length=200)
    descripcion = models.TextField(blank=True, null=True)
    stock_actual = models.IntegerField(default=0)
    stock_minimo = models.IntegerField(default=5)
    unidad_medida = models.CharField(max_length=50)
    unidad_medida_fk = models.ForeignKey(UnidadMedida, on_delete=models.PROTECT, null=True, blank=True)
    fecha_registro = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    fecha_vencimiento = models.DateField(
        null=True, 
        blank=True, 
        help_text="Fecha de vencimiento opcional para productos perecederos (químicos, tintas, limpieza, etc.)"
    )

    @property
    def dias_para_vencer(self):
        if self.fecha_vencimiento:
            from django.utils import timezone
            return (self.fecha_vencimiento - timezone.now().date()).days
        return None

    @property
    def estado_vencimiento(self):
        dias = self.dias_para_vencer
        if dias is None:
            return 'SIN_VENCIMIENTO'
        if dias < 0:
            return 'VENCIDO'
        elif dias <= 30:
            return 'POR_VENCER_CRITICO'  # Menos de 30 días
        elif dias <= 60:
            return 'POR_VENCER_ALERTA'   # Menos de 60 días
        return 'VIGENTE'

class InventarioAlmacen(models.Model):
    """
    Tarjeta 5: Controlar y aislar la existencia física (stock) de cada material por Almacén.
    """
    material = models.ForeignKey(
        Material,
        on_delete=models.CASCADE,
        related_name='inventarios_almacen'
    )
    almacen = models.ForeignKey(
        Almacen,
        on_delete=models.CASCADE,
        related_name='inventarios'
    )
    # Existencia física real en estantería (Cajas físicas)
    stock_fisico = models.IntegerField(default=0)
    
    # Existencia reservada para pedidos autorizados en trámite
    stock_reservado = models.IntegerField(default=0)

    @property
    def stock_disponible(self):
        """
        Retorna el saldo neto real disponible para nuevas solicitudes.
        """
        return self.stock_fisico - self.stock_reservado

    def save(self, *args, **kwargs):
        """
        Al guardar existencias en un almacén, recalculamos automáticamente 
        el stock consolidado global en la ficha del Material para no romper vistas existentes.
        """
        from django.db import transaction
        with transaction.atomic():
            super().save(*args, **kwargs)
            # Recalcular stock acumulado total (Físico real)
            total_stock = sum(inv.stock_fisico for inv in self.material.inventarios_almacen.all())
            self.material.stock_actual = total_stock
            self.material.save()

    def __str__(self):
        return f"{self.material.nombre} en {self.almacen.nombre} - Físico: {self.stock_fisico} | Disp: {self.stock_disponible}"

    class Meta:
        verbose_name = "Inventario por Almacén"
        verbose_name_plural = "Inventarios por Almacén"
        unique_together = ('material', 'almacen')



# ========================================================
# 3. MOVIMIENTOS HISTÓRICOS (KARDEX / LOTES PEPS)
# ========================================================

TIPOS_MOVIMIENTO = [
    ('ENTRADA', 'Entrada'),
    ('SALIDA', 'Salida'),
]

class MovimientoInventario(models.Model):
    material = models.ForeignKey(Material, on_delete=models.CASCADE)
    
    # Tarjeta 6: Asociar el movimiento y lote PEPS a un almacén específico
    almacen = models.ForeignKey(
        Almacen,
        on_delete=models.PROTECT,
        related_name='movimientos',
        null=True,  
        blank=True,
        help_text="Almacén o subalmacén donde ocurre físicamente el movimiento"
    )
    
    tipo = models.CharField(max_length=20, choices=TIPOS_MOVIMIENTO)
    cantidad = models.IntegerField()
    costo_unitario = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    costo_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    stock_anterior = models.IntegerField(default=0)
    stock_resultante = models.IntegerField(default=0)
    referencia = models.CharField(max_length=100)
    fecha = models.DateTimeField(auto_now_add=True)
    fecha_vencimiento = models.DateField(
        null=True, 
        blank=True, 
        help_text="Fecha de caducidad/vencimiento para insumos perecederos (químicos, tintas, reactivos, etc.)"
    )
    usuario = models.ForeignKey(User, on_delete=models.CASCADE)
    saldo_disponible_lote = models.IntegerField(
        default=0, 
        help_text="Solo para ENTRADAS: Cantidad remanente de este lote para el costeo PEPS"
    )
    unidad_destino = models.ForeignKey(
        'organizacion.UnidadOrganizacional',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='consumos',
        help_text="Unidad organizacional de la Gobernación que consumió el material"
    )

    def __str__(self):
        return f"{self.tipo} - {self.material.nombre} ({self.cantidad}) en {self.almacen.nombre if self.almacen else 'Global'}"
    @property
    def valor_disponible_lote(self):
        """
        Calcula el valor monetario del saldo remanente de este lote específico.
        """
        return self.saldo_disponible_lote * self.costo_unitario
    @property
    def dias_para_vencer(self):
        if self.fecha_vencimiento:
            from django.utils import timezone
            hoy = timezone.now().date()
            return (self.fecha_vencimiento - hoy).days
        return None

    @property
    def estado_vencimiento(self):
        dias = self.dias_para_vencer
        if dias is None:
            return 'SIN_VENCIMIENTO'
        if dias < 0:
            return 'VENCIDO'
        elif dias <= 30:
            return 'POR_VENCER_CRITICO'
        elif dias <= 60:
            return 'POR_VENCER_ALERTA'
        return 'VIGENTE'
# ========================================================
# 4. COMPRAS (ENTRADAS DE ALMACÉN)
# ========================================================

class Proveedor(models.Model):
    nit = models.CharField(max_length=20, unique=True, help_text="NIT o documento de identificación")
    razon_social = models.CharField(max_length=200)
    telefono = models.CharField(max_length=20, blank=True, null=True)
    direccion = models.CharField(max_length=300, blank=True, null=True)

    def __str__(self):
        return self.razon_social
        
    class Meta:
        verbose_name = "Proveedor"
        verbose_name_plural = "Proveedores"


class NotaIngreso(models.Model):
    nro_nota = models.CharField(max_length=50, unique=True, help_text="Número correlativo de nota de ingreso")
    proveedor = models.ForeignKey(Proveedor, on_delete=models.PROTECT, related_name='notas_ingreso')
    
    # Tarjeta 7: Almacén destino de la recepción física de los bienes
    almacen_destino = models.ForeignKey(
        Almacen,
        on_delete=models.PROTECT,
        related_name='notas_ingreso',
        null=True,
        blank=True,
        help_text="Almacén o subalmacén donde ingresarán físicamente los bienes"
    )
    
    c31 = models.CharField(max_length=50, blank=True, null=True, help_text="Documento de gasto SIGEP C-31")
    nota_entrega = models.CharField(max_length=50, blank=True, null=True, help_text="Número de Nota de Entrega o Remisión")
    factura = models.CharField(max_length=50, blank=True, null=True, help_text="Número de Factura de Compra")
    reingreso = models.BooleanField(default=False, help_text="Indica si la entrada es por devolución/reingreso de oficina")
    fecha = models.DateField(help_text="Fecha de recepción física de los bienes")
    usuario = models.ForeignKey(User, on_delete=models.PROTECT, help_text="Almacenero que registra el ingreso")
    fecha_registro = models.DateTimeField(auto_now_add=True)
    compra_menor_origen = models.ForeignKey(
        'compras.CompraMenor',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='notas_ingreso_asociadas',
        help_text="Orden de Compra origen emitida por Bienes y Servicios"
    )

    def __str__(self):
        return f"Nota de Ingreso Nro: {self.nro_nota} - {self.proveedor.razon_social} -> {self.almacen_destino.nombre if self.almacen_destino else 'Central'}"

    class Meta:
        verbose_name = "Nota de Ingreso"
        verbose_name_plural = "Notas de Ingreso"


class NotaIngresoDetalle(models.Model):
    nota_ingreso = models.ForeignKey(NotaIngreso, on_delete=models.CASCADE, related_name='detalles')
    material = models.ForeignKey(Material, on_delete=models.PROTECT)
    cantidad = models.IntegerField()
    precio_unitario = models.DecimalField(max_digits=12, decimal_places=2)
    precio_total = models.DecimalField(max_digits=12, decimal_places=2)

    def __str__(self):
        return f"{self.material.nombre} - Cantidad: {self.cantidad}"

    class Meta:
        verbose_name = "Detalle de Nota de Ingreso"
        verbose_name_plural = "Detalles de Nota de Ingreso"


# ========================================================
# 5. SALIDAS DE ALMACÉN (EGRESOS FÍSICOS)
# ========================================================

class NotaSalida(models.Model):
    """
    Tarjeta 12: Cabecera de la Nota de Salida / Acta de Entrega de Almacén.
    """
    nro_nota = models.CharField(
        max_length=50, 
        unique=True, 
        help_text="Número correlativo de la nota de salida o acta de entrega"
    )
    solicitud_origen = models.ForeignKey(
        'solicitudes.Solicitud',  
        on_delete=models.PROTECT,
        related_name='notas_salida',
        help_text="Solicitud autorizada que originó el egreso físico"
    )
    # Tarjeta 12: Almacén de donde se retiró físicamente el stock
    almacen_origen = models.ForeignKey(
        Almacen,
        on_delete=models.PROTECT,
        related_name='notas_salida',
        null=True,
        blank=True,
        help_text="Almacén o subalmacén de donde se despacha el stock"
    )
    unidad_destino = models.ForeignKey(
        'organizacion.UnidadOrganizacional',
        on_delete=models.PROTECT,
        related_name='notas_salida',
        help_text="Unidad de la Gobernación que recibe el material"
    )
    fecha = models.DateField(
        help_text="Fecha de egreso físico y firma de entrega"
    )
    usuario = models.ForeignKey(
        User, 
        on_delete=models.PROTECT, 
        help_text="Almacenero que procesa el despacho"
    )
    fecha_registro = models.DateTimeField(
        auto_now_add=True
    )

    def __str__(self):
        return f"Nota de Salida Nro: {self.nro_nota} -> {self.unidad_destino.nombre}"

    class Meta:
        verbose_name = "Nota de Salida"
        verbose_name_plural = "Notas de Salida"


class NotaSalidaDetalle(models.Model):
    """
    Tarjeta 12: Detalle de ítems asociados a una Nota de Salida.
    """
    nota_salida = models.ForeignKey(
        NotaSalida, 
        on_delete=models.CASCADE, 
        related_name='detalles'
    )
    material = models.ForeignKey(
        Material, 
        on_delete=models.PROTECT
    )
    cantidad = models.IntegerField()
    costo_unitario_real = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        help_text="Costo real de salida determinado por el lote PEPS"
    )
    costo_total_real = models.DecimalField(
        max_digits=12, 
        decimal_places=2
    )

    def __str__(self):
        return f"{self.material.nombre} (Egreso) - Cantidad: {self.cantidad}"

    class Meta:
        verbose_name = "Detalle de Nota de Salida"
        verbose_name_plural = "Detalles de Nota de Salida"

class Transferencia(models.Model):
    """
    Tarjeta 21: Cabecera para el Traspaso/Transferencia física de stock entre almacenes.
    """
    ESTADOS_TRANSFERENCIA = [
        ('EN_TRANSITO', 'En Tránsito / Enviado'),
        ('RECIBIDA', 'Recibida / Consolidada'),
        ('RECHAZADA', 'Rechazada / Devuelta'),
    ]

    nro_transferencia = models.CharField(
        max_length=50, 
        unique=True, 
        help_text="Número correlativo oficial de transferencia (ej: TR-00001)"
    )
    origen = models.ForeignKey(
        Almacen, 
        on_delete=models.PROTECT, 
        related_name='transferencias_enviadas',
        help_text="Almacén despachador"
    )
    destino = models.ForeignKey(
        Almacen, 
        on_delete=models.PROTECT, 
        related_name='transferencias_recibidas',
        help_text="Almacén receptor"
    )
    estado = models.CharField(
        max_length=20, 
        choices=ESTADOS_TRANSFERENCIA, 
        default='EN_TRANSITO'
    )
    fecha_envio = models.DateTimeField(
        auto_now_add=True
    )
    fecha_recepcion = models.DateTimeField(
        null=True, 
        blank=True
    )
    usuario_envia = models.ForeignKey(
        User, 
        on_delete=models.PROTECT, 
        related_name='transferencias_despachadas',
        help_text="Almacenero que despacha del origen"
    )
    usuario_recibe = models.ForeignKey(
        User, 
        on_delete=models.PROTECT, 
        null=True, 
        blank=True, 
        related_name='transferencias_recepcionadas',
        help_text="Almacenero que acepta en el destino"
    )

    def __str__(self):
        return f"Transferencia {self.nro_transferencia}: {self.origen.nombre} -> {self.destino.nombre}"

    class Meta:
        verbose_name = "Transferencia entre Almacenes"
        verbose_name_plural = "Transferencias entre Almacenes"


class TransferenciaDetalle(models.Model):
    """
    Tarjeta 21: Detalle de ítems y valuación PEPS de la transferencia.
    """
    transferencia = models.ForeignKey(
        Transferencia, 
        on_delete=models.CASCADE, 
        related_name='detalles'
    )
    material = models.ForeignKey(
        Material, 
        on_delete=models.PROTECT
    )
    cantidad = models.IntegerField()
    
    # Registramos el costo obtenido del PEPS del origen para transferirlo con el mismo valor al destino
    costo_unitario_transferencia = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=0.00
    )
    costo_total_transferencia = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=0.00
    )

    def __str__(self):
        return f"{self.material.nombre} - Cantidad: {self.cantidad}"
# ========================================================
# 6. ASIGNACIÓN DE CUOTA FÍSICA POR UNIDAD (PROGRAMACIÓN ANUAL)
# ========================================================

class AsignacionMaterialUnidad(models.Model):
    """
    Control de Cuota Física Programada por Unidad Organizacional.
    Si una unidad tiene un registro aquí, el sistema aplica la modalidad CUOTA FÍSICA.
    Si no tiene registro, el sistema aplica BOLSA COMÚN contra el saldo de su POA.
    """
    unidad = models.ForeignKey(
        'organizacion.UnidadOrganizacional',
        on_delete=models.CASCADE,
        related_name='cuotas_materiales'
    )
    material = models.ForeignKey(
        Material,
        on_delete=models.CASCADE,
        related_name='cuotas_unidades'
    )
    gestion = models.IntegerField(
        default=2026,
        help_text="Gestión fiscal de la asignación"
    )
    cantidad_asignada = models.IntegerField(
        default=0,
        help_text="Cupo físico máximo autorizado para la gestión"
    )
    cantidad_consumida = models.IntegerField(
        default=0,
        help_text="Cantidad física retirada formalmente hasta la fecha"
    )

    @property
    def saldo_disponible(self):
        """Retorna la cantidad física que aún le queda por retirar a la oficina."""
        return max(0, self.cantidad_asignada - self.cantidad_consumida)

    def __str__(self):
        return f"{self.unidad.nombre} - {self.material.nombre}: {self.saldo_disponible}/{self.cantidad_asignada} ({self.gestion})"

    class Meta:
        verbose_name = "Asignación de Material por Unidad"
        verbose_name_plural = "Asignaciones de Material por Unidad"
        unique_together = ('unidad', 'material', 'gestion')