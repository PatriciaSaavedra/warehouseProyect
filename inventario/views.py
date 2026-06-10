from django.shortcuts import render, redirect
from usuarios.utils import rol_requerido
from .models import MovimientoInventario
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404
from django.http import HttpResponse
from reportlab.pdfgen import canvas
from .services import registrar_entrada
from .models import (
    Material,
    MovimientoInventario,
    PartidaPresupuestaria,
    UnidadMedida
)

@login_required
@rol_requerido([
    'ALMACENERO',
    'KARDISTA',
    'ADMINISTRADOR'
])

def inventario_view(request):

    materiales = Material.objects.all()

    return render(
        request,
        'inventario/index.html',
        {
            'materiales': materiales
        }
    )
@login_required
@rol_requerido([
    'ALMACENERO',
    'KARDISTA',
    'ADMINISTRADOR'
])
def reporte_inventario(request):

    response = HttpResponse(content_type='application/pdf')

    response['Content-Disposition'] = (
        'attachment; filename="inventario.pdf"'
    )

    pdf = canvas.Canvas(response)

    # TITULO

    pdf.setFont("Helvetica-Bold", 16)

    pdf.drawString(
        200,
        800,
        "REPORTE DE INVENTARIO"
    )

    # DATOS

    materiales = Material.objects.all()

    y = 750

    pdf.setFont("Helvetica", 12)

    for material in materiales:

        texto = (
            f"{material.codigo} | "
            f"{material.nombre} | "
            f"Stock: {material.stock_actual}"
        )

        pdf.drawString(50, y, texto)

        y -= 25

    pdf.save()

    return response
@login_required
@rol_requerido([
    'ALMACENERO',
    'KARDISTA',
    'ADMINISTRADOR'
])
def kardex_pdf(request, id):

    material = get_object_or_404(Material, id=id)

    movimientos = MovimientoInventario.objects.filter(
        material=material
    ).order_by('-fecha')

    response = HttpResponse(
        content_type='application/pdf'
    )

    response['Content-Disposition'] = (
        f'attachment; filename="kardex_{material.nombre}.pdf"'
    )

    pdf = canvas.Canvas(response)

    # TITULO

    pdf.setFont("Helvetica-Bold", 16)

    pdf.drawString(
        180,
        800,
        f"KARDEX - {material.nombre}"
    )

    # INFO MATERIAL

    pdf.setFont("Helvetica", 12)

    pdf.drawString(
        50,
        760,
        f"Codigo: {material.codigo}"
    )

    pdf.drawString(
        50,
        740,
        f"Stock Actual: {material.stock_actual}"
    )

    # TABLA

    y = 700

    pdf.setFont("Helvetica-Bold", 11)

    pdf.drawString(30, y, "Fecha")
    pdf.drawString(100, y, "Tipo")
    pdf.drawString(170, y, "Cantidad")
    pdf.drawString(250, y, "Antes")
    pdf.drawString(320, y, "Despues")
    pdf.drawString(420, y, "Referencia")
    y -= 30

    pdf.setFont("Helvetica", 10)

    for movimiento in movimientos:

        pdf.drawString(
            30,
            y,
            movimiento.fecha.strftime('%d/%m/%Y')
        )

        pdf.drawString(
            100,
            y,
            movimiento.tipo
        )

        pdf.drawString(
            170,
            y,
            str(movimiento.cantidad)
        )

        pdf.drawString(
            250,
            y,
            str(movimiento.stock_anterior)
        )

        pdf.drawString(
            320,
            y,
            str(movimiento.stock_resultante)
        )

        pdf.drawString(
            420,
            y,
            movimiento.referencia[:20]
        )

        y -= 20

    pdf.save()

    return response

@login_required
@rol_requerido([
    'ALMACENERO',
    'KARDISTA',
    'ADMINISTRADOR'
])
def movimientos(request):

    movimientos = MovimientoInventario.objects.all().order_by('-id')

    return render(
        request,
        'inventario/movimientos.html',
        {
            'movimientos': movimientos
        }
    )

@login_required
@rol_requerido([
    'ALMACENERO',
    'KARDISTA',
    'ADMINISTRADOR'
])
def kardex(request, id):

    material = get_object_or_404(Material, id=id)

    movimientos = MovimientoInventario.objects.filter(
        material=material
    ).order_by('-fecha')

    return render(
        request,
        'inventario/kardex.html',
        {
            'material': material,
            'movimientos': movimientos
        }
    )
@login_required
@rol_requerido([
    'ALMACENERO',
    'ADMINISTRADOR'
])
def entrada_inventario(request):

    materiales = Material.objects.all()

    if request.method == 'POST':

        material_id = request.POST.get('material')

        cantidad = int(request.POST.get('cantidad'))

        referencia = request.POST.get('referencia')

        material = Material.objects.get(id=material_id)

        # SUMAR STOCK

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
            usuario=request.user
        )

        return redirect('inventario')

    return render(
        request,
        'inventario/entrada.html',
        {
            'materiales': materiales
        }
    )
@login_required
@rol_requerido([
    'ALMACENERO',
    'ADMINISTRADOR'
])
def nuevo_material(request):

    if request.method == 'POST':

        nombre = request.POST.get('nombre')

        descripcion = request.POST.get('descripcion')

        unidad_id = request.POST.get(
            'unidad_medida_fk'
        )

        unidad = UnidadMedida.objects.get(
            id=unidad_id
        )
        partida_id = request.POST.get('partida')
        stock_actual = int(request.POST.get('stock_actual'))

        stock_minimo = int(request.POST.get('stock_minimo'))

        partida = PartidaPresupuestaria.objects.get(
            id=partida_id
        )
        material_existente = Material.objects.filter(
            partida=partida,
            nombre__iexact=nombre
        ).exists()

        if material_existente:

            return render(
                request,
                'inventario/nuevo_material.html',
                {
                    'partidas': PartidaPresupuestaria.objects.all(),
                    'unidades': UnidadMedida.objects.all(),
                    'error': 'Ya existe un material con ese nombre en esta partida.'
                }
            )
        materiales_partida = Material.objects.filter(
            partida=partida
        ).order_by('codigo')

        if materiales_partida.exists():

            ultimo_codigo = materiales_partida.last().codigo

            correlativo = int(
                ultimo_codigo.split('-')[1]
            ) + 1

        else:

            correlativo = 1

        codigo = (
            f"{partida.codigo}-"
            f"{str(correlativo).zfill(4)}"
        )
        material = Material.objects.create(

            partida=partida,

            codigo=codigo,

            nombre=nombre,

            descripcion=descripcion,

            unidad_medida=unidad.nombre,

            unidad_medida_fk=unidad,

            stock_actual=stock_actual,

            stock_minimo=stock_minimo

        )
        # REGISTRAR STOCK INICIAL

        MovimientoInventario.objects.create(

            material=material,

            tipo='ENTRADA',

            cantidad=stock_actual,

            stock_anterior=0,

            stock_resultante=stock_actual,

            referencia='STOCK INICIAL',

            usuario=request.user

        )
        return redirect('inventario')

    return render(
        request,
        'inventario/nuevo_material.html',
        {
            'partidas': PartidaPresupuestaria.objects.all(),
            'unidades': UnidadMedida.objects.all()
        }
    )
@login_required
@rol_requerido([
    'ALMACENERO',
    'ADMINISTRADOR'
])
def editar_material(request, id):

    material = get_object_or_404(Material, id=id)

    if request.method == 'POST':

        nombre = request.POST.get('nombre')
        existe = Material.objects.filter(
            partida=material.partida,
            nombre__iexact=nombre
        ).exclude(
            id=material.id
        ).exists()

        if existe:

            return render(
                request,
                'inventario/editar_material.html',
                {
                    'material': material,
                    'unidades': UnidadMedida.objects.all(),
                    'error': (
                        'Ya existe un material con ese nombre '
                        'en esta partida.'
                    )
                }
            )
        material.nombre = nombre
        material.descripcion = request.POST.get('descripcion')

        unidad_id = request.POST.get(
            'unidad_medida_fk'
        )

        unidad = UnidadMedida.objects.get(
            id=unidad_id
        )

        material.unidad_medida_fk = unidad

        material.unidad_medida = unidad.nombre

        material.stock_minimo = int(
            request.POST.get('stock_minimo')
        )

        material.save()

        return redirect('inventario')
        
    return render(
        request,
        'inventario/editar_material.html',
        {
            'material': material,
            'unidades': UnidadMedida.objects.all()     
        }
    )
@login_required
@rol_requerido([
    'ADMINISTRADOR'
])
def eliminar_material(request, id):

    material = get_object_or_404(Material, id=id)

    material.delete()

    return redirect('inventario')

@login_required
@rol_requerido([
    'ALMACENERO',
    'ADMINISTRADOR'
])
def salida_inventario(request):
    
    materiales = Material.objects.all()
    material_preseleccionado = request.GET.get('material')
    if request.method == 'POST':

        material_id = request.POST.get('material')

        cantidad = int(
            request.POST.get('cantidad')
        )

        referencia = request.POST.get(
            'referencia'
        )

        material = Material.objects.get(
            id=material_id
        )

        if cantidad > material.stock_actual:

            return render(
                request,
                'inventario/salida.html',
                {
                    'materiales': materiales,
                    'error': (
                        'Stock insuficiente'
                    )
                }
            )

        stock_anterior = material.stock_actual

        material.stock_actual -= cantidad

        material.save()

        MovimientoInventario.objects.create(

            material=material,

            tipo='SALIDA',

            cantidad=cantidad,

            stock_anterior=stock_anterior,

            stock_resultante=material.stock_actual,

            referencia=referencia,

            usuario=request.user
        )

        return redirect('inventario')

    return render(
        request,
        'inventario/salida.html',
        {
            'materiales': materiales,
            'material_preseleccionado': material_preseleccionado
        }
    )