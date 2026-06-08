from django.db import models
from django.contrib.auth.models import User


class Bitacora(models.Model):

    usuario = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True
    )

    accion = models.CharField(
        max_length=200
    )

    fecha = models.DateTimeField(
        auto_now_add=True
    )

    modulo = models.CharField(
        max_length=100
    )

    descripcion = models.TextField(
        blank=True,
        null=True
    )

    class Meta:
        ordering = ['-fecha']