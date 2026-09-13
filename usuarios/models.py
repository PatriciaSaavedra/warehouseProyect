from django.db import models
from django.contrib.auth.models import User
from organizacion.models import Secretaria, UnidadAdministrativa


ROLES = [
    ('UNIDAD_SOLICITANTE', 'Unidad Solicitante'),
    ('JEFE_INMEDIATO', 'Jefe Inmediato Superior'),

    # Almacenes
    ('ALMACENERO', 'Almacenero'),
    ('KARDISTA', 'Kardista'),
    ('ADMIN_ALMACENES', 'Administrador de Almacenes'),

    # Presupuesto y adquisiciones
    ('PRESUPUESTOS', 'Unidad de Presupuestos'),
    ('BIENES_SERVICIOS', 'Unidad de Bienes y Servicios'),

    # Administración del sistema
    ('ADMINISTRADOR', 'Administrador del Sistema'),

    # Otros responsables del proceso
    ('SECRETARIO_SAF', 'Secretario de la SAF'),
    ('RPA', 'Responsable del Proceso de Contratación (RPA)'),
    ('JEFE_ADMINISTRATIVO', 'Jefe Administrativo'),
]


class PerfilUsuario(models.Model):

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='perfilusuario'
    )

    secretaria = models.ForeignKey(
        Secretaria,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='perfiles_usuarios'
    )

    unidad = models.ForeignKey(
        UnidadAdministrativa,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='perfiles_usuarios'
    )

    rol = models.CharField(
        max_length=30,
        choices=ROLES,
        default='UNIDAD_SOLICITANTE'
    )

    telefono = models.CharField(
        max_length=20,
        blank=True,
        null=True
    )

    # Almacenes que el usuario tiene asignados
    # para realizar operaciones.
    almacenes_autorizados = models.ManyToManyField(
        'inventario.Almacen',
        blank=True,
        related_name='usuarios_autorizados',
        help_text='Almacenes en los que el usuario puede realizar operaciones.'
    )

    # ---------------------------------------------------------
    # ACCESO A ALMACENES (UN OBJETO ESPECÍFICO)
    # ---------------------------------------------------------

    def puede_ver_almacen(self, almacen):
        """
        Determina si el usuario puede consultar información de un almacén.
        """
        if self.rol in ['ADMINISTRADOR', 'ADMIN_ALMACENES'] or self.user.is_superuser:
            return True

        # Si es el responsable directo en el modelo Almacen
        if almacen.responsable_id == self.user_id:
            return True

        # Almacenes asignados en M2M
        return self.almacenes_autorizados.filter(pk=almacen.pk).exists()

    def puede_operar_almacen(self, almacen):
        """
        Determina si el usuario puede realizar operaciones (entradas, salidas, ajustes).
        """
        if self.rol == 'ADMINISTRADOR' or self.user.is_superuser:
            return True

        # El Administrador de Almacenes puede operar solo si está asignado o es el responsable
        # (para evitar que registre salidas accidentales en depósitos donde no está físicamente)
        if almacen.responsable_id == self.user_id:
            return True

        return self.almacenes_autorizados.filter(pk=almacen.pk).exists()

    def tiene_acceso_almacen(self, almacen):
        """
        Compatibilidad con vistas y services ya existentes.
        """
        return self.puede_operar_almacen(almacen)

    # ---------------------------------------------------------
    # CONSULTAS DE QUERYSET (LISTAS DE ALMACENES PARA VISTAS)
    # ---------------------------------------------------------

    def get_almacenes_visibles(self):
        """
        Retorna el QuerySet de almacenes que este usuario puede consultar.
        Ideal para poblar tablas y selectores de lectura.
        """
        from inventario.models import Almacen

        if self.rol in ['ADMINISTRADOR', 'ADMIN_ALMACENES'] or self.user.is_superuser:
            return Almacen.objects.filter(is_active=True).order_by('nombre')

        return Almacen.objects.filter(
            models.Q(id__in=self.almacenes_autorizados.values_list('id', flat=True)) |
            models.Q(responsable=self.user),
            is_active=True
        ).distinct().order_by('nombre')

    def get_almacenes_operables(self):
        """
        Retorna el QuerySet de almacenes donde este usuario puede registrar movimientos.
        Ideal para selectores de Entradas, Salidas y Ajustes físicos.
        """
        from inventario.models import Almacen

        if self.rol == 'ADMINISTRADOR' or self.user.is_superuser:
            return Almacen.objects.filter(is_active=True).order_by('nombre')

        return Almacen.objects.filter(
            models.Q(id__in=self.almacenes_autorizados.values_list('id', flat=True)) |
            models.Q(responsable=self.user),
            is_active=True
        ).distinct().order_by('nombre')

    # ---------------------------------------------------------
    # PROPIEDADES DE APOYO (HELPERS)
    # ---------------------------------------------------------

    @property
    def es_admin_almacen_o_superior(self):
        return self.rol in ['ADMINISTRADOR', 'ADMIN_ALMACENES'] or self.user.is_superuser

    def __str__(self):
        return self.user.get_full_name() or self.user.username