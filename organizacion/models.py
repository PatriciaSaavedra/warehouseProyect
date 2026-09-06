from django.db import models

class Secretaria(models.Model):
    nombre = models.CharField(max_length=200, unique=True)
    codigo = models.CharField(max_length=50, blank=True, null=True)
    is_active = models.BooleanField(default=True)

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
    is_active = models.BooleanField(default=True)

    # Tarjeta 17: Retorna únicamente los materiales permitidos para esta Unidad según su POA 2026
    def materiales_autorizados_poa(self, gestion=2026):
        """
        Retorna la lista de materiales autorizados para esta Unidad Organizacional,
        filtrando únicamente aquellos cuya Partida Presupuestaria está registrada en su POA activo.
        """
        from presupuestos.models import POA
        from inventario.models import Material

        # Obtenemos los IDs de las partidas que tienen fondos asignados en el POA de esta unidad
        partidas_permitidas = POA.objects.filter(
            unidad=self,
            gestion=gestion
        ).values_list('partida_id', flat=True)

        # Retornamos solo los materiales que pertenezcan a esas partidas permitidas
        return Material.objects.filter(partida_id__in=partidas_permitidas)

    def __str__(self):
        if self.secretaria:
            return f"{self.nombre} ({self.secretaria.nombre})"
        return self.nombre


class UnidadAdministrativa(UnidadOrganizacional):
    class Meta:
        proxy = True
        verbose_name = "Unidad Administrativa"
        verbose_name_plural = "Unidades Administrativas"