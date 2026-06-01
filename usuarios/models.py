from django.db import models
from django.contrib.auth.models import User

from organizacion.models import UnidadOrganizacional
ROLES = [

    ('UNIDAD_SOLICITANTE', 'Unidad Solicitante'),

    ('JEFE_INMEDIATO', 'Jefe Inmediato Superior'),

    ('ALMACENERO', 'Almacenero'),

    ('KARDISTA', 'Kardista'),

    ('PRESUPUESTOS', 'Unidad de Presupuestos'),

    ('BIENES_SERVICIOS', 'Unidad de Bienes y Servicios'),

    ('PROVEEDOR', 'Proveedor'),

    ('ADMINISTRADOR', 'Administrador del Sistema'),

]


class PerfilUsuario(models.Model):

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE
    )

    unidad = models.ForeignKey(
        UnidadOrganizacional,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
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