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

    # --- COMPATIBILIDAD CON ENFOQUE POA (ANTERIOR) ---
    def materiales_autorizados_poa(self, gestion=2026):
        """
        Retorna la lista de materiales autorizados para esta Unidad Organizacional,
        filtrando únicamente aquellos cuya Partida Presupuestaria está registrada en su POA activo.
        """
        from presupuestos.models import POA
        from inventario.models import Material

        partidas_permitidas = POA.objects.filter(
            unidad=self,
            gestion=gestion
        ).values_list('partida_id', flat=True)

        return Material.objects.filter(partida_id__in=partidas_permitidas)

    # --- NUEVO ENFOQUE: ASIGNACIÓN FÍSICA DIRECTA EN ENTRADAS (SIN POA) ---
    def materiales_asignados_disponibles(self):
        """
        Retorna los materiales que tienen saldo físico asignado a esta Unidad
        a través de las Notas de Ingreso (Entradas), sustituyendo al POA.
        """
        from inventario.models import Material, AsignacionEntradaUnidad
        from django.db.models import F

        # Obtenemos IDs de materiales donde cantidad_asignada > cantidad_retirada
        materiales_ids = AsignacionEntradaUnidad.objects.filter(
            unidad_organizacional=self,
            cantidad_asignada__gt=F('cantidad_retirada'),
            nota_ingreso_detalle__material__is_active=True
        ).values_list('nota_ingreso_detalle__material_id', flat=True).distinct()

        return Material.objects.filter(id__in=materiales_ids, is_active=True)

    def saldo_material_asignado(self, material):
        """
        Retorna la cantidad física total que le queda disponible a esta oficina para este material.
        """
        from inventario.models import AsignacionEntradaUnidad
        from django.db.models import Sum, F

        asignaciones = AsignacionEntradaUnidad.objects.filter(
            unidad_organizacional=self,
            nota_ingreso_detalle__material=material,
            cantidad_asignada__gt=F('cantidad_retirada')
        )
        total_asignado = asignaciones.aggregate(s=Sum('cantidad_asignada'))['s'] or 0
        total_retirado = asignaciones.aggregate(s=Sum('cantidad_retirada'))['s'] or 0
        return max(0, total_asignado - total_retirado)

    def __str__(self):
        if self.secretaria:
            return f"{self.nombre} ({self.secretaria.nombre})"
        return self.nombre


class UnidadAdministrativa(UnidadOrganizacional):
    class Meta:
        proxy = True
        verbose_name = "Unidad Administrativa"
        verbose_name_plural = "Unidades Administrativas"