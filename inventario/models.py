# --- TU ARCHIVO inventario/models.py COMPLETO Y UNIFICADO ---

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
    
    # Vinculación del subalmacén con su Unidad Organizacional correspondiente (GAD Potosí)
    unidad_organizacional = models.ForeignKey(
        'organizacion.UnidadOrganizacional',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='almacenes'
    )
    responsable = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='almacenes_bajo_responsabilidad'
    )
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.nombre} ({self.get_tipo_display()})"

    class Meta:
        verbose_name = "Almacén"
        verbose_name_plural = "Almacenes"


# ========================================================
# 2. MODELOS DE MATERIALES E INVENTARIO FÍSICO
# ========================================================

class Material(models.Model):
    partida = models.ForeignKey(
        PartidaPresupuestaria,
        on_delete=models.PROTECT,
        null=True,
        blank=True
    )
    codigo = models.CharField(max_length=50, unique=True)
    nombre = models.CharField(max_length=200)
    descripcion = models.TextField(blank=True, null=True)
    stock_actual = models.IntegerField(default=0)  # Iniciará en 0 al crearse
    stock_minimo = models.IntegerField(default=5)
    unidad_medida = models.CharField(max_length=50)
    unidad_medida_fk = models.ForeignKey(
        UnidadMedida,
        on_delete=models.PROTECT,
        null=True,
        blank=True
    )
    fecha_registro = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.nombre
    
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['partida', 'nombre'],
                name='material_unico_por_partida'
            )
        ]

    @property
    def tiene_movimientos(self):
        """
        Retorna True si el material ya cuenta con movimientos en el Kardex.
        """
        return self.movimientoinventario_set.exists()


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