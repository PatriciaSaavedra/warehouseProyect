from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.http import HttpResponse, JsonResponse 
from decimal import Decimal
from django.db.models import Sum
from django.utils.dateparse import parse_date
import datetime

from auditoria.models import Bitacora
from organizacion.models import UnidadOrganizacional

from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, landscape

from usuarios.decorators import rol_requerido
from .services import registrar_salida_valorada_peps

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
def kardex_pdf(request, id):
    """
    Genera el reporte PDF oficial del Kardex de Existencias Valorado en Vista Previa (Pág. 12) [28].
    Versión blindada con coordenadas absolutas de grilla para evitar KeyErrors en ReportLab [28].
    """
    material = get_object_or_404(Material, id=id)

    # Consultar movimientos en orden cronológico para calcular los saldos acumulados
    movimientos_db = MovimientoInventario.objects.filter(material=material).order_by('fecha')

    # 1. Definir los encabezados de doble nivel para la cuadrícula (9 Columnas exactas) [28]
    data = [
        ['Fecha', 'Detalle / Referencia', 'Cantidades (Físico)', '', '', 'P. Unitario\n(Bs.)', 'Importes (Valorado)', '', ''],
        ['', '', 'Entrada', 'Salida', 'Saldo', '', 'Entrada', 'Salida', 'Saldo']
    ]

    saldo_fisico = 0
    saldo_valorado = Decimal('0.00')

    # 2. Procesar los movimientos convirtiendo preventivamente todas las celdas a strings [28]
    for mov in movimientos_db:
        if mov.tipo == 'ENTRADA':
            saldo_fisico += mov.cantidad
            saldo_valorado += (mov.costo_total or Decimal('0.00'))
            ent_cant = str(mov.cantidad)
            sal_cant = ""
            ent_imp = f"{(mov.costo_total or Decimal('0.00')):.2f}"
            sal_imp = ""
        else:  # SALIDA o BAJA
            saldo_fisico -= mov.cantidad
            saldo_valorado -= (mov.costo_total or Decimal('0.00'))
            ent_cant = ""
            sal_cant = str(mov.cantidad)
            ent_imp = ""
            sal_imp = f"{(mov.costo_total or Decimal('0.00')):.2f}"

        # Añadir fila con 9 elementos exactos
        data.append([
            str(mov.fecha.strftime('%d/%m/%Y')),
            str(mov.referencia or ''),
            str(ent_cant),
            str(sal_cant),
            str(saldo_fisico),
            f"{(mov.costo_unitario or Decimal('0.00')):.2f}",
            str(ent_imp),
            str(sal_imp),
            f"{saldo_valorado:.2f}"
        ])

    # 3. Configurar respuesta PDF (Inline para Vista Previa)
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="kardex_{material.codigo}.pdf"'

    # Formato horizontal Landscape
    pdf = canvas.Canvas(response, pagesize=landscape(letter))
    width, height = landscape(letter)

    # Configurar metadatos para la pestaña del navegador [28]
    pdf.setTitle(f"Kardex Valorado - {material.codigo}")
    pdf.setSubject("SGA - Gobierno Autónomo Departamental de Potosí")
    pdf.setAuthor("Sistema de Gestión de Almacenes")

    # Dibujar cabecera del documento
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

    # FILE: inventario/views.py (Sección final de la función kardex_pdf corregida)

    # 4. Construir la Tabla con ReportLab (Usando límites absolutos) [28]
    col_widths = [70, 182, 50, 50, 50, 60, 60, 60, 60]
    t = Table(data, colWidths=col_widths)

    # Calculamos la última fila absoluta para evitar el bug del '-1' de ReportLab [28]
    last_row = len(data) - 1

    t_style = TableStyle([
        # SPAN para encabezados de doble nivel
        ('SPAN', (0, 0), (0, 1)),  # Fecha
        ('SPAN', (1, 0), (1, 1)),  # Detalle
        ('SPAN', (2, 0), (4, 0)),  # Cantidades (Físico)
        ('SPAN', (5, 0), (5, 1)),  # P. Unitario
        ('SPAN', (6, 0), (8, 0)),  # Importes (Valorado)

        # Alineaciones de datos
        ('ALIGN', (0, 0), (8, last_row), 'CENTER'),
        ('ALIGN', (1, 2), (1, last_row), 'LEFT'),      # Detalle a la izquierda
        ('ALIGN', (5, 2), (8, last_row), 'RIGHT'),     # Números a la derecha

        # Fuentes y colores de cabecera
        ('FONTNAME', (0, 0), (8, 1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (8, 1), 9),
        ('BACKGROUND', (0, 0), (8, 1), colors.HexColor('#F3F4F6')), 

        # Grillas y bordes
        ('GRID', (0, 0), (8, last_row), 0.5, colors.HexColor('#D1D5DB')), 

        # Fuentes de contenido
        ('FONTNAME', (0, 2), (8, last_row), 'Helvetica'),
        ('FONTSIZE', (0, 2), (8, last_row), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
    ])
    t.setStyle(t_style)

    # =========================================================================
    # --- CORRECCIÓN DEFINITIVA: RENDERIZADO PRECIOSO SIN TRUNCADO [28] ---
    # =========================================================================
    avail_width = width - 100
    avail_height = height - 150

    # 1. Recuperamos el alto real exacto compilado por ReportLab (h_actual) [28]
    w_actual, h_actual = t.wrapOn(pdf, avail_width, avail_height)

    # 2. Posicionamos el tope de la tabla a una distancia fija y segura del encabezado (height - 125) [28]
    # Calculamos la coordenada Y de la base restando la altura real compilada (h_actual) [28]
    pdf_y = height - 125 - h_actual
    t.drawOn(pdf, 50, pdf_y)
    # =========================================================================

    pdf.save()

    # Registrar en bitácora de auditoría
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
def reporte_inventario(request):
    """
    Genera el reporte de Inventario Físico Valorado tabular en Vista Previa (Inciso e / Pág. 10 del Manual) [28].
    """
    desde_str = request.GET.get('desde')
    hasta_str = request.GET.get('hasta')

    # Convertir o usar valores por defecto para la gestión de almacén (2026)
    if desde_str:
        desde = parse_date(desde_str)
    else:
        desde = datetime.date(2026, 1, 1)

    if hasta_str:
        hasta = parse_date(hasta_str)
    else:
        hasta = datetime.date(2026, 12, 31)

    # Consultar catálogo ordenado por código correlativo
    materiales = Material.objects.all().order_by('codigo')

    # Encabezados de doble nivel reglamentarios
    headers_1 = ['Código', 'Descripción del Material', 'Unid.', 'Saldo Inicial / Apertura', '', 'Entradas del Periodo', '', 'Salidas del Periodo', '', 'Saldos de Cierre', '']
    headers_2 = ['', '', '', 'Cant.', 'Importe (Bs.)', 'Cant.', 'Importe (Bs.)', 'Cant.', 'Importe (Bs.)', 'Cant.', 'Importe (Bs.)']

    data = [headers_1, headers_2]

    # Procesar matemáticamente los saldos físicos y monetarios por período
    for mat in materiales:
        mov_previos = MovimientoInventario.objects.filter(material=mat, fecha__date__lt=desde)
        ini_cant = 0
        ini_val = Decimal('0.00')
        for m in mov_previos:
            if m.tipo == 'ENTRADA':
                ini_cant += m.cantidad
                ini_val += m.costo_total
            else:
                ini_cant -= m.cantidad
                ini_val -= m.costo_total

        mov_periodo = MovimientoInventario.objects.filter(material=mat, fecha__date__range=[desde, hasta])
        ent_cant = 0
        ent_val = Decimal('0.00')
        sal_cant = 0
        sal_val = Decimal('0.00')
        for m in mov_periodo:
            if m.tipo == 'ENTRADA':
                ent_cant += m.cantidad
                ent_val += m.costo_total
            else:
                sal_cant += m.cantidad
                sal_val += m.costo_total

        # Cálculo de Saldos de Cierre
        fin_cant = ini_cant + ent_cant - sal_cant
        fin_val = ini_val + ent_val - sal_val

        data.append([
            mat.codigo,
            mat.nombre[:35], 
            mat.unidad_medida_fk.codigo if mat.unidad_medida_fk else mat.unidad_medida,
            str(ini_cant),
            f"{ini_val:.2f}",
            str(ent_cant),
            f"{ent_val:.2f}",
            str(sal_cant),
            f"{sal_val:.2f}",
            str(fin_cant),
            f"{fin_val:.2f}"
        ])

    response = HttpResponse(content_type='application/pdf')
    # Cambiado a 'inline' para vista previa en el navegador [28]
    response['Content-Disposition'] = f'inline; filename="inventario_fisico_valorado_{desde}_{hasta}.pdf"'

    pdf = canvas.Canvas(response, pagesize=landscape(letter))
    width, height = landscape(letter)

    # =========================================================================
    # --- NUEVO: CONFIGURAR METADATOS DEL DOCUMENTO PARA EL NAVEGADOR ---
    # =========================================================================
    pdf.setTitle(f"Inventario Valorado ({desde} a {hasta})")  # Nombrar tu pestaña automáticamente [28]
    pdf.setSubject("SGA - Gobierno Autónomo Departamental de Potosí")
    pdf.setAuthor("Sistema de Gestión de Almacenes")
    # =========================================================================

    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(50, height - 50, "INVENTARIO FÍSICO VALORADO DE ALMACENES")
    
    pdf.setFont("Helvetica", 10)
    pdf.drawString(50, height - 75, "Gobierno Autónomo Departamental de Potosí")
    pdf.drawString(50, height - 90, f"Período de Evaluación: Desde {desde.strftime('%d/%m/%Y')} hasta {hasta.strftime('%d/%m/%Y')}")

    # Configurar la Tabla con ReportLab
    col_widths = [65, 142, 35, 45, 60, 45, 60, 45, 60, 45, 65]
    t = Table(data, colWidths=col_widths)

    t_style = TableStyle([
        ('SPAN', (0, 0), (0, 1)),  
        ('SPAN', (1, 0), (1, 1)),  
        ('SPAN', (2, 0), (2, 1)),  
        ('SPAN', (3, 0), (4, 0)),  
        ('SPAN', (5, 0), (6, 0)),  
        ('SPAN', (7, 0), (8, 0)),  
        ('SPAN', (9, 0), (10, 0)), 

        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('ALIGN', (1, 2), (1, -1), 'LEFT'),  
        ('ALIGN', (4, 2), (4, -1), 'RIGHT'), 
        ('ALIGN', (6, 2), (6, -1), 'RIGHT'),
        ('ALIGN', (8, 2), (8, -1), 'RIGHT'),
        ('ALIGN', (10, 2), (10, -1), 'RIGHT'),

        ('FONTNAME', (0, 0), (-1, 1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 1), 8),
        ('BACKGROUND', (0, 0), (-1, 1), colors.HexColor('#F3F4F6')),

        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#D1D5DB')),
        ('LINEBELOW', (0, 1), (-1, 1), 1, colors.HexColor('#9CA3AF')),

        ('FONTNAME', (0, 2), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 2), (-1, -1), 7.5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
    ])
    t.setStyle(t_style)

    table_height = len(data) * 16  
    t.wrapOn(pdf, 50, height - 120 - table_height)
    t.drawOn(pdf, 50, height - 120 - table_height)

    pdf.save()

    # Registrar en bitácora
    Bitacora.objects.create(
        usuario=request.user,
        modulo='Inventario',
        accion='Exportar Inventario Valorado',
        descripcion=f'Se exportó el reporte de Inventario Físico Valorado desde {desde} hasta {hasta}.'
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
from compras.models import CompraMenor  # Asegura esta importación

@login_required
@rol_requerido(['ALMACENERO', 'ADMINISTRADOR'])
def entrada_inventario(request):
    """
    Registra una Nota de Ingreso jalando los datos de la Orden de Compra y actualizando su estado [28].
    """
    # Obtenemos las Órdenes de Compra pendientes de ingreso de la gestión actual [28]
    compras_pendientes = CompraMenor.objects.filter(completada=False, gestion=2026).select_related('proveedor', 'partida')
    
    proveedores = Proveedor.objects.all().order_by('razon_social')
    materiales = Material.objects.all().order_by('codigo')

    if request.method == 'POST':
        compra_id = request.POST.get('compra_origen')  # <-- Capturamos la Orden seleccionada
        proveedor_id = request.POST.get('proveedor')
        c31 = request.POST.get('c31', '').strip()
        nota_entrega = request.POST.get('nota_entrega', '').strip()
        factura = request.POST.get('factura', '').strip()
        reingreso = request.POST.get('reingreso') == 'on'
        fecha = request.POST.get('fecha')

        materiales_ids = request.POST.getlist('material_id[]')
        cantidades = request.POST.getlist('cantidad[]')
        precios_unitarios = request.POST.getlist('precio_unitario[]')

        if not proveedor_id or not fecha:
            messages.error(request, 'El proveedor y la fecha de ingreso son campos obligatorios.')
            return redirect('entrada_inventario')

        if not materiales_ids:
            messages.error(request, 'Debe registrar al menos un material en el detalle del ingreso.')
            return redirect('entrada_inventario')

        proveedor = get_object_or_404(Proveedor, id=proveedor_id)
        compra_origen = None
        if compra_id:
            compra_origen = get_object_or_404(CompraMenor, id=compra_id)

        # Autogenerar nro de nota correlativa automática
        total_notas = NotaIngreso.objects.count() + 1
        nro_nota = f"NI-{str(total_notas).zfill(5)}"

        try:
            with transaction.atomic():
                # 1. Crear cabecera de la Nota de Ingreso vinculándola a la compra
                nota = NotaIngreso.objects.create(
                    nro_nota=nro_nota,
                    proveedor=proveedor,
                    c31=c31 if c31 else None,
                    nota_entrega=nota_entrega if nota_entrega else None,
                    factura=factura if factura else None,
                    reingreso=reingreso,
                    fecha=fecha,
                    usuario=request.user,
                    compra_menor_origen=compra_origen  # <-- GUARDAMOS EL VÍNCULO
                )

                # 2. Si venía de una compra, la marcamos como completada (Ingresada) [28]
                if compra_origen:
                    compra_origen.completada = True
                    compra_origen.save()

                # 3. Procesar cada material en el lote PEPS
                for i in range(len(materiales_ids)):
                    m_id = materiales_ids[i]
                    cant = int(cantidades[i])
                    p_uni = Decimal(precios_unitarios[i])
                    p_tot = cant * p_uni

                    material = get_object_or_404(Material, id=m_id)
                    stock_anterior = material.stock_actual

                    # Incrementar stock del material
                    material.stock_actual += cant
                    material.save()

                    # Registrar detalle
                    NotaIngresoDetalle.objects.create(
                        nota_ingreso=nota,
                        material=material,
                        cantidad=cant,
                        precio_unitario=p_uni,
                        precio_total=p_tot
                    )

                    # Registrar en el Kardex alimentando el saldo disponible PEPS
                    MovimientoInventario.objects.create(
                        material=material,
                        tipo='ENTRADA',
                        cantidad=cant,
                        saldo_disponible_lote=cant,  # Alimentamos lote PEPS
                        costo_unitario=p_uni,
                        costo_total=p_tot,
                        stock_anterior=stock_anterior,
                        stock_resultante=material.stock_actual,
                        referencia=f"NOTA INGRESO NRO {nro_nota}",
                        usuario=request.user
                    )

                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Inventario',
                    accion='Registrar Nota de Ingreso',
                    descripcion=f'Se registró la Nota de Ingreso {nro_nota} por compra {nro_nota}'
                )

            messages.success(request, f'Nota de Ingreso {nro_nota} procesada correctamente.')
            return redirect('inventario')

        except Exception as e:
            messages.error(request, f'Error al registrar el ingreso: {str(e)}')
            return redirect('entrada_inventario')

    return render(
        request,
        'inventario/entrada.html',
        {
            'compras_pendientes': compras_pendientes,  # <-- ENVIAMOS LAS COMPRAS PENDIENTES
            'proveedores': proveedores,
            'materiales': materiales
        }
    )

@login_required
@rol_requerido(['ALMACENERO', 'ADMINISTRADOR'])
def salida_inventario(request):
    materiales = Material.objects.filter(stock_actual__gt=0)
    unidades = UnidadOrganizacional.objects.all().order_by('nombre')  # <-- OBTENER UNIDADES
    material_preseleccionado = request.GET.get('material')
    
    if request.method == 'POST':
        material_id = request.POST.get('material')
        cantidad = int(request.POST.get('cantidad', 0))
        referencia = request.POST.get('referencia', '').strip()
        unidad_destino_id = request.POST.get('unidad_destino')  # <-- CAPTURAR UNIDAD

        if not material_id or not unidad_destino_id or cantidad <= 0:
            messages.error(request, 'Debe completar todos los campos obligatorios.')
            return redirect('salida_inventario')

        material = get_object_or_404(Material, id=material_id)
        unidad_destino = get_object_or_404(UnidadOrganizacional, id=unidad_destino_id)

        if cantidad > material.stock_actual:
            messages.error(request, 'No existe stock suficiente disponible para este despacho.')
            return redirect('salida_inventario')

        try:
            # Procesar el egreso valorado PEPS asociándolo a la unidad destino
            registrar_salida_valorada_peps(
                material=material,
                cantidad_salida=cantidad,
                tipo_movimiento='SALIDA',
                referencia=referencia,
                usuario=request.user,
                unidad_destino=unidad_destino  # <-- ENVIAR AL SERVICIO
            )

            Bitacora.objects.create(
                usuario=request.user,
                modulo='Inventario',
                accion='Registrar Salida de Material',
                descripcion=f'Despacho de {cantidad} u. de {material.nombre} a la unidad: {unidad_destino.nombre}. Ref: {referencia}'
            )

            messages.success(request, f'Salida de {cantidad} unidades a {unidad_destino.nombre} procesada correctamente.')
            return redirect('inventario')

        except Exception as e:
            messages.error(request, f'Error al procesar la salida PEPS: {str(e)}')
            return redirect('salida_inventario')

    return render(
        request,
        'inventario/salida.html',
        {
            'materiales': materiales,
            'unidades': unidades,  # <-- ENVIAR A LA PLANTILLA
            'material_preseleccionado': material_preseleccionado
        }
    )

@login_required
@rol_requerido(['ALMACENERO', 'ADMINISTRADOR'])
def registrar_baja(request):
    """
    Registra pérdidas, mermas o vencimientos de stock respaldados por informe técnico (Inciso m del RE-SABS) [28].
    Aplica el algoritmo de costeo PEPS para registrar financieramente la baja en el Kardex [28].
    """
    # Filtramos para mostrar únicamente materiales que posean existencias físicas en el almacén
    materiales = Material.objects.filter(stock_actual__gt=0).order_by('codigo')
    material_preseleccionado = request.GET.get('material')

    if request.method == 'POST':
        material_id = request.POST.get('material')
        cantidad = int(request.POST.get('cantidad', 0))
        motivo = request.POST.get('motivo', '').strip()  # Ej: Vencimiento / Caducidad, Rotura / Daño Físico...
        referencia = request.POST.get('referencia', '').strip()  # Ej: Informe Técnico o Resolución

        # Validación de campos obligatorios
        if not material_id or not motivo or not referencia:
            messages.error(request, 'Todos los campos son obligatorios.')
            return redirect('registrar_baja')

        material = get_object_or_404(Material, id=material_id)

        # Validación de límites de stock físico
        if cantidad <= 0:
            messages.error(request, 'La cantidad de baja debe ser mayor a cero.')
            return redirect('registrar_baja')

        if cantidad > material.stock_actual:
            messages.error(request, 'No puede dar de baja una cantidad superior al stock actual disponible en almacén.')
            return redirect('registrar_baja')

        try:
            # Procesar el egreso valorado mediante el algoritmo PEPS (FIFO)
            registrar_salida_valorada_peps(
                material=material,
                cantidad_salida=cantidad,
                tipo_movimiento='BAJA',
                referencia=f"BAJA: {motivo} ({referencia})",
                usuario=request.user
            )

            # Registrar la auditoría transaccional correspondiente en la Bitácora
            Bitacora.objects.create(
                usuario=request.user,
                modulo='Inventario',
                accion='Registrar Baja de Almacén',
                descripcion=f'Baja de {cantidad} u. de {material.nombre} por motivo de {motivo}. Ref: {referencia}'
            )

            messages.success(request, f'Baja de {cantidad} unidades de {material.nombre} procesada correctamente.')
            return redirect('inventario')

        except Exception as e:
            messages.error(request, f'Ocurrió un error al procesar la baja de material: {str(e)}')
            return redirect('registrar_baja')

    return render(
        request,
        'inventario/baja.html',
        {
            'materiales': materiales,
            'material_preseleccionado': material_preseleccionado
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
                    saldo_disponible_lote=cantidad,
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
@rol_requerido(['ALMACENERO', 'ADMINISTRADOR'])
def salida_inventario(request):
    materiales = Material.objects.all()
    material_preseleccionado = request.GET.get('material')
    
    if request.method == 'POST':
        material_id = request.POST.get('material')
        cantidad = int(request.POST.get('cantidad', 0))
        referencia = request.POST.get('referencia', '').strip()

        material = get_object_or_404(Material, id=material_id)

        if cantidad <= 0:
            messages.error(request, 'La cantidad de salida debe ser mayor a cero.')
            return redirect('salida_inventario')

        if cantidad > material.stock_actual:
            messages.error(request, 'Stock insuficiente disponible.')
            return redirect('salida_inventario')

        try:
            # Consumir lotes de manera cronológica usando PEPS (FIFO)
            registrar_salida_valorada_peps(
                material=material,
                cantidad_salida=cantidad,
                tipo_movimiento='SALIDA',
                referencia=referencia,
                usuario=request.user
            )

            Bitacora.objects.create(
                usuario=request.user,
                modulo='Inventario',
                accion='Registrar Salida de Material',
                descripcion=f'Despacho de {cantidad} u. de {material.nombre}. Ref: {referencia}'
            )

            messages.success(request, f'Salida de {cantidad} unidades registrada correctamente.')
            return redirect('inventario')

        except Exception as e:
            messages.error(request, f'Error al procesar la salida PEPS: {str(e)}')
            return redirect('salida_inventario')

    return render(
        request,
        'inventario/salida.html',
        {
            'materiales': materiales,
            'material_preseleccionado': material_preseleccionado
        }
    )


@login_required
@rol_requerido(['ALMACENERO', 'KARDISTA', 'ADMINISTRADOR'])
def reporte_consumo_unidades(request):
    """
    Muestra la lista de consumos y costos acumulados clasificados por Unidad Organizacional (Inciso g) [28].
    """
    unidades = UnidadOrganizacional.objects.all().order_by('nombre')
    datos_consumo = []

    for unidad in unidades:
        # Filtrar solo salidas valoradas asociadas a esta unidad
        movimientos_unidad = MovimientoInventario.objects.filter(
            unidad_destino=unidad,
            tipo='SALIDA'
        )

        total_items = movimientos_unidad.aggregate(Sum('cantidad'))['cantidad__sum'] or 0
        total_monto = movimientos_unidad.aggregate(Sum('costo_total'))['costo_total__sum'] or Decimal('0.00')

        if total_items > 0:  # Mostrar únicamente unidades con consumos registrados
            datos_consumo.append({
                'unidad': unidad,
                'total_items': total_items,
                'total_monto': total_monto
            })

    return render(
        request,
        'inventario/consumo_unidades.html',
        {
            'datos_consumo': datos_consumo
        }
    )

@login_required
def obtener_items_compra_view(request):
    """
    API asíncrona que devuelve los materiales y cantidades aprobadas de una Orden de Compra [28].
    """
    compra_id = request.GET.get('compra_id')
    if not compra_id:
        return JsonResponse([], safe=False)

    # Obtenemos la Orden de Compra y su solicitud de origen
    compra = get_object_or_404(CompraMenor, id=compra_id)
    solicitud = compra.solicitud_origen

    if not solicitud:
        return JsonResponse([], safe=False)

    # Recuperamos el detalle de los materiales aprobados
    detalles = solicitud.detalles.select_related('material')
    data = []

    for d in detalles:
        if d.material:
            # Estimamos el último costo de ingreso registrado en el Kardex PEPS
            last_ent = MovimientoInventario.objects.filter(
                material=d.material, 
                tipo='ENTRADA'
            ).order_by('-fecha').first()
            
            costo_u = last_ent.costo_unitario if last_ent else Decimal('0.00')
            # Si el jefe aprobó una cantidad menor, arrastramos la cantidad aprobada
            cantidad = d.cantidad_aprobada if d.cantidad_aprobada is not None else d.cantidad_solicitada

            data.append({
                'material_id': d.material.id,
                'codigo': d.material.codigo,
                'nombre': d.material.nombre,
                'cantidad': cantidad,
                'costo_unitario': float(costo_u),
                'stock_actual': d.material.stock_actual
            })

    return JsonResponse(data, safe=False)