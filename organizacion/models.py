from django.db import models

class Secretaria(models.Model):
    """
    Representa una Secretaría Departamental (Nivel 2 / Nivel 3)
    del Gobierno Autónomo Departamental de Potosí.
    """
    nombre = models.CharField(max_length=200, unique=True)
    codigo = models.CharField(max_length=50, blank=True, null=True)

    def __str__(self):
        return self.nombre


class UnidadOrganizacional(models.Model):
    """
    Representa una Unidad Organizacional (Nivel 4 / Nivel 5)
    que depende de una Secretaría Departamental.
    
    Se mantiene este modelo como concreto para conservar la tabla original
    de la Base de Datos sin romper relaciones en otras apps (inventario, solicitudes) [28].
    """
    nombre = models.CharField(max_length=200)
    codigo_sigep = models.CharField(max_length=50, blank=True, null=True)
    secretaria = models.ForeignKey(
        Secretaria,
        on_delete=models.CASCADE,
        related_name='unidades_organizacionales',
        null=True,
        blank=True
    )

    def __str__(self):
        if self.secretaria:
            return f"{self.nombre} ({self.secretaria.nombre})"
        return self.nombre


class UnidadAdministrativa(UnidadOrganizacional):
    """
    Proxy model de UnidadOrganizacional. 
    Permite usar la nomenclatura de UnidadAdministrativa en formularios,
    vistas y código de forma transparente sin duplicar tablas en la BD [28].
    """
    class Meta:
        proxy = True
        verbose_name = "Unidad Administrativa"
        verbose_name_plural = "Unidades Administrativas"