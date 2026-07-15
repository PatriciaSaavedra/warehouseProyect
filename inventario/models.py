from django.db import models
from django.contrib.auth.models import User

class PartidaPresupuestaria(models.Model):
    codigo = models.CharField(max_length=20, unique=True)
    nombre = models.CharField(max_length=300)
    descripcion = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"{self.codigo} - {self.nombre}"

class UnidadMedida(models.Model):
    codigo = models.CharField(max_length=10, unique=True)
    nombre = models.CharField(max_length=100)

    def __str__(self):
        return f"{self.codigo} - {self.nombre}"

class Material(models.Model):
    partida = models.ForeignKey(
        'PartidaPresupuestaria',
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
        'UnidadMedida',
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

TIPOS_MOVIMIENTO = [
    ('ENTRADA', 'Entrada'),
    ('SALIDA', 'Salida'),
]

class MovimientoInventario(models.Model):
    material = models.ForeignKey(Material, on_delete=models.CASCADE)
    tipo = models.CharField(max_length=20, choices=TIPOS_MOVIMIENTO)
    cantidad = models.IntegerField()
    
    # --- NUEVOS CAMPOS PARA VALORACIÓN (RE-SABS) ---
    costo_unitario = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    costo_total = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    
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
        return f"{self.tipo} - {self.material.nombre} ({self.cantidad})"

class Proveedor(models.Model):
    """
    Catálogo de proveedores del GAD Potosí (Pág. 12 del Modelo de Objetos)
    """
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
    """
    Cabecera de la Nota de Ingreso de Almacén (Pág. 5 del Manual de Usuario)
    """
    nro_nota = models.CharField(max_length=50, unique=True, help_text="Número correlativo de nota de ingreso")
    proveedor = models.ForeignKey(Proveedor, on_delete=models.PROTECT, related_name='notas_ingreso')
    c31 = models.CharField(max_length=50, blank=True, null=True, help_text="Documento de gasto SIGEP C-31")
    nota_entrega = models.CharField(max_length=50, blank=True, null=True, help_text="Número de Nota de Entrega o Remisión")
    factura = models.CharField(max_length=50, blank=True, null=True, help_text="Número de Factura de Compra")
    reingreso = models.BooleanField(default=False, help_text="Indica si la entrada es por devolución/reingreso de oficina")
    fecha = models.DateField(help_text="Fecha de recepción física de los bienes")
    usuario = models.ForeignKey(User, on_delete=models.PROTECT, help_text="Almacenero que registra el ingreso")
    fecha_registro = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Nota de Ingreso Nro: {self.nro_nota} - {self.proveedor.razon_social}"

    class Meta:
        verbose_name = "Nota de Ingreso"
        verbose_name_plural = "Notas de Ingreso"


class NotaIngresoDetalle(models.Model):
    """
    Detalle de ítems asociados a una Nota de Ingreso (Estructura Multi-Ítem)
    """
    nota_ingreso = models.ForeignKey(NotaIngreso, on_delete=models.CASCADE, related_name='detalles')
    material = models.ForeignKey(Material, on_delete=models.PROTECT)
    cantidad = models.IntegerField()
    precio_unitario = models.DecimalField(max_digits=12, decimal_places=2)
    precio_total = models.DecimalField(max_digits=12, decimal_places=2)

    def __str__(self):
        return f"{self.material.nombre} - Cantidad: {self.cantidad}"