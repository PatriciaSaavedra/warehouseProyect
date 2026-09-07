from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
import json 
import html
from django.contrib import messages
from django.utils import timezone

from django.db import transaction
from django.http import HttpResponse, JsonResponse, request 
from decimal import Decimal
from django.db.models import Sum, Q, F
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

from django.contrib.auth.models import User

from compras.models import CompraMenor

from .models import (
    Material,
    MovimientoInventario,
    PartidaPresupuestaria,
    UnidadMedida,
    Proveedor,
    NotaIngreso,
    NotaIngresoDetalle,Almacen, InventarioAlmacen,
    Transferencia, TransferenciaDetalle,
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
@rol_requerido(['ADMINISTRADOR'])
def almacen_list(request):
    """
    Tarjeta 2: Muestra la lista de Almacenes (Central y Subalmacenes) de la Gobernación.
    """
    query = request.GET.get('q', '').strip()
    almacenes = Almacen.objects.select_related('unidad_organizacional', 'responsable').all()

    if query:
        almacenes = almacenes.filter(
            Q(nombre__icontains=query) |
            Q(tipo__icontains=query) |
            Q(unidad_organizacional__nombre__icontains=query) |
            Q(responsable__username__icontains=query)
        )

    almacenes = almacenes.order_by('tipo', 'nombre')

    paginator = Paginator(almacenes, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'inventario/almacen_list.html', {
        'page_obj': page_obj,
        'query': query
    })


@login_required
@rol_requerido(['ADMINISTRADOR'])
def crear_almacen(request):
    """
    Tarjeta 2: Registra un nuevo almacén o subalmacén, vinculándolo con su Unidad y Responsable [28].
    """
    unidades = UnidadOrganizacional.objects.filter(is_active=True).order_by('nombre')
    usuarios = User.objects.filter(is_active=True).order_by('username')

    if request.method == 'POST':
        nombre = request.POST.get('nombre', '').strip()
        descripcion = request.POST.get('descripcion', '').strip()
        tipo = request.POST.get('tipo', 'SUBALMACEN')
        unidad_id = request.POST.get('unidad_organizacional')
        responsable_id = request.POST.get('responsable')

        if not nombre or not tipo:
            messages.error(request, "El Nombre y el Tipo de Almacén son campos obligatorios.")
            return redirect('crear_almacen')

        if Almacen.objects.filter(nombre=nombre).exists():
            messages.error(request, f"Ya existe un almacén registrado con el nombre '{nombre}'.")
            return redirect('crear_almacen')

        unidad = get_object_or_404(UnidadOrganizacional, id=unidad_id) if unidad_id else None
        responsable = get_object_or_404(User, id=responsable_id) if responsable_id else None

        try:
            with transaction.atomic():
                Almacen.objects.create(
                    nombre=nombre,
                    descripcion=descripcion,
                    tipo=tipo,
                    unidad_organizacional=unidad,
                    responsable=responsable,
                    is_active=True
                )
                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Inventario',
                    accion='Crear Almacén',
                    descripcion=f'Se creó el almacén "{nombre}" de tipo {tipo} en la base de datos.'
                )
            messages.success(request, f"Almacén '{nombre}' registrado correctamente.")
            return redirect('almacen_list')

        except Exception as e:
            messages.error(request, f"Error de base de datos al registrar: {str(e)}")
            return redirect('crear_almacen')

    return render(request, 'inventario/crear_almacen.html', {
        'unidades': unidades,
        'usuarios': usuarios
    })


@login_required
@rol_requerido(['ADMINISTRADOR'])
def editar_almacen(request, id):
    """
    Tarjeta 2: Permite modificar el responsable, la unidad o dar de baja lógica a un almacén [28].
    """
    almacen = get_object_or_404(Almacen, id=id)
    unidades = UnidadOrganizacional.objects.filter(is_active=True).order_by('nombre')
    usuarios = User.objects.filter(is_active=True).order_by('username')

    if request.method == 'POST':
        nombre = request.POST.get('nombre', '').strip()
        descripcion = request.POST.get('descripcion', '').strip()
        tipo = request.POST.get('tipo', 'SUBALMACEN')
        unidad_id = request.POST.get('unidad_organizacional')
        responsable_id = request.POST.get('responsable')
        is_active = request.POST.get('is_active') == 'true'

        if not nombre or not tipo:
            messages.error(request, "El Nombre y el Tipo de Almacén son obligatorios.")
            return redirect('editar_almacen', id=almacen.id)

        unidad = get_object_or_404(UnidadOrganizacional, id=unidad_id) if unidad_id else None
        responsable = get_object_or_404(User, id=responsable_id) if responsable_id else None

        try:
            with transaction.atomic():
                almacen.nombre = nombre
                almacen.descripcion = descripcion
                almacen.tipo = tipo
                almacen.unidad_organizacional = unidad
                almacen.responsable = responsable
                almacen.is_active = is_active
                almacen.save()

                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Inventario',
                    accion='Editar Almacén',
                    descripcion=f'Se modificaron los datos del almacén "{nombre}" (ID: {almacen.id})'
                )
            messages.success(request, f"Almacén '{nombre}' actualizado correctamente.")
            return redirect('almacen_list')

        except Exception as e:
            messages.error(request, f"Error al actualizar el almacén: {str(e)}")
            return redirect('editar_almacen', id=almacen.id)

    return render(request, 'inventario/editar_almacen.html', {
        'almacen': almacen,
        'unidades': unidades,
        'usuarios': usuarios
    })
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
# inventario/views.py

@login_required
@rol_requerido(['ALMACENERO', 'KARDISTA', 'ADMINISTRADOR'])
def kardex(request, id):
    """
    Tarjetas 18 y 19: Calcula, filtra y consulta el Kardex de Existencias Valorado 
    de forma aislada por Almacén, rango de fechas, mes, gestión y tipo de movimiento.
    """
    material = get_object_or_404(Material, id=id)
    perfil = request.user.perfilusuario
    rol = perfil.rol

    # 1. Cargar almacenes disponibles según permisos
    if rol == 'ADMINISTRADOR':
        almacenes_disponibles = Almacen.objects.filter(is_active=True).order_by('nombre')
    else:
        almacenes_disponibles = perfil.almacenes_autorizados.filter(is_active=True).order_by('nombre')

    # 2. Capturar filtros de la Tarjeta 19
    filtro_almacen_id = request.GET.get('almacen', '').strip()
    desde_str = request.GET.get('desde', '').strip()
    hasta_str = request.GET.get('hasta', '').strip()
    filtro_mes = request.GET.get('mes', '').strip()
    filtro_gestion = request.GET.get('gestion', '2026').strip()  # Gestión por defecto
    filtro_tipo = request.GET.get('tipo_movimiento', '').strip()

    # Base de consulta para el material específico
    movimientos_query = MovimientoInventario.objects.filter(material=material)

    # Filtrado obligatorio por Almacén (Tarjeta 18)
    almacen_seleccionado = None
    if filtro_almacen_id:
        almacen_seleccionado = get_object_or_404(Almacen, id=filtro_almacen_id)
        if not perfil.tiene_acceso_almacen(almacen_seleccionado):
            messages.error(request, f"No tiene autorización para auditar el Kardex en: {almacen_seleccionado.nombre}")
            return redirect('inventario_por_almacen')
        movimientos_query = movimientos_query.filter(almacen=almacen_seleccionado)
    else:
        # Si no se selecciona, forzar al almacenero a ver solo sus almacenes autorizados
        if rol != 'ADMINISTRADOR':
            movimientos_query = movimientos_query.filter(almacen__in=almacenes_disponibles)

    # Filtrado por Gestión Fiscal
    try:
        gestion_ano = int(filtro_gestion)
        movimientos_query = movimientos_query.filter(fecha__year=gestion_ano)
    except ValueError:
        gestion_ano = 2026

    # Filtrar por Mes
    if filtro_mes:
        try:
            mes_num = int(filtro_mes)
            movimientos_query = movimientos_query.filter(fecha__month=mes_num)
        except ValueError:
            pass

    # Filtrar por rango de fechas (Dentro de la gestión)
    fecha_limite_inicial = None
    if desde_str:
        fecha_limite_inicial = parse_date(desde_str)
        if fecha_limite_inicial:
            movimientos_query = movimientos_query.filter(fecha__date__gte=fecha_limite_inicial)

    if hasta_str:
        hasta_date = parse_date(hasta_str)
        if hasta_date:
            movimientos_query = movimientos_query.filter(fecha__date__lte=hasta_date)

    # Filtrar por Tipo de Movimiento (ENTRADA / SALIDA)
    if filtro_tipo:
        movimientos_query = movimientos_query.filter(tipo=filtro_tipo)

    # 3. CÁLCULO DEL SALDO ANTERIOR (Arraste de periodos previos)
    # Se calculan todos los movimientos ocurridos antes de la fecha inicial del filtro
    query_previos = MovimientoInventario.objects.filter(material=material, fecha__year=gestion_ano)
    if almacen_seleccionado:
        query_previos = query_previos.filter(almacen=almacen_seleccionado)
    if fecha_limite_inicial:
        query_previos = query_previos.filter(fecha__date__lt=fecha_limite_inicial)
    elif filtro_mes:
        query_previos = query_previos.filter(fecha__month__lt=int(filtro_mes))

    saldo_inicial_fisico = 0
    saldo_inicial_valorado = Decimal('0.00')

    for m in query_previos.order_by('fecha'):
        if m.tipo == 'ENTRADA':
            saldo_inicial_fisico += m.cantidad
            saldo_inicial_valorado += m.costo_total
        else:
            saldo_inicial_fisico -= m.cantidad
            saldo_inicial_valorado -= m.costo_total

    # 4. PROCESAR MOVIMIENTOS FILTRADOS
    movimientos_db = movimientos_query.order_by('fecha')
    movimientos_valorados = []
    
    saldo_fisico = saldo_inicial_fisico
    saldo_valorado = saldo_inicial_valorado

    for mov in movimientos_db:
        if mov.tipo == 'ENTRADA':
            saldo_fisico += mov.cantidad
            saldo_valorado += mov.costo_total
            entrada_cant, salida_cant = mov.cantidad, 0
            entrada_imp, salida_imp = mov.costo_total, Decimal('0.00')
        else:
            saldo_fisico -= mov.cantidad
            saldo_valorado -= mov.costo_total
            entrada_cant, salida_cant = 0, mov.cantidad
            entrada_imp, salida_imp = Decimal('0.00'), mov.costo_total

        movimientos_valorados.append({
            'fecha': mov.fecha,
            'detalle': mov.unidad_destino.nombre if mov.unidad_destino else mov.referencia,
            'usuario': mov.usuario.username,
            'entrada_cant': entrada_cant,
            'salida_cant': salida_cant,
            'saldo_cant': saldo_fisico,
            'precio_unitario': mov.costo_unitario,
            'entrada_importe': entrada_imp,
            'salida_importe': salida_imp,
            'saldo_importe': saldo_valorado,
        })

    # Mostrar del más nuevo al más antiguo en pantalla
    movimientos_valorados.reverse()

    # Lista de meses oficiales para el filtro
    meses_lista = [
        (1, 'Enero'), (2, 'Febrero'), (3, 'Marzo'), (4, 'Abril'),
        (5, 'Mayo'), (6, 'Junio'), (7, 'Julio'), (8, 'Agosto'),
        (9, 'Septiembre'), (10, 'Octubre'), (11, 'Noviembre'), (12, 'Diciembre')
    ]
    Bitacora.objects.create(
        usuario=request.user,
        modulo='Inventario',
        accion='Visualizar Kardex',
        descripcion=f'El usuario visualizó en pantalla el Kardex Valorado interactivo de {material.nombre} (Cód: {material.codigo}).'
    )
    return render(
        request,
        'inventario/kardex.html',
        {
            'material': material,
            'movimientos': movimientos_valorados,
            'almacenes_disponibles': almacenes_disponibles,
            'filtro_almacen_id': filtro_almacen_id,
            'desde': desde_str,
            'hasta': hasta_str,
            'filtro_mes': filtro_mes,
            'filtro_gestion': filtro_gestion,
            'filtro_tipo': filtro_tipo,
            'meses': meses_lista,
            # Variables de Saldo Inicial para pintar en la cabecera
            'saldo_inicial_cant': saldo_inicial_fisico,
            'saldo_inicial_val': saldo_inicial_valorado,
        }
    )
@login_required
@rol_requerido(['ALMACENERO', 'ADMINISTRADOR'])
def entrada_inventario(request):
    """
    Tarjeta 6 y 7: Registra un documento formal de Nota de Ingreso (Entrada) en un almacén específico.
    Crea automáticamente el lote de inventario (Movimiento con saldo_disponible_lote) y actualiza el stock.
    """
    perfil = getattr(request.user, 'perfilusuario', None)
    rol = perfil.rol if perfil else 'UNIDAD_SOLICITANTE'
    
    # Tarjeta 3: Filtrar selección de almacenes según permisos de usuario
    if rol == 'ADMINISTRADOR':
        almacenes = Almacen.objects.filter(is_active=True).order_by('nombre')
    else:
        almacenes = perfil.almacenes_autorizados.filter(is_active=True).order_by('nombre')

    proveedores = Proveedor.objects.all().order_by('razon_social')
    materiales = Material.objects.all().order_by('nombre')

    if request.method == 'POST':
        nro_nota = request.POST.get('nro_nota', '').strip()
        proveedor_id = request.POST.get('proveedor')
        almacen_id = request.POST.get('almacen_destino')
        c31 = request.POST.get('c31', '').strip()
        nota_entrega = request.POST.get('nota_entrega', '').strip()
        factura = request.POST.get('factura', '').strip()
        reingreso = request.POST.get('reingreso') == 'true'
        fecha = request.POST.get('fecha')
        payload_raw = request.POST.get('payload')  # JSON con el detalle de materiales

        if not nro_nota or not proveedor_id or not almacen_id or not fecha or not payload_raw:
            messages.error(request, "Los campos con asterisco (*) son obligatorios.")
            return redirect('entrada_inventario')

        payload_raw = html.unescape(payload_raw) if hasattr(html, 'unescape') else payload_raw
        try:
            payload = json.loads(payload_raw)
        except json.JSONDecodeError:
            messages.error(request, "Error en el formato de los datos de entrada.")
            return redirect('entrada_inventario')

        if not payload:
            messages.error(request, "Debe agregar al menos un material a la nota de ingreso.")
            return redirect('entrada_inventario')

        proveedor = get_object_or_404(Proveedor, id=proveedor_id)
        almacen = get_object_or_404(Almacen, id=almacen_id)

        # Tarjeta 3: Control de acceso al almacén destino
        if not perfil.tiene_acceso_almacen(almacen):
            messages.error(request, f"No tiene autorización para registrar ingresos en: {almacen.nombre}")
            return redirect('entrada_inventario')

        if NotaIngreso.objects.filter(nro_nota=nro_nota).exists():
            messages.error(request, f"Ya existe una Nota de Ingreso registrada con el nro: {nro_nota}.")
            return redirect('entrada_inventario')

        try:
            with transaction.atomic():
                # 1. Crear cabecera de Nota de Ingreso
                nota = NotaIngreso.objects.create(
                    nro_nota=nro_nota,
                    proveedor=proveedor,
                    almacen_destino=almacen,
                    c31=c31,
                    nota_entrega=nota_entrega,
                    factura=factura,
                    reingreso=reingreso,
                    fecha=fecha,
                    usuario=request.user
                )

                # 2. Registrar cada material (Detalle + Stock Almacén + Lote PEPS)
                for item_key, item_data in payload.items():
                    material = Material.objects.get(id=item_key)
                    cantidad = int(item_data.get('cantidad', 0))
                    precio_u = Decimal(str(item_data.get('precio_unitario', '0.00')))

                    if cantidad <= 0 or precio_u < 0:
                        raise ValueError("Las cantidades y precios de los materiales deben ser mayores a cero.")

                    precio_total = cantidad * precio_u

                    # A. Guardar en NotaIngresoDetalle
                    NotaIngresoDetalle.objects.create(
                        nota_ingreso=nota,
                        material=material,
                        cantidad=cantidad,
                        precio_unitario=precio_u,
                        precio_total=precio_total
                    )

                    # B. Obtener o crear stock físico aislado por almacén
                    inv, created = InventarioAlmacen.objects.get_or_create(
                        material=material,
                        almacen=almacen,
                        defaults={'stock_fisico': 0, 'stock_reservado': 0}
                    )
                    
                    stock_anterior = inv.stock_fisico
                    inv.stock_fisico += cantidad
                    inv.save()  # Actualiza stock consolidado global en Material automáticamente

                    # C. Tarjeta 6: Crear el lote de inventario en MovimientoInventario para costeo PEPS
                    MovimientoInventario.objects.create(
                        material=material,
                        almacen=almacen,
                        tipo='ENTRADA',
                        cantidad=cantidad,
                        costo_unitario=precio_u,
                        costo_total=precio_total,
                        stock_anterior=stock_anterior,
                        stock_resultante=inv.stock_fisico,
                        referencia=f"NOTA INGRESO NRO {nro_nota}",
                        usuario=request.user,
                        saldo_disponible_lote=cantidad  # El lote inicia al 100% disponible
                    )

                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Inventario',
                    accion='Registrar Entrada',
                    descripcion=f'Se registró la Nota de Ingreso {nro_nota} en el almacén {almacen.nombre}'
                )

            messages.success(request, f"Nota de Ingreso {nro_nota} registrada con éxito en {almacen.nombre}.")
            return redirect('nota_ingreso_list')

        except Exception as e:
            messages.error(request, f"Error al procesar el ingreso físico de materiales: {str(e)}")
            return redirect('entrada_inventario')

    return render(request, 'inventario/crear_entrada.html', {
        'almacenes': almacenes,
        'proveedores': proveedores,
        'materiales': materiales
    })
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

        # 1. Obtener almacén de despacho por defecto (Almacén Central)
        almacen = Almacen.objects.filter(tipo='CENTRAL', is_active=True).first()
        if not almacen:
            messages.error(request, 'No se ha configurado un Almacén Central activo en el sistema.')
            return redirect('salida_inventario')

        try:
            # Procesar el egreso valorado PEPS asociándolo al Almacén Central y a la unidad destino
            registrar_salida_valorada_peps(
                material=material,
                almacen=almacen,                  # <-- PARÁMETRO DE ALMACÉN CENTRAL AGREGADO
                cantidad_salida=cantidad,
                tipo_movimiento='SALIDA',
                referencia=referencia,
                usuario=request.user,
                unidad_destino=unidad_destino
            )

            Bitacora.objects.create(
                usuario=request.user,
                modulo='Inventario',
                accion='Registrar Salida de Material',
                descripcion=f'Despacho de {cantidad} u. de {material.nombre} a la unidad: {unidad_destino.nombre} desde {almacen.nombre}. Ref: {referencia}'
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
    Registra pérdidas, mermas o vencimientos de stock respaldados por informe técnico (Inciso m del RE-SABS).
    Aplica el algoritmo de costeo PEPS para registrar financieramente la baja en el Kardex.
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

        # 1. Obtener almacén de baja por defecto (Almacén Central)
        almacen = Almacen.objects.filter(tipo='CENTRAL', is_active=True).first()
        if not almacen:
            messages.error(request, 'No se ha configurado un Almacén Central activo en el sistema.')
            return redirect('registrar_baja')

        try:
            # Procesar el egreso valorado mediante el algoritmo PEPS (FIFO) asociándolo al Almacén Central
            registrar_salida_valorada_peps(
                material=material,
                almacen=almacen,                  # <-- PARÁMETRO DE ALMACÉN CENTRAL AGREGADO
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
                descripcion=f'Baja de {cantidad} u. de {material.nombre} desde {almacen.nombre} por motivo de {motivo}. Ref: {referencia}'
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
    """
    Tarjeta 4: Registra un nuevo material en el catálogo general.
    El stock inicial se fija estrictamente en 0 de forma inalterable.
    """
    partidas = PartidaPresupuestaria.objects.all().order_by('codigo')
    unidades = UnidadMedida.objects.all().order_by('nombre')

    if request.method == 'POST':
        partida_id = request.POST.get('partida')
        codigo = request.POST.get('codigo', '').strip()
        nombre = request.POST.get('nombre', '').strip()
        descripcion = request.POST.get('descripcion', '').strip()
        unidad_id = request.POST.get('unidad_medida_fk')
        stock_minimo = request.POST.get('stock_minimo', 5)

        if not codigo or not nombre or not unidad_id:
            messages.error(request, "Los campos con asterisco (Código, Nombre y Unidad de Medida) son obligatorios.")
            return redirect('nuevo_material')

        partida = get_object_or_404(PartidaPresupuestaria, id=partida_id) if partida_id else None
        unidad = get_object_or_404(UnidadMedida, id=unidad_id)

        # NUEVO CONTROL LEGAL SABS (Tarjeta 4): Validar que el código inicie con el código de la partida
        if partida and not codigo.startswith(partida.codigo):
            messages.error(
                request, 
                f"Error de Codificación SABS: El código del material debe comenzar obligatoriamente "
                f"con el código de su partida presupuestaria ({partida.codigo}). "
                f"Ejemplo correcto: {partida.codigo}-0001"
            )
            return redirect('nuevo_material')

        # 2. NUEVA VALIDACIÓN (Tarjeta 4): Validar que el código no termine en guion y tenga un correlativo
        if '-' in codigo:
            prefijo, correlativo = codigo.split('-', 1)
            if not correlativo.strip():
                messages.error(
                    request, 
                    "Error de Codificación SABS: Debe ingresar un número correlativo o identificador "
                    "después del guion. Ejemplo correcto: 39100-0001"
                )
                return redirect('nuevo_material')
        # Validación de código único en el sistema
        if Material.objects.filter(codigo=codigo).exists():
            messages.error(request, f"Ya existe un material registrado con el código '{codigo}'.")
            return redirect('nuevo_material')

        try:
            # Tarjeta 4: El stock_actual se inicializa estrictamente en 0
            Material.objects.create(
                partida=partida,
                codigo=codigo,
                nombre=nombre,
                descripcion=descripcion,
                unidad_medida_fk=unidad,
                unidad_medida=unidad.nombre,  # Respaldo textual
                stock_actual=0,
                stock_minimo=int(stock_minimo)
            )

            # Registrar trazabilidad en la Bitácora (Tarjeta 30)
            Bitacora.objects.create(
                usuario=request.user,
                modulo='Inventario',
                accion='Registrar Material',
                descripcion=f'Se registró el material {nombre} con el código {codigo} en el catálogo.'
            )

            messages.success(request, f"El material '{nombre}' ha sido registrado en el catálogo.")
            return redirect('inventario_por_almacen')

        except Exception as e:
            messages.error(request, f"Error al procesar el guardado del material: {str(e)}")
            return redirect('nuevo_material')

    return render(request, 'inventario/nuevo_material.html', {
        'partidas': partidas,
        'unidades': unidades
    })
@login_required
@rol_requerido(['ALMACENERO', 'ADMINISTRADOR'])
def crear_unidad_medida_ajax(request):
    """
    Tarjeta 4: Registra de forma rápida una nueva unidad de medida mediante AJAX,
    evitando que el catalogador pierda el progreso de su formulario.
    """
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            codigo = data.get('codigo', '').strip().upper()
            nombre = data.get('nombre', '').strip()

            if not codigo or not nombre:
                return JsonResponse({'ok': False, 'error': 'Código y Nombre son obligatorios.'}, status=400)

            # Validar unicidad del código
            if UnidadMedida.objects.filter(codigo=codigo).exists():
                return JsonResponse({'ok': False, 'error': f"El código de unidad '{codigo}' ya existe en el sistema."}, status=400)

            # Crear el registro de forma atómica
            unidad = UnidadMedida.objects.create(codigo=codigo, nombre=nombre)
            
            return JsonResponse({
                'ok': True,
                'id': unidad.id,
                'codigo': unidad.codigo,
                'nombre': unidad.nombre
            })

        except Exception as e:
            return JsonResponse({'ok': False, 'error': str(e)}, status=400)

    return JsonResponse({'ok': False, 'error': 'Método no permitido.'}, status=405)
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
@rol_requerido(['ALMACENERO', 'ADMINISTRADOR'])
def editar_material(request, id):
    """
    Tarjeta 4: Modifica metadatos básicos de catalogación y el stock mínimo (alerta).
    Protege el inventario impidiendo que el stock real sea alterado manualmente.
    """
    material = get_object_or_404(Material, id=id)
    unidades = UnidadMedida.objects.all().order_by('nombre')

    if request.method == 'POST':
        nombre = request.POST.get('nombre', '').strip()
        descripcion = request.POST.get('descripcion', '').strip()
        unidad_id = request.POST.get('unidad_medida_fk')
        stock_minimo = request.POST.get('stock_minimo', 5)

        if not nombre or not unidad_id:
            messages.error(request, "Los campos Nombre y Unidad de Medida son obligatorios.")
            return redirect('editar_material', id=id)

        unidad = get_object_or_404(UnidadMedida, id=unidad_id)

        try:
            with transaction.atomic():
                material.nombre = nombre
                material.descripcion = descripcion
                material.unidad_medida_fk = unidad
                material.unidad_medida = unidad.nombre
                material.stock_minimo = int(stock_minimo)
                # NO se altera 'stock_actual' en este guardado bajo ninguna condición.
                material.save()

                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Inventario',
                    accion='Editar Material',
                    descripcion=f'Se modificaron los datos de catalogación del material {nombre} (Cód: {material.codigo})'
                )

            messages.success(request, f"La ficha del material '{nombre}' ha sido actualizada.")
            return redirect('inventario_por_almacen')

        except Exception as e:
            messages.error(request, f"Error al actualizar la ficha del material: {str(e)}")
            return redirect('editar_material', id=id)

    return render(request, 'inventario/editar_material.html', {
        'material': material,
        'unidades': unidades
    })
@login_required
@rol_requerido(['ADMINISTRADOR'])
def toggle_material(request, id):
    """
    Tarjeta 6: Activa o desactiva de manera lógica un material en el catálogo.
    Evita la pérdida de datos históricos de Kardex y solicitudes.
    """
    material = get_object_or_404(Material, id=id)
    nuevo_estado = not material.is_active

    try:
        with transaction.atomic():
            material.is_active = nuevo_estado
            material.save()

            estado_texto = "ACTIVADO" if nuevo_estado else "DESACTIVADO (DADO DE BAJA)"

            Bitacora.objects.create(
                usuario=request.user,
                modulo='Inventario',
                accion='Cambiar Estado Material',
                descripcion=f'Se cambió el estado del material "{material.nombre}" (Código: {material.codigo}) a {estado_texto}.'
            )
        
        messages.success(request, f"Material '{material.nombre}' {'activado' if nuevo_estado else 'desactivado'} correctamente.")
    except Exception as e:
        messages.error(request, f"Error al cambiar el estado del material: {str(e)}")

    return redirect('inventario_por_almacen') 
@login_required
@rol_requerido(['ALMACENERO', 'KARDISTA', 'ADMINISTRADOR'])
def lotes_list(request):
    """
    Tarjeta 8: Gestión de Lotes.
    Muestra los lotes activos (entradas con saldo disponible para PEPS),
    asociados a su material, almacén, costo unitario y documento de origen.
    """
    perfil = getattr(request.user, 'perfilusuario', None)
    rol = perfil.rol if perfil else 'UNIDAD_SOLICITANTE'
    
    # Control de acceso multi-almacén (Tarjeta 3)
    if rol == 'ADMINISTRADOR':
        almacenes_disponibles = Almacen.objects.filter(is_active=True).order_by('nombre')
    else:
        almacenes_disponibles = perfil.almacenes_autorizados.filter(is_active=True).order_by('nombre')

    # Filtros de búsqueda
    filtro_almacen_id = request.GET.get('almacen', '').strip()
    query_busqueda = request.GET.get('q', '').strip()

    # Consultamos solo movimientos de entrada que tengan saldo disponible en el lote
    lotes_query = MovimientoInventario.objects.filter(
        tipo='ENTRADA',
        saldo_disponible_lote__gt=0
    ).select_related('material', 'almacen', 'usuario')

    # Aplicar filtros
    if filtro_almacen_id:
        almacen_seleccionado = get_object_or_404(Almacen, id=filtro_almacen_id)
        # Seguridad: Validar acceso al almacén filtrado
        if not perfil.tiene_acceso_almacen(almacen_seleccionado):
            messages.error(request, f"No tiene autorización para auditar lotes en: {almacen_seleccionado.nombre}")
            return redirect('lotes_list')
        lotes_query = lotes_query.filter(almacen=almacen_seleccionado)

    if query_busqueda:
        lotes_query = lotes_query.filter(
            Q(material__nombre__icontains=query_busqueda) |
            Q(material__codigo__icontains=query_busqueda) |
            Q(referencia__icontains=query_busqueda)  # Filtra por nota de ingreso / documento origen
        )

    # Ordenar por fecha de entrada (los más antiguos primero por PEPS)
    lotes_query = lotes_query.order_by('fecha', 'id')

    # Paginación
    paginator = Paginator(lotes_query, 15)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'inventario/lotes_list.html', {
        'page_obj': page_obj,
        'almacenes_disponibles': almacenes_disponibles,
        'filtro_almacen_id': filtro_almacen_id,
        'query_busqueda': query_busqueda,
    })

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

@login_required
def inventario_por_almacen(request):
    """
    Tarjeta 5: Consulta de stock consolidado y aislado por Almacén / Subalmacén.
    Aplica controles de seguridad Multi-almacén según la Tarjeta 3.
    """
    perfil = getattr(request.user, 'perfilusuario', None)
    rol = perfil.rol if perfil else 'UNIDAD_SOLICITANTE'
    
    # Tarjeta 3: Cargar solo los almacenes que el usuario tiene permitido operar
    if rol == 'ADMINISTRADOR':
        almacenes_disponibles = Almacen.objects.filter(is_active=True).order_by('nombre')
    else:
        almacenes_disponibles = perfil.almacenes_autorizados.filter(is_active=True).order_by('nombre')

    # Capturar parámetros de filtrado
    filtro_almacen_id = request.GET.get('almacen', '').strip()
    filtro_sin_stock = request.GET.get('sin_stock', 'false').lower() == 'true'
    query_busqueda = request.GET.get('q', '').strip()

    almacen_seleccionado = None
    if filtro_almacen_id:
        almacen_seleccionado = get_object_or_404(Almacen, id=filtro_almacen_id)
        
        # Tarjeta 3: Control estricto de acceso. Si el usuario intenta forzar por URL un almacén no autorizado:
        if not perfil.tiene_acceso_almacen(almacen_seleccionado):
            messages.error(request, f"No tiene autorización para auditar el almacén: {almacen_seleccionado.nombre}")
            return redirect('inventario_por_almacen')

    # Aplicar lógica de filtrado de Tarjeta 5
    if almacen_seleccionado:
        # Aislado: Consultar materiales en este almacén específico
        inventario_query = InventarioAlmacen.objects.filter(almacen=almacen_seleccionado).select_related('material', 'material__partida')
        
        if query_busqueda:
            inventario_query = inventario_query.filter(
                Q(material__nombre__icontains=query_busqueda) |
                Q(material__codigo__icontains=query_busqueda)
            )
        if filtro_sin_stock:
            # CORREGIDO: Usamos expresión F para comparar directamente en la base de datos [4]
            # stock_fisico <= stock_reservado es equivalente a stock_disponible <= 0 [4]
            inventario_query = inventario_query.filter(stock_fisico__lte=F('stock_reservado'))
            
        inventario_query = inventario_query.order_by('material__nombre')
    else:
        # Consolidado: Consultar materiales a nivel global en la Gobernación
        inventario_query = Material.objects.all().select_related('partida')
        
        if query_busqueda:
            inventario_query = inventario_query.filter(
                Q(nombre__icontains=query_busqueda) |
                Q(codigo__icontains=query_busqueda)
            )
        if filtro_sin_stock:
            # Tarjeta 5: Identificar materiales agotados a nivel consolidado
            inventario_query = inventario_query.filter(stock_actual__lte=0)
            
        inventario_query = inventario_query.order_by('nombre')

    paginator = Paginator(inventario_query, 15)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'inventario/stock_list.html', {
        'page_obj': page_obj,
        'almacenes_disponibles': almacenes_disponibles,
        'almacen_seleccionado': almacen_seleccionado,
        'filtro_almacen_id': filtro_almacen_id,
        'filtro_sin_stock': filtro_sin_stock,
        'query_busqueda': query_busqueda,
        'rol': rol
    })

@login_required
@rol_requerido(['ALMACENERO', 'KARDISTA', 'ADMINISTRADOR'])
def nota_ingreso_list(request):
    """
    Tarjeta 7: Lista las Notas de Ingreso (Entradas físicas) del almacén.
    """
    query = request.GET.get('q', '').strip()
    notas = NotaIngreso.objects.select_related('proveedor', 'usuario').all()

    if query:
        notas = notas.filter(
            Q(nro_nota__icontains=query) |
            Q(proveedor__razon_social__icontains=query) |
            Q(factura__icontains=query)
        )

    notas = notas.order_by('-id')

    paginator = Paginator(notas, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'inventario/entrada_list.html', {
        'page_obj': page_obj,
        'query': query
    })


@login_required
@rol_requerido(['ALMACENERO', 'KARDISTA', 'ADMINISTRADOR'])
def nota_salida_list(request):
    """
    Tarjeta 12: Lista las Notas de Salida / Actas de entrega (Egresos físicos) del almacén.
    """
    query = request.GET.get('q', '').strip()
    # Importamos NotaSalida del mismo archivo para consultas
    from .models import NotaSalida
    notas = NotaSalida.objects.select_related('unidad_destino', 'usuario').all()

    if query:
        notas = notas.filter(
            Q(nro_nota__icontains=query) |
            Q(unidad_destino__nombre__icontains=query)
        )

    notas = notas.order_by('-id')

    paginator = Paginator(notas, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'inventario/salida_list.html', {
        'page_obj': page_obj,
        'query': query
    })
@login_required
@rol_requerido(['ALMACENERO', 'KARDISTA', 'ADMINISTRADOR'])
def detalle_nota_ingreso(request, id):
    """
    Tarjeta 7: Detalle de una Nota de Ingreso (Entrada) con sus respectivos materiales.
    """
    nota = get_object_or_404(
        NotaIngreso.objects.prefetch_related('detalles__material__partida').select_related('proveedor', 'usuario'),
        id=id
    )
    Bitacora.objects.create(
        usuario=request.user,
        modulo='Inventario',
        accion='Visualizar Detalle Nota Ingreso',
        descripcion=f'El usuario visualizó en pantalla el detalle de la Nota de Ingreso Nro: {nota.nro_nota}.'
    )
    return render(request, 'inventario/detalle_entrada.html', {
        'nota': nota
    })


@login_required
@rol_requerido(['ALMACENERO', 'KARDISTA', 'ADMINISTRADOR'])
def detalle_nota_salida(request, id):
    """
    Tarjeta 12: Detalle de una Nota de Salida (Egreso) con su valuación PEPS y enlace de origen.
    """
    from .models import NotaSalida
    nota = get_object_or_404(
        NotaSalida.objects.prefetch_related('detalles__material__partida').select_related('solicitud_origen', 'unidad_destino', 'usuario'),
        id=id
    )
    Bitacora.objects.create(
        usuario=request.user,
        modulo='Inventario',
        accion='Visualizar Detalle Nota Salida',
        descripcion=f'El usuario visualizó en pantalla el detalle de la Nota de Salida Nro: {nota.nro_nota} despachada a {nota.unidad_destino.nombre}.'
    )

    return render(request, 'inventario/detalle_salida.html', {
        'nota': nota
    })
# inventario/views.py

@login_required
@rol_requerido(['ADMINISTRADOR'])
def toggle_almacen(request, id):
    """
    Tarjeta 3: Activa o desactiva de manera lógica un Almacén o Subalmacén.
    """
    almacen = get_object_or_404(Almacen, id=id)
    nuevo_estado = not almacen.is_active

    try:
        with transaction.atomic():
            almacen.is_active = nuevo_estado
            almacen.save()

            estado_texto = "ACTIVADO" if nuevo_estado else "DESACTIVADO (DADO DE BAJA)"

            Bitacora.objects.create(
                usuario=request.user,
                modulo='Inventario',
                accion='Cambiar Estado Almacén',
                descripcion=f'Se cambió el estado del almacén "{almacen.nombre}" (ID: {id}) a {estado_texto}.'
            )
        
        messages.success(request, f"Almacén '{almacen.nombre}' {'activado' if nuevo_estado else 'desactivado'} correctamente.")
    except Exception as e:
        messages.error(request, f"Error al cambiar el estado del almacén: {str(e)}")

    return redirect('almacen_list')

@login_required
@rol_requerido(['ALMACENERO', 'KARDISTA', 'ADMINISTRADOR'])
def transferencia_list(request):
    """
    Tarjeta 21: Lista las transferencias de inventario registradas en el sistema.
    Aplica filtros de seguridad según los almacenes autorizados del usuario.
    """
    perfil = getattr(request.user, 'perfilusuario', None)
    rol = perfil.rol if perfil else 'UNIDAD_SOLICITANTE'
    
    # Filtros de visualización por permisos multi-almacén (Tarjeta 3)
    if rol == 'ADMINISTRADOR':
        transferencias = Transferencia.objects.select_related('origen', 'destino', 'usuario_envia', 'usuario_recibe').all()
    else:
        # Los almaceneros solo ven transferencias que involucren almacenes que tienen autorizados (origen o destino)
        almacenes_user = perfil.almacenes_autorizados.all()
        transferencias = Transferencia.objects.filter(
            Q(origen__in=almacenes_user) | Q(destino__in=almacenes_user)
        ).select_related('origen', 'destino', 'usuario_envia', 'usuario_recibe')

    transferencias = transferencias.order_by('-id')
    
    paginator = Paginator(transferencias, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'inventario/transferencia_list.html', {
        'page_obj': page_obj
    })


@login_required
@rol_requerido(['ALMACENERO', 'ADMINISTRADOR'])
def enviar_transferencia(request):
    """
    Tarjeta 21: Registra un envío de transferencia (Paso 1: EN_TRANSITO).
    Valida stock en el origen y descuenta de forma valorada aplicando PEPS.
    """
    perfil = getattr(request.user, 'perfilusuario', None)
    rol = perfil.rol if perfil else 'UNIDAD_SOLICITANTE'
    
    # Orígenes permitidos según rol de usuario
    if rol == 'ADMINISTRADOR':
        almacenes_origen = Almacen.objects.filter(is_active=True).order_by('nombre')
    else:
        almacenes_origen = perfil.almacenes_autorizados.filter(is_active=True).order_by('nombre')

    # El destino puede ser cualquier otro almacén activo
    almacenes_destino = Almacen.objects.filter(is_active=True).order_by('nombre')
    materiales = Material.objects.filter(stock_actual__gt=0).order_by('nombre')

    if request.method == 'POST':
        origen_id = request.POST.get('origen')
        destino_id = request.POST.get('destino')
        payload_raw = request.POST.get('payload')

        if not origen_id or not destino_id or not payload_raw:
            messages.error(request, "Debe seleccionar almacenes de origen, destino y agregar al menos un ítem.")
            return redirect('enviar_transferencia')

        if origen_id == destino_id:
            messages.error(request, "El almacén de destino no puede ser el mismo que el de origen.")
            return redirect('enviar_transferencia')

        payload_raw = html.unescape(payload_raw)
        try:
            payload = json.loads(payload_raw)
        except json.JSONDecodeError:
            messages.error(request, "Error en el formato de los datos de transferencia.")
            return redirect('enviar_transferencia')

        origen = get_object_or_404(Almacen, id=origen_id)
        destino = get_object_or_404(Almacen, id=destino_id)

        # Seguridad multi-almacén origen (Tarjeta 3)
        if not perfil.tiene_acceso_almacen(origen):
            messages.error(request, f"No tiene autorización para despachar materiales desde: {origen.nombre}")
            return redirect('enviar_transferencia')

        try:
            with transaction.atomic():
                # 1. Generar número correlativo TR-XXXXX
                ultima = Transferencia.objects.select_for_update().order_by('id').last()
                nro_num = (ultima.id + 1) if ultima else 1
                nro_correlativo = f"TR-{nro_num:05d}"

                transferencia = Transferencia.objects.create(
                    nro_transferencia=nro_correlativo,
                    origen=origen,
                    destino=destino,
                    estado='EN_TRANSITO',
                    usuario_envia=request.user
                )

                # 2. Descontar cada ítem del origen con PEPS
                for item_key, item_data in payload.items():
                    material = Material.objects.get(id=item_key)
                    cantidad = int(item_data.get('cantidad', 0))

                    if cantidad <= 0:
                        raise ValueError(f"La cantidad de '{material.nombre}' debe ser mayor a cero.")

                    # Llamar a nuestro servicio PEPS en el almacén de origen
                    # Esto reduce stock_fisico en InventarioAlmacen y crea el MovimientoInventario de SALIDA
                    mov = registrar_salida_valorada_peps(
                        material=material,
                        almacen=origen,
                        cantidad_salida=cantidad,
                        tipo_movimiento='SALIDA',
                        referencia=f"TRANSFERENCIA ENVIADA {nro_correlativo}",
                        usuario=request.user
                    )

                    # Registrar detalle conservando costos contables PEPS despachados
                    TransferenciaDetalle.objects.create(
                        transferencia=transferencia,
                        material=material,
                        cantidad=cantidad,
                        costo_unitario_transferencia=mov.costo_unitario,
                        costo_total_transferencia=mov.costo_total
                    )

                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Inventario',
                    accion='Despachar Transferencia',
                    descripcion=f'Se despachó la transferencia {nro_correlativo} desde {origen.nombre} hacia {destino.nombre}'
                )

            messages.success(request, f"Transferencia {nro_correlativo} enviada con éxito. Stock en tránsito.")
            return redirect('transferencia_list')

        except Exception as e:
            messages.error(request, f"Error al procesar el envío de la transferencia: {str(e)}")
            return redirect('enviar_transferencia')

    return render(request, 'inventario/crear_transferencia.html', {
        'almacenes_origen': almacenes_origen,
        'almacenes_destino': almacenes_destino,
        'materiales': materiales
    })


@login_required
@rol_requerido(['ALMACENERO', 'ADMINISTRADOR'])
def recibir_transferencia(request, id):
    """
    Tarjeta 21: Confirma la recepción física en el destino (Paso 2: RECIBIDA).
    Incrementa el stock físico del destino y crea el correspondiente lote PEPS.
    """
    transferencia = get_object_or_404(Transferencia, id=id)
    perfil = getattr(request.user, 'perfilusuario', None)

    if transferencia.estado != 'EN_TRANSITO':
        messages.error(request, "Esta transferencia ya ha sido procesada o cancelada.")
        return redirect('transferencia_list')

    # Seguridad multi-almacén destino: Solo el responsable del almacén destino puede recibirla
    if not perfil.tiene_acceso_almacen(transferencia.destino):
        messages.error(request, f"No tiene autorización para recibir inventario en el almacén: {transferencia.destino.nombre}")
        return redirect('transferencia_list')

    try:
        with transaction.atomic():
            transferencia = Transferencia.objects.select_for_update().get(id=id)
            detalles = transferencia.detalles.all()

            for det in detalles:
                # 1. Obtener o crear inventario aislado en el almacén destino
                inv_destino, created = InventarioAlmacen.objects.get_or_create(
                    material=det.material,
                    almacen=transferencia.destino,
                    defaults={'stock_fisico': 0, 'stock_reservado': 0}
                )
                
                stock_anterior = inv_destino.stock_fisico
                inv_destino.stock_fisico += det.cantidad
                inv_destino.save()  # Guarda e incrementa automáticamente el consolidado global de Material

                # 2. Registrar la entrada valorada en el Kardex del Almacén Destino
                # Se mantiene el costo del origen (TR-XXXXX) para no alterar la valuación contable en la transferencia
                MovimientoInventario.objects.create(
                    material=det.material,
                    almacen=transferencia.destino,
                    tipo='ENTRADA',
                    cantidad=det.cantidad,
                    costo_unitario=det.costo_unitario_transferencia,
                    costo_total=det.costo_total_transferencia,
                    stock_anterior=stock_anterior,
                    stock_resultante=inv_destino.stock_fisico,
                    referencia=f"TRANSFERENCIA RECIBIDA {transferencia.nro_transferencia}",
                    usuario=request.user,
                    saldo_disponible_lote=det.cantidad  # Abre el nuevo lote de entrada PEPS en el destino
                )

            # 3. Consolidar el estado de la transferencia
            transferencia.estado = 'RECIBIDA'
            transferencia.fecha_recepcion = timezone.now()
            transferencia.usuario_recibe = request.user
            transferencia.save()

            Bitacora.objects.create(
                usuario=request.user,
                modulo='Inventario',
                accion='Recibir Transferencia',
                descripcion=f'Se recepcionó conforme la transferencia {transferencia.nro_transferencia} en el almacén {transferencia.destino.nombre}'
            )

        messages.success(request, f"Transferencia {transferencia.nro_transferencia} recibida y consolidada en stock con éxito.")
    except Exception as e:
        messages.error(request, f"Error al procesar la recepción física de la transferencia: {str(e)}")

    return redirect('transferencia_list')
@login_required
@rol_requerido(['ALMACENERO', 'KARDISTA', 'ADMINISTRADOR'])
def nota_recepcion_pdf(request, id):
    """
    Genera el acta oficial de "Nota de Recepción" (Imagen 4) del GAD Potosí.
    """
    from reportlab.lib.pagesizes import letter
    nota = get_object_or_404(
        NotaIngreso.objects.prefetch_related('detalles__material__partida').select_related('proveedor', 'usuario'),
        id=id
    )

    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="nota_recepcion_{nota.nro_nota}.pdf"'

    pdf = canvas.Canvas(response, pagesize=letter)
    width, height = letter
    pdf.setTitle(f"Nota de Recepción Nro: {nota.nro_nota}")

    # Encabezado Institucional
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(50, height - 40, "GOBIERNO AUTÓNOMO DEPARTAMENTAL DE POTOSÍ")
    pdf.drawString(50, height - 52, "SECRETARÍA DPTAL. ADMINISTRATIVA Y FINANCIERA")
    pdf.drawString(50, height - 64, "UNIDAD DE ALMACENES")

    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawRightString(width - 50, height - 50, f"Nº. {nota.nro_nota.replace('NI-', '')}")
    pdf.drawString(50, height - 90, "NOTA DE RECEPCIÓN")

    # CORREGIDO: Traducir filtros HTML a expresiones de Python seguras
    factura_val = nota.factura if nota.factura else '—'
    c31_val = nota.c31 if nota.c31 else '—'
    fecha_factura_val = nota.fecha.strftime('%Y-%m-%d') if nota.fecha else '—'
    fecha_ingreso_val = nota.fecha_registro.strftime('%Y-%m-%d') if nota.fecha_registro else '—'
    orden_val = nota.compra_menor_origen.nro_orden if nota.compra_menor_origen else '—'

    # Tabla de metadatos (Proveedor, Factura, Orden de Compra, etc.)
    metadata = [
        [f"Proveedor: {nota.proveedor.razon_social}", f"Fac. Nº: {factura_val}"],
        [f"C.I. NIT. Nº: {nota.proveedor.nit}", f"C-31: {c31_val}"],
        [f"Fecha de FACTURA: {fecha_factura_val}", f"Orden de: {orden_val}"],
        [f"DESTINO: {nota.almacen_destino.nombre}", f"Fecha de Ingreso: {fecha_ingreso_val}"]
    ]

    t_meta = Table(metadata, colWidths=[250, 250])
    t_meta.setStyle(TableStyle([
        ('FONTNAME', (0,0), (-1,-1), 'Helvetica'),
        ('FONTSIZE', (0,0), (-1,-1), 8.5),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#D1D5DB')),
        ('PADDING', (0,0), (-1,-1), 3),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))

    t_meta.wrapOn(pdf, 50, height - 170)
    t_meta.drawOn(pdf, 50, height - 170)

    # Detalle de Materiales Recibidos
    headers = ['ITEM', 'DESCRIPCION', 'U. MEDIDA', 'PEDIDO', 'ENTREG.', 'P. UNIT (Bs.)', 'P. TOTAL (Bs.)', 'PARTIDA']
    data = [headers]

    total_nota = Decimal('0.00')
    for index, det in enumerate(nota.detalles.all(), start=1):
        total_nota += det.precio_total
        unidad_manejo = det.material.unidad_medida_fk.codigo if det.material.unidad_medida_fk else det.material.unidad_medida
        data.append([
            str(index),
            det.material.nombre[:45],
            unidad_manejo,
            str(det.cantidad),  # Cantidad Pedido
            str(det.cantidad),  # Cantidad Entregado
            f"{det.precio_unitario:.2f}",
            f"{det.precio_total:.2f}",
            det.material.partida.codigo
        ])

    data.append(['', 'COSTO TOTAL:', '', '', '', '', f"{total_nota:.2f}", ''])

    col_widths = [30, 200, 50, 45, 45, 60, 60, 40]
    t_det = Table(data, colWidths=col_widths)
    last_row = len(data) - 1

    t_style = TableStyle([
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('ALIGN', (1,1), (1, last_row), 'LEFT'),  # Descripción alineada izquierda
        ('ALIGN', (5,1), (6, last_row), 'RIGHT'),  # Costos derecha
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 8),
        ('GRID', (0,0), (-1, last_row - 1), 0.5, colors.HexColor('#D1D5DB')),
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#F3F4F6')),
        # Fila de totales
        ('FONTNAME', (1, last_row), (1, last_row), 'Helvetica-Bold'),
        ('FONTNAME', (6, last_row), (6, last_row), 'Helvetica-Bold'),
        ('LINEABOVE', (6, last_row), (6, last_row), 1, colors.black),
    ])
    t_det.setStyle(t_style)

    # Renderizar tabla dinámica
    w_act, h_act = t_det.wrapOn(pdf, width - 100, height - 250)
    pdf_y = height - 190 - h_act
    t_det.drawOn(pdf, 50, pdf_y)

    # Firmas oficiales al final de la página (Coordenadas fijas en base de la hoja)
    y_firmas = 80
    pdf.setFont("Helvetica", 7.5)
    pdf.drawString(50, y_firmas, "___________________________________")
    pdf.drawString(50, y_firmas - 10, "Responsable Almacenes")

    pdf.drawString(230, y_firmas, "___________________________________")
    pdf.drawString(230, y_firmas - 10, "Responsable Bienes y Servicios")

    pdf.drawString(410, y_firmas, "___________________________________")
    pdf.drawString(410, y_firmas - 10, "Responsable Kardex Valorado")

    pdf.save()

    Bitacora.objects.create(
        usuario=request.user,
        modulo='Inventario',
        accion='Exportar Nota Recepción PDF',
        descripcion=f'Se descargó el PDF oficial de la Nota de Recepción Nro: {nota.nro_nota} del proveedor {nota.proveedor.razon_social}.'
    )

    return response
@login_required
@rol_requerido(['ALMACENERO', 'KARDISTA', 'ADMINISTRADOR'])
def kardex_fisico_pdf(request, id):
    """
    Genera el formato idéntico del "Kardex de Control de Existencias" (Ficha física amarilla, Imagen 3).
    """
    from reportlab.lib.pagesizes import letter
    material = get_object_or_404(Material, id=id)

    # Consultamos movimientos de inventario en este almacén en particular
    movimientos_db = MovimientoInventario.objects.filter(material=material).order_by('fecha')

    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="kardex_fisico_{material.codigo}.pdf"'

    pdf = canvas.Canvas(response, pagesize=letter)
    width, height = letter
    pdf.setTitle(f"Kardex Físico - {material.codigo}")

    # Encabezado Ficha Física
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(50, height - 40, "KARDEX DE CONTROL DE EXISTENCIAS")
    pdf.drawString(50, height - 52, "GOBIERNO AUTÓNOMO DEPARTAMENTAL DE POTOSÍ")

    pdf.setFont("Helvetica", 9)
    pdf.drawString(50, height - 75, "Artículo / Subartículo:")
    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawString(160, height - 75, material.nombre)

    pdf.setFont("Helvetica", 9)
    pdf.drawString(50, height - 90, "Código Material:")
    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawString(160, height - 90, material.codigo)

    pdf.setFont("Helvetica", 9)
    pdf.drawString(420, height - 75, "U. de Manejo:")
    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawString(495, height - 75, material.unidad_medida_fk.nombre if material.unidad_medida_fk else material.unidad_medida)

    pdf.setFont("Helvetica", 9)
    pdf.drawString(420, height - 90, "Partida:")
    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawString(495, height - 90, material.partida.codigo if material.partida else "—")

    # Columnas idénticas a la Ficha de cartón (Imagen 3)
    headers_1 = ['FECHA', 'DETALLE / DESTINO', 'Nº INGRESO', 'Nº SALIDA', 'CONTROL FISICO (Cantidades)', '', '']
    headers_2 = ['', '', '', '', 'Entrada', 'Salida', 'Saldo']
    data = [headers_1, headers_2]

    saldo_fisico = 0
    for mov in movimientos_db:
        nro_ingreso = mov.referencia.replace("NOTA INGRESO NRO ", "") if "NOTA INGRESO" in mov.referencia else "—"
        nro_salida = mov.referencia.replace("DESPACHO: ", "").replace("BAJA: ", "") if ("DESPACHO" in mov.referencia or "BAJA" in mov.referencia) else "—"

        if mov.tipo == 'ENTRADA':
            saldo_fisico += mov.cantidad
            entrada = str(mov.cantidad)
            salida = ""
        else:
            saldo_fisico -= mov.cantidad
            entrada = ""
            salida = str(mov.cantidad)

        # Destino/Detalle en base a la Unidad Organizacional (Ficha física, Imagen 3)
        detalle_destino = mov.unidad_destino.nombre if mov.unidad_destino else mov.referencia

        data.append([
            mov.fecha.strftime('%d/%m/%Y'),
            detalle_destino[:35],
            nro_ingreso,
            nro_salida,
            entrada,
            salida,
            str(saldo_fisico)
        ])

    col_widths = [65, 175, 65, 65, 45, 45, 50]
    t = Table(data, colWidths=col_widths)
    last_row = len(data) - 1

    t_style = TableStyle([
        ('SPAN', (0,0), (0,1)),  # Fecha
        ('SPAN', (1,0), (1,1)),  # Detalle
        ('SPAN', (2,0), (2,1)),  # N° Ingreso
        ('SPAN', (3,0), (3,1)),  # N° Salida
        ('SPAN', (4,0), (6,0)),  # Control Físico

        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('ALIGN', (1,2), (1, last_row), 'LEFT'),  # Detalle a la izquierda
        ('FONTNAME', (0,0), (-1,1), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 8),
        ('GRID', (0,0), (-1, last_row), 0.5, colors.HexColor('#9CA3AF')),
        ('BACKGROUND', (0,0), (-1,1), colors.HexColor('#FDF2F2')), # Matiz amarillento simulando la ficha real
        ('FONTNAME', (0,2), (-1, last_row), 'Helvetica'),
    ])
    t.setStyle(t_style)

    w_act, h_act = t.wrapOn(pdf, width - 100, height - 200)
    pdf_y = height - 120 - h_act
    t.drawOn(pdf, 50, pdf_y)

    pdf.save()
    Bitacora.objects.create(
        usuario=request.user,
        modulo='Inventario',
        accion='Exportar Kardex Físico PDF',
        descripcion=f'Se descargó el PDF de la Ficha de Kardex Físico (Imagen 3) de {material.nombre} (Cód: {material.codigo}).'
    )
    return response
@login_required
@rol_requerido(['ALMACENERO', 'KARDISTA', 'ADMINISTRADOR'])
def reporte_inventario_oficial_pdf(request):
    """
    Genera el formato tabular oficial e impreso del "Inventario Físico Valorado de Materiales" (Imagen 2).
    """
    desde_str = request.GET.get('desde')
    hasta_str = request.GET.get('hasta')

    desde = parse_date(desde_str) if desde_str else datetime.date(2026, 1, 1)
    hasta = parse_date(hasta_str) if hasta_str else datetime.date(2026, 5, 31)

    materiales = Material.objects.all().order_by('codigo')

    # Estructura de doble nivel
    headers_1 = ['ITEM', 'DESCRIPCION', 'UNIDAD DE\nMANEJO', f'SALDO AL {desde.strftime("%d/%m/%Y")}', '', 'ENTRADAS DEL PERIODO', '', 'SALIDAS DEL PERIODO', '', 'SALDO AL Cierre', '']
    headers_2 = ['', '', '', 'CANT. SALDO', 'COSTO TOTAL', 'CANT. INGRES.', 'COSTO TOTAL', 'CANT. ENTREG.', 'COSTO TOTAL', 'CANTIDAD', 'COSTO TOTAL']
    data = [headers_1, headers_2]

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
        ent_cant, ent_val = 0, Decimal('0.00')
        sal_cant, sal_val = 0, Decimal('0.00')
        for m in mov_periodo:
            if m.tipo == 'ENTRADA':
                ent_cant += m.cantidad
                ent_val += m.costo_total
            else:
                sal_cant += m.cantidad
                sal_val += m.costo_total

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
    response['Content-Disposition'] = f'inline; filename="inventario_oficial_potosi.pdf"'

    # --- CORRECCIÓN DEFINITIVA DE PESTAÑA: Instanciar y poner título de inmediato ---
    pdf = canvas.Canvas(response, pagesize=landscape(letter))
    pdf.setTitle(f"Inventario Valorado ({desde.strftime('%d/%m/%Y')} a {hasta.strftime('%d/%m/%Y')})")
    pdf.setSubject("SGA - Gobierno Autónomo Departamental de Potosí")
    pdf.setAuthor("Sistema de Gestión de Almacenes")

    width, height = landscape(letter)

    # Encabezados
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(50, height - 40, "GOBIERNO AUTÓNOMO DEPARTAMENTAL DE POTOSÍ")
    pdf.drawString(50, height - 52, "SECRETARÍA DPTAL. ADMINISTRATIVA Y FINANCIERA")
    pdf.drawString(50, height - 64, "UNIDAD DE CONTABILIDAD")

    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(250, height - 90, f"INVENTARIO FÍSICO VALORADO DE MATERIALES")
    pdf.setFont("Helvetica", 9)
    pdf.drawString(290, height - 105, f"PRACTICADO DEL {desde.strftime('%d/%m/%Y')} AL {hasta.strftime('%d/%m/%Y')}")

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

        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('ALIGN', (1,2), (1,-1), 'LEFT'),
        ('FONTNAME', (0,0), (-1,1), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 7),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#9CA3AF')),
        ('BACKGROUND', (0,0), (-1,1), colors.HexColor('#E5E7EB')),
        ('FONTNAME', (0,2), (-1,-1), 'Helvetica'),
    ])
    t.setStyle(t_style)

    w_act, h_act = t.wrapOn(pdf, width - 100, height - 200)
    t.drawOn(pdf, 50, height - 125 - h_act)

    pdf.save()

    # REGISTRO DE AUDITORÍA: Exportación de documento oficial [1]
    Bitacora.objects.create(
        usuario=request.user,
        modulo='Inventario',
        accion='Exportar Inventario Oficial PDF',
        descripcion=f'Se exportó en PDF el Inventario Físico Valorado desde {desde} hasta {hasta}.'
    )

    return response
@login_required
@rol_requerido(['ADMINISTRADOR'])
def cierre_conciliacion_view(request):
    """
    Tarjeta 20: Permite realizar la conciliación física de inventario (Imagen 2),
    registrar diferencias de forma atómica y realizar el cierre anual de gestión
    generando los saldos iniciales del año siguiente.
    """
    perfil = request.user.perfilusuario
    almacenes = Almacen.objects.filter(is_active=True).order_by('nombre')
    
    almacen_id = request.GET.get('almacen', '').strip()
    almacen_seleccionado = None
    inventario_query = []

    if almacen_id:
        almacen_seleccionado = get_object_or_404(Almacen, id=almacen_id)
        inventario_query = InventarioAlmacen.objects.filter(almacen=almacen_seleccionado).select_related('material')

    if request.method == 'POST':
        almacen_id_post = request.POST.get('almacen_id')
        gestion_cierre = int(request.POST.get('gestion_cierre', 2026))
        gestion_nueva = gestion_cierre + 1
        
        almacen_op = get_object_or_404(Almacen, id=almacen_id_post)
        items_inventario = InventarioAlmacen.objects.filter(almacen=almacen_op).select_related('material')

        try:
            with transaction.atomic():
                for item in items_inventario:
                    material = item.material
                    
                    # 1. Recuperar el stock físico real reportado por el almacenero
                    conteo_real_raw = request.POST.get(f"real_{item.id}", '').strip()
                    if conteo_real_raw == "":
                        continue
                    
                    conteo_real = int(conteo_real_raw)
                    stock_sistema_previo = item.stock_fisico
                    
                    # 2. CONCILIACIÓN: Comparar físico vs sistema y registrar diferencias (Imagen 2)
                    diferencia = conteo_real - stock_sistema_previo

                    if diferencia > 0:
                        # Sobrante: Registrar un ajuste de ENTRADA en el Kardex al último costo estimado
                        last_ent = MovimientoInventario.objects.filter(material=material, tipo='ENTRADA').order_by('-fecha').first()
                        costo_u = last_ent.costo_unitario if last_ent else Decimal('0.00')
                        
                        item.stock_fisico = conteo_real
                        item.save()

                        MovimientoInventario.objects.create(
                            material=material,
                            almacen=almacen_op,
                            tipo='ENTRADA',
                            cantidad=diferencia,
                            costo_unitario=costo_u,
                            costo_total=diferencia * costo_u,
                            stock_anterior=stock_sistema_previo,
                            stock_resultante=conteo_real,
                            referencia=f"AJUSTE CONCILIACION FÍSICA {gestion_cierre}",
                            usuario=request.user,
                            saldo_disponible_lote=diferencia
                        )

                    elif diferencia < 0:
                        # Faltante: Registrar una BAJA aplicando PEPS de manera automática
                        registrar_salida_valorada_peps(
                            material=material,
                            almacen=almacen_op,
                            cantidad_salida=abs(diferencia),
                            tipo_movimiento='BAJA',
                            referencia=f"AJUSTE MERMA CONCILIACION {gestion_cierre}",
                            usuario=request.user
                        )
                        # Recargamos para tener el stock real actualizado después del PEPS
                        item.refresh_from_db()

                    # 3. CIERRE ANUAL (Tarjeta 20): Generar stock inicial para la gestión nueva (ej. 2027)
                    # Creamos la ENTRADA de apertura para el 01/01/XXXX del año siguiente
                    last_entrada_valorada = MovimientoInventario.objects.filter(material=material, tipo='ENTRADA').order_by('-fecha').first()
                    costo_u_apertura = last_entrada_valorada.costo_unitario if last_entrada_valorada else Decimal('0.00')
                    
                    # Registramos el movimiento fechado al año siguiente
                    fecha_apertura = datetime.datetime(gestion_nueva, 1, 1, 0, 0, 0, tzinfo=timezone.get_current_timezone())

                    MovimientoInventario.objects.create(
                        material=material,
                        almacen=almacen_op,
                        tipo='ENTRADA',
                        cantidad=item.stock_fisico,
                        costo_unitario=costo_u_apertura,
                        costo_total=item.stock_fisico * costo_u_apertura,
                        stock_anterior=0,  # Inicia de cero en el nuevo año
                        stock_resultante=item.stock_fisico,
                        referencia=f"SALDO INICIAL APERTURA GESTIÓN {gestion_nueva}",
                        usuario=request.user,
                        saldo_disponible_lote=item.stock_fisico,
                        fecha=fecha_apertura # Forzamos la fecha de apertura fiscal
                    )

                # Registrar auditoría final
                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Inventario',
                    accion='Cierre de Gestión Anual',
                    descripcion=f'Se realizó la conciliación física y el cierre anual de gestión {gestion_cierre} en {almacen_op.nombre}. Saldo inicial {gestion_nueva} generado.'
                )

            messages.success(request, f"Conciliación física procesada, gestión {gestion_cierre} cerrada y saldos iniciales {gestion_nueva} generados exitosamente.")
            return redirect('inventario_por_almacen')

        except Exception as e:
            messages.error(request, f"Error al procesar el cierre anual y conciliación: {str(e)}")
            return redirect('cierre_conciliacion')

    return render(request, 'inventario/cierre_conciliacion.html', {
        'almacenes': almacenes,
        'almacen_seleccionado': almacen_seleccionado,
        'filtro_almacen_id': almacen_id,
        'inventario': inventario_query,
        'gestion_actual': 2026
    })