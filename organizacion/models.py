# FILE: organizacion/models.py
from django.db import models

class Secretaria(models.Model):
    nombre = models.CharField(max_length=200, unique=True)
    codigo = models.CharField(max_length=50, blank=True, null=True)
    is_active = models.BooleanField(default=True)  # <-- CAMPO NUEVO PARA BAJA LÓGICA

    def __str__(self):
        return self.nombre


class UnidadOrganizacional(models.Model):
    nombre = models.CharField(max_length=200)
    codigo_sigep = models.CharField(max_length=50, blank=True, null=True)
    secretaria = models.ForeignKey(
        Secretaria,
        on_delete=models.CASCADE,
        related_name='unidades_organizacionales',
        null=True,
        blank=True
    )
    is_active = models.BooleanField(default=True)  # <-- CAMPO NUEVO PARA BAJA LÓGICA

    def __str__(self):
        if self.secretaria:
            return f"{self.nombre} ({self.secretaria.nombre})"
        return self.nombre


class UnidadAdministrativa(UnidadOrganizacional):
    class Meta:
        proxy = True
        verbose_name = "Unidad Administrativa"
        verbose_name_plural = "Unidades Administrativas"