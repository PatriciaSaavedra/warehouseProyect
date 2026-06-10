from .models import MovimientoInventario


def registrar_entrada(material, cantidad, usuario, referencia):

    stock_anterior = material.stock_actual

    material.stock_actual += cantidad
    material.save()

    MovimientoInventario.objects.create(
        material=material,
        tipo='ENTRADA',
        cantidad=cantidad,
        stock_anterior=stock_anterior,
        stock_resultante=material.stock_actual,
        referencia=referencia,
        usuario=usuario
    )