from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.http import HttpResponse
from decimal import Decimal

from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, landscape

from usuarios.decorators import rol_requerido
from auditoria.models import Bitacora
from .models import (
    Material,
    MovimientoInventario,
    PartidaPresupuestaria,
    UnidadMedida,
    Proveedor,
    NotaIngreso,
    NotaIngresoDetalle
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
    """
    Genera el reporte PDF oficial del Kardex de Existencias Valorado (Pág. 12) [28].
    """
    material = get_object_or_404(Material, id=id)

    # Consultar movimientos en orden cronológico para calcular los saldos acumulados
    movimientos_db = MovimientoInventario.objects.filter(material=material).order_by('fecha')

    # 1. Definir los encabezados de doble nivel para la cuadrícula
    headers_nivel_1 = ['Fecha', 'Detalle / Referencia', 'Cantidades (Físico)', '', '', 'P. Unitario\n(Bs.)', 'Importes (Valorado)', '', '']
    headers_nivel_2 = ['', '', 'Entrada', 'Salida', 'Saldo', '', 'Entrada', 'Salida', 'Saldo']

    # Unir encabezados con el contenido procesado
    data = [headers_nivel_1, headers_nivel_2]

    saldo_fisico = 0
    saldo_valorado = Decimal('0.00')

    for mov in movimientos_db:
        if mov.tipo == 'ENTRADA':
            saldo_fisico += mov.cantidad
            saldo_valorado += mov.costo_total
            entrada_cant = str(mov.cantidad)
            salida_cant = ""
            entrada_imp = f"{mov.costo_total:.2f}"
            salida_imp = ""
        else:  # SALIDA
            saldo_fisico -= mov.cantidad
            saldo_valorado -= mov.costo_total
            entrada_cant = ""
            salida_cant = str(mov.cantidad)
            entrada_imp = ""
            salida_imp = f"{mov.costo_total:.2f}"

        data.append([
            mov.fecha.strftime('%d/%m/%Y'),
            mov.referencia,
            entrada_cant,
            salida_cant,
            str(saldo_fisico),
            f"{mov.costo_unitario:.2f}",
            entrada_imp,
            salida_imp,
            f"{saldo_valorado:.2f}"
        ])

    # 2. Configurar la respuesta HTTP en formato PDF
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="kardex_{material.codigo}.pdf"'

    # Usamos tamaño carta en formato apaisado (Landscape): 792 pt de ancho x 612 pt de alto
    pdf = canvas.Canvas(response, pagesize=landscape(letter))
    width, height = landscape(letter)

    # 3. Dibujar cabecera institucional del GAD Potosí
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(50, height - 50, "KARDEX DE EXISTENCIAS VALORADO")

    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(50, height - 75, "Subartículo:")
    pdf.setFont("Helvetica", 10)
    pdf.drawString(120, height - 75, material.nombre)

    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(50, height - 90, "Código:")
    pdf.setFont("Helvetica", 10)
    pdf.drawString(120, height - 90, material.codigo)

    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(420, height - 75, "Partida:")
    pdf.setFont("Helvetica", 10)
    pdf.drawString(480, height - 75, f"{material.partida.codigo} - {material.partida.nombre[:30]}")

    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(420, height - 90, "U. Medida:")
    pdf.setFont("Helvetica", 10)
    pdf.drawString(480, height - 90, material.unidad_medida)

    # 4. Configurar la Tabla con ReportLab
    # Suma de anchos de columna: 70 + 182 + (3 * 50) + 60 + (3 * 60) = 642pt (Dejando 50pt de margen a los lados)
    col_widths = [70, 182, 50, 50, 50, 60, 60, 60, 60]
    
    t = Table(data, colWidths=col_widths)

    # Estilos profesionales de la tabla (Sujeto a normas gubernamentales de almacén)
    t_style = TableStyle([
        # Uniones de celdas (SPAN) para los encabezados de doble nivel
        ('SPAN', (0, 0), (0, 1)),  # Fecha
        ('SPAN', (1, 0), (1, 1)),  # Detalle
        ('SPAN', (2, 0), (4, 0)),  # Cantidades (Físico)
        ('SPAN', (5, 0), (5, 1)),  # P. Unitario
        ('SPAN', (6, 0), (8, 0)),  # Importes (Valorado)

        # Alineaciones de contenido
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('ALIGN', (1, 2), (1, -1), 'LEFT'),      # Detalle a la izquierda
        ('ALIGN', (5, 2), (-1, -1), 'RIGHT'),    # Importes numéricos a la derecha

        # Fuentes de encabezado
        ('FONTNAME', (0, 0), (-1, 1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 1), 9),
        ('BACKGROUND', (0, 0), (-1, 1), colors.HexColor('#F3F4F6')), # Gris de fondo de cabecera

        # Bordes y grillas
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#D1D5DB')), # Grilla general del Kardex
        ('LINEBELOW', (0, 1), (-1, 1), 1, colors.HexColor('#9CA3AF')), # Línea gruesa de separación

        # Fuentes de contenido
        ('FONTNAME', (0, 2), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 2), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
    ])

    t.setStyle(t_style)

    # Calcular la altura necesaria para dibujar la tabla
    table_height = len(data) * 18  # Aproximadamente 18pt de altura por cada fila procesada
    
    # Dibujar la tabla estructurada en las coordenadas del canvas
    t.wrapOn(pdf, 50, height - 120 - table_height)
    t.drawOn(pdf, 50, height - 120 - table_height)

    pdf.save()

    # Registrar bitácora de auditoría
    Bitacora.objects.create(
        usuario=request.user,
        modulo='Inventario',
        accion='Exportar PDF Kardex',
        descripcion=f'Se descargó el PDF del Kardex de existencias para {material.nombre} ({material.codigo})'
    )

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

# FILE: inventario/views.py (Reemplazar la función kardex)
@login_required
@rol_requerido([
    'ALMACENERO',
    'KARDISTA',
    'ADMINISTRADOR'
])
def kardex(request, id):
    """
    Procesa y calcula el Kardex de Existencias Valorado cronológico (Pág. 12 del Manual) [28].
    """
    material = get_object_or_404(Material, id=id)
    
    # Consultamos los movimientos en orden cronológico ascendente para calcular los acumulados
    movimientos_db = MovimientoInventario.objects.filter(material=material).order_by('fecha')
    
    movimientos_valorados = []
    saldo_fisico = 0
    saldo_valorado = Decimal('0.00')

    for mov in movimientos_db:
        if mov.tipo == 'ENTRADA':
            saldo_fisico += mov.cantidad
            saldo_valorado += mov.costo_total
            entrada_cant = mov.cantidad
            salida_cant = 0
            entrada_imp = mov.costo_total
            salida_imp = Decimal('0.00')
        else:  # SALIDA
            saldo_fisico -= mov.cantidad
            saldo_valorado -= mov.costo_total
            entrada_cant = 0
            salida_cant = mov.cantidad
            entrada_imp = Decimal('0.00')
            salida_imp = mov.costo_total

        movimientos_valorados.append({
            'fecha': mov.fecha,
            'detalle': mov.referencia,
            'usuario': mov.usuario.username,
            # Físico (Cantidades)
            'entrada_cant': entrada_cant,
            'salida_cant': salida_cant,
            'saldo_cant': saldo_fisico,
            # Valoración
            'precio_unitario': mov.costo_unitario,
            # Valorado (Importes)
            'entrada_importe': entrada_imp,
            'salida_importe': salida_imp,
            'saldo_importe': saldo_valorado,
        })

    # Invertimos la lista para mostrar el historial más reciente primero en la tabla del HTML
    movimientos_valorados.reverse()

    return render(
        request,
        'inventario/kardex.html',
        {
            'material': material,
            'movimientos': movimientos_valorados
        }
    )
@login_required
@rol_requerido(['ALMACENERO', 'ADMINISTRADOR'])
def entrada_inventario(request):
    """
    Registra una Nota de Ingreso formal multi-ítem con proveedor y costos (Pág. 5 del Manual).
    """
    proveedores = Proveedor.objects.all().order_by('razon_social')
    materiales = Material.objects.all().order_by('codigo')

    if request.method == 'POST':
        proveedor_id = request.POST.get('proveedor')
        c31 = request.POST.get('c31', '').strip()
        nota_entrega = request.POST.get('nota_entrega', '').strip()
        factura = request.POST.get('factura', '').strip()
        reingreso = request.POST.get('reingreso') == 'on'
        fecha = request.POST.get('fecha')

        # Listas dinámicas enviadas desde la tabla dinámica del HTML
        materiales_ids = request.POST.getlist('material_id[]')
        cantidades = request.POST.getlist('cantidad[]')
        precios_unitarios = request.POST.getlist('precio_unitario[]')

        # Validaciones de cabecera e ítems mínimos
        if not proveedor_id or not fecha:
            messages.error(request, 'El proveedor y la fecha de ingreso son campos obligatorios.')
            return redirect('entrada_inventario')

        if not materiales_ids or len(materiales_ids) == 0:
            messages.error(request, 'Debe registrar al menos un material en el detalle de la nota.')
            return redirect('entrada_inventario')

        proveedor = get_object_or_404(Proveedor, id=proveedor_id)

        # Autogenerar nro de nota correlativa automática
        total_notas = NotaIngreso.objects.count() + 1
        nro_nota = f"NI-{str(total_notas).zfill(5)}"

        try:
            with transaction.atomic():
                # 1. Crear cabecera de la Nota de Ingreso
                nota = NotaIngreso.objects.create(
                    nro_nota=nro_nota,
                    proveedor=proveedor,
                    c31=c31 if c31 else None,
                    nota_entrega=nota_entrega if nota_entrega else None,
                    factura=factura if factura else None,
                    reingreso=reingreso,
                    fecha=fecha,
                    usuario=request.user
                )

                # 2. Procesar cada material en el lote
                for i in range(len(materiales_ids)):
                    m_id = materiales_ids[i]
                    cant = int(cantidades[i])
                    p_uni = Decimal(precios_unitarios[i])
                    p_tot = cant * p_uni

                    if cant <= 0 or p_uni < 0:
                        raise ValueError("Cantidad o precio unitario inválido en el detalle.")

                    material = get_object_or_404(Material, id=m_id)
                    stock_anterior = material.stock_actual

                    # Incrementar stock del material
                    material.stock_actual += cant
                    material.save()

                    # Registrar detalle de la Nota de Ingreso
                    NotaIngresoDetalle.objects.create(
                        nota_ingreso=nota,
                        material=material,
                        cantidad=cant,
                        precio_unitario=p_uni,
                        precio_total=p_tot
                    )

                    # Registrar movimiento físico-valorado en el Kardex
                    MovimientoInventario.objects.create(
                        material=material,
                        tipo='ENTRADA',
                        cantidad=cant,
                        costo_unitario=p_uni,
                        costo_total=p_tot,
                        stock_anterior=stock_anterior,
                        stock_resultante=material.stock_actual,
                        referencia=f"NOTA INGRESO NRO {nro_nota}",
                        usuario=request.user
                    )

                # Registrar auditoría
                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Inventario',
                    accion='Registrar Nota de Ingreso',
                    descripcion=f'Se registró la Nota de Ingreso {nro_nota} de {proveedor.razon_social} con {len(materiales_ids)} ítems.'
                )

            messages.success(request, f'Nota de Ingreso {nro_nota} procesada y stock actualizado de manera exitosa.')
            return redirect('inventario')

        except Exception as e:
            messages.error(request, f'Error al registrar el ingreso: {str(e)}')
            return redirect('entrada_inventario')

    return render(
        request,
        'inventario/entrada.html',
        {
            'proveedores': proveedores,
            'materiales': materiales
        }
    )

@login_required
@rol_requerido([
    'ALMACENERO',
    'ADMINISTRADOR'
])
def salida_inventario(request):

    materiales = Material.objects.all()

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

        # VALIDAR STOCK

        if cantidad > material.stock_actual:

            return render(
                request,
                'inventario/salida.html',
                {
                    'materiales': materiales,
                    'error': 'Stock insuficiente.'
                }
            )

        # DESCONTAR STOCK

        material.stock_actual -= cantidad

        material.save()

        # REGISTRAR MOVIMIENTO

        MovimientoInventario.objects.create(

            material=material,

            tipo='SALIDA',

            cantidad=cantidad,

            referencia=referencia,

            usuario=request.user

        )

        return redirect('inventario')

    return render(
        request,
        'inventario/salida.html',
        {
            'materiales': materiales
        }
    )
@login_required
@rol_requerido(['ALMACENERO', 'ADMINISTRADOR'])
def nuevo_material(request):
    if request.method == 'POST':
        nombre = request.POST.get('nombre', '').strip()
        descripcion = request.POST.get('descripcion', '').strip()
        unidad_id = request.POST.get('unidad_medida_fk')
        partida_id = request.POST.get('partida')
        stock_minimo = int(request.POST.get('stock_minimo', 5))

        unidad = get_object_or_404(UnidadMedida, id=unidad_id)
        partida = get_object_or_404(PartidaPresupuestaria, id=partida_id)

        # Validar existencia de duplicados
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
                    'error': 'Ya existe un material con ese nombre en esta partida presupuestaria.'
                }
            )

        # Generar código correlativo de manera automática
        materiales_partida = Material.objects.filter(partida=partida).order_by('codigo')
        if materiales_partida.exists():
            ultimo_codigo = materiales_partida.last().codigo
            try:
                correlativo = int(ultimo_codigo.split('-')[1]) + 1
            except (ValueError, IndexError):
                correlativo = 1
        else:
            correlativo = 1

        codigo = f"{partida.codigo}-{str(correlativo).zfill(4)}"

        # El material se crea estrictamente con stock_actual en 0.
        with transaction.atomic():
            material = Material.objects.create(
                partida=partida,
                codigo=codigo,
                nombre=nombre,
                descripcion=descripcion,
                unidad_medida=unidad.nombre,
                unidad_medida_fk=unidad,
                stock_actual=0,  # Stock inicial en cero
                stock_minimo=stock_minimo
            )
            
            Bitacora.objects.create(
                usuario=request.user,
                modulo='Inventario',
                accion='Creación de material',
                descripcion=f'Se registró el nuevo subartículo: {nombre} ({codigo})'
            )

        messages.success(request, f'Material {nombre} registrado correctamente con stock inicial en 0. Proceda a establecer su saldo inicial.')
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
@rol_requerido(['ALMACENERO', 'ADMINISTRADOR'])
def establecer_saldo_inicial(request, id):
    """
    Registra el saldo inicial físico y valorado para un subartículo (Pág 4 del Manual).
    """
    material = get_object_or_404(Material, id=id)

    # Validación de control interno: evitar duplicar saldos iniciales si ya cuenta con movimientos
    if MovimientoInventario.objects.filter(material=material, referencia='STOCK INICIAL').exists():
        messages.error(request, f'El material {material.nombre} ya tiene un saldo inicial registrado.')
        return redirect('inventario')

    if request.method == 'POST':
        cantidad = int(request.POST.get('cantidad', 0))
        costo_unitario_raw = request.POST.get('costo_unitario', '0')

        if cantidad <= 0:
            messages.error(request, 'La cantidad del saldo inicial debe ser mayor a cero.')
            return redirect('establecer_saldo_inicial', id=id)

        try:
            costo_unitario = Decimal(costo_unitario_raw)
            if costo_unitario < 0:
                raise ValueError
        except (ValueError, ArithmeticError):
            messages.error(request, 'El costo unitario debe ser un valor decimal válido y no negativo.')
            return redirect('establecer_saldo_inicial', id=id)

        costo_total = cantidad * costo_unitario
        stock_anterior = material.stock_actual

        try:
            with transaction.atomic():
                # Actualizar stock físico en el material
                material.stock_actual += cantidad
                material.save()

                # Crear el movimiento valorado en el Kardex
                MovimientoInventario.objects.create(
                    material=material,
                    tipo='ENTRADA',
                    cantidad=cantidad,
                    costo_unitario=costo_unitario,
                    costo_total=costo_total,
                    stock_anterior=stock_anterior,
                    stock_resultante=material.stock_actual,
                    referencia='STOCK INICIAL',
                    usuario=request.user
                )

                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Inventario',
                    accion='Establecer Saldo Inicial',
                    descripcion=f'Saldo inicial para {material.nombre}: {cantidad} u. a un costo unitario de {costo_unitario} Bs.'
                )

            messages.success(request, f'Saldo inicial establecido para {material.nombre} por {cantidad} unidades.')
            return redirect('inventario')

        except Exception:
            messages.error(request, 'Ocurrió un problema de base de datos al registrar el saldo inicial.')
            return redirect('establecer_saldo_inicial', id=id)

    return render(
        request,
        'inventario/establecer_saldo_inicial.html',
        {
            'material': material
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