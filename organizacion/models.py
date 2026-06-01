from django.db import models


TIPOS_UNIDAD = [

    ('SECRETARIA', 'Secretaría'),

    ('DIRECCION', 'Dirección'),

    ('JEFATURA', 'Jefatura'),

    ('AREA', 'Área'),

]   


class UnidadOrganizacional(models.Model):

    nombre = models.CharField(
        max_length=200
    )

    tipo = models.CharField(
        max_length=20,
        choices=TIPOS_UNIDAD
    )

    padre = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='dependencias'
    )

    def __str__(self):

        return f"{self.nombre} ({self.tipo})"