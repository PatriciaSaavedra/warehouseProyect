from django.db import models
from django.contrib.auth.models import User
from organizacion.models import Secretaria, UnidadAdministrativa

ROLES = [
    ('UNIDAD_SOLICITANTE', 'Unidad Solicitante'),
    ('JEFE_INMEDIATO', 'Jefe Inmediato Superior'),
    ('ALMACENERO', 'Almacenero'),
    ('KARDISTA', 'Kardista'),
    ('PRESUPUESTOS', 'Unidad de Presupuestos'),
    ('BIENES_SERVICIOS', 'Unidad de Bienes y Servicios'),
    ('ADMINISTRADOR', 'Administrador del Sistema'),
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
        max_length=20,
        choices=ROLES,
        default='UNIDAD_SOLICITANTE'
    )
    telefono = models.CharField(
        max_length=20,
        blank=True,
        null=True
    )

    def __str__(self):
        return self.user.username