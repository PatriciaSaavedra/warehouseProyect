from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
import json 
import html
from django.contrib import messages
from django.utils import timezone
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from decimal import Decimal
from django.db.models import Sum, Q, F, Prefetch
from django.utils.dateparse import parse_date
import datetime

from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, landscape

from auditoria.models import Bitacora
from organizacion.models import Secretaria, UnidadOrganizacional
from django.contrib.auth.models import User
from compras.models import CompraMenor
from django.core.exceptions import ValidationError

from usuarios.decorators import rol_requerido
from .services import registrar_salida_valorada_peps, validar_operacion_almacen, obtener_inventario_para_unidad

from .models import (
    Material,
    MovimientoInventario,
    PartidaPresupuestaria,
    UnidadMedida,
    Proveedor,
    NotaIngreso,
    NotaIngresoDetalle,
    Almacen, 
    InventarioAlmacen,
    Transferencia, 
    TransferenciaDetalle,
)


def obtener_almacenes_usuario(user):
    """
    Función auxiliar: Retorna los almacenes que el usuario tiene derecho a operar/auditar.
    Para ADMINISTRADOR o ADMIN_ALMACENES retorna todos los almacenes activos de la Gobernación.
    """
    perfil = getattr(user, 'perfilusuario', None)
    if not perfil:
        return Almacen.objects.none()

    if perfil.rol in ['ADMINISTRADOR', 'ADMIN_ALMACENES'] or user.is_superuser:
        return Almacen.objects.filter(is_active=True)

    filtro_almacenes = Q(responsable=user) | Q(id__in=perfil.almacenes_autorizados.all())
    if perfil.unidad:
        filtro_almacenes |= Q(unidad_organizacional=perfil.unidad)

    return Almacen.objects.filter(filtro_almacenes, is_active=True).distinct()


@login_required
@rol_requerido(['ALMACENERO', 'KARDISTA', 'ADMINISTRADOR', 'ADMIN_ALMACENES'])
def inventario_view(request):
    """
    Catálogo de Existencias jerárquico por Partidas Presupuestarias.
    Integra:
    - Búsqueda por texto (código o nombre).
    - Selector de almacén para aislar el stock.
    - Filtro de materiales agotados.
    - Cuadro de generación de Inventario Valorado con fechas.
    - Acceso a Cierre de Gestión y cruce con el POA.
    """
    from presupuestos.models import POA
    from datetime import date

    perfil = request.user.perfilusuario
    rol = perfil.rol

    # 1. Almacenes disponibles según rol
    almacenes_disponibles = Almacen.objects.filter(is_active=True).order_by('nombre') if (rol in ['ADMINISTRADOR', 'ADMIN_ALMACENES']) else perfil.almacenes_autorizados.filter(is_active=True).order_by('nombre')

    # 2. Captura de filtros
    filtro_almacen_id = request.GET.get('almacen', '').strip()
    query_busqueda = request.GET.get('q', '').strip()
    filtro_sin_stock = request.GET.get('sin_stock') in ['true', 'on', '1']
    
    desde_defecto = request.GET.get('desde', f"{date.today().year}-01-01")
    hasta_defecto = request.GET.get('hasta', f"{date.today().year}-12-31")

    almacen_seleccionado = None
    if filtro_almacen_id:
        almacen_seleccionado = get_object_or_404(Almacen, id=filtro_almacen_id)
    else:
        almacen_central = Almacen.objects.filter(tipo='CENTRAL', is_active=True).first()
        if almacen_central and (rol in ['ADMINISTRADOR', 'ADMIN_ALMACENES'] or perfil.tiene_acceso_almacen(almacen_central)):
            almacen_seleccionado = almacen_central
            filtro_almacen_id = str(almacen_central.id)
        else:
            almacen_seleccionado = almacenes_disponibles.first()
            if almacen_seleccionado:
                filtro_almacen_id = str(almacen_seleccionado.id)

    # 3. Base de consulta de materiales activos
    materiales_base = Material.objects.filter(is_active=True).select_related('unidad_medida_fk').prefetch_related('inventarios_almacen__almacen')

    if query_busqueda:
        materiales_base = materiales_base.filter(
            Q(nombre__icontains=query_busqueda) |
            Q(codigo__icontains=query_busqueda) |
            Q(partida__codigo__icontains=query_busqueda) |
            Q(partida__nombre__icontains=query_busqueda)
        )

    partidas = PartidaPresupuestaria.objects.order_by('codigo')

    partidas_agrupadas = []
    total_articulos_global = 0
    total_stock_fisico_almacen = 0

    for part in partidas:
        materiales_partida = materiales_base.filter(partida=part).order_by('codigo')
        materiales_data = []
        stock_partida_en_almacen = 0

        for mat in materiales_partida:
            inv = mat.inventarios_almacen.filter(almacen=almacen_seleccionado).first() if almacen_seleccionado else None
            stock_en_deposito = inv.stock_fisico if inv else 0
            stock_disp_deposito = inv.stock_disponible if inv else 0

            # Aplicar filtro de solo agotados
            if filtro_sin_stock and stock_disp_deposito > 0:
                continue

            stock_partida_en_almacen += stock_en_deposito

            desglose = []
            for inv_item in mat.inventarios_almacen.filter(stock_fisico__gt=0):
                desglose.append({
                    'almacen': inv_item.almacen.nombre,
                    'stock_fisico': inv_item.stock_fisico,
                    'stock_disponible': inv_item.stock_disponible
                })

            materiales_data.append({
                'material': mat,
                'stock_almacen': stock_en_deposito,
                'stock_disponible': stock_disp_deposito,
                'desglose': desglose
            })

        # Incluir la partida si tiene ítems tras los filtros
        if materiales_data:
            total_articulos_global += len(materiales_data)
            total_stock_fisico_almacen += stock_partida_en_almacen

            poas_partida = POA.objects.filter(
                partida=part,
                gestion=2026,
                monto_disponible__gt=0
            ).select_related('unidad', 'unidad__secretaria').order_by('unidad__nombre')

            partidas_agrupadas.append({
                'partida': part,
                'materiales_data': materiales_data,
                'total_materiales': len(materiales_data),
                'stock_fisico_partida': stock_partida_en_almacen, # <-- Variable corregida
                'poas_habilitados': poas_partida
            })

    return render(request, 'inventario/index.html', {
        'partidas_agrupadas': partidas_agrupadas,
        'total_articulos_global': total_articulos_global,
        'total_stock_fisico_global': total_stock_fisico_almacen,
        'almacenes_disponibles': almacenes_disponibles,
        'almacen_seleccionado': almacen_seleccionado,
        'filtro_almacen_id': filtro_almacen_id,
        'query_busqueda': query_busqueda,
        'filtro_sin_stock': filtro_sin_stock,
        'desde_defecto': desde_defecto,
        'hasta_defecto': hasta_defecto,
    })
@login_required
@rol_requerido(['ALMACENERO', 'KARDISTA', 'ADMINISTRADOR'])
def kardex(request, id):
    """
    Libro de Almacén o Kardex Valorado oficial (D.S. 0181 / SABS).
    AISLAMIENTO ESTRICTO: El Kardex siempre se calcula y audita por almacén individual
    (por defecto Almacén Central), evitando mezclar depósitos en un mismo saldo.
    """
    material = get_object_or_404(Material, id=id)
    perfil = request.user.perfilusuario
    rol = perfil.rol

    # 1. Almacenes autorizados
    almacenes_disponibles = Almacen.objects.filter(is_active=True).order_by('nombre') if rol == 'ADMINISTRADOR' else perfil.almacenes_autorizados.filter(is_active=True).order_by('nombre')

    if not almacenes_disponibles.exists() and rol != 'ADMINISTRADOR':
        messages.error(request, "No tiene ningún almacén asignado bajo su responsabilidad.")
        return redirect('inventario')

    filtro_almacen_id = request.GET.get('almacen', '').strip()

    # ========================================================
    # REGLA CLAVE: SELECCIÓN AUTOMÁTICA DEL ALMACÉN CENTRAL
    # Si no se pasó un almacén por URL, auditar por defecto Almacén Central
    # ========================================================
    if not filtro_almacen_id:
        almacen_central = Almacen.objects.filter(tipo='CENTRAL', is_active=True).first()
        if almacen_central and (rol == 'ADMINISTRADOR' or perfil.tiene_acceso_almacen(almacen_central)):
            almacen_seleccionado = almacen_central
            filtro_almacen_id = str(almacen_central.id)
        else:
            almacen_seleccionado = almacenes_disponibles.first()
            if almacen_seleccionado:
                filtro_almacen_id = str(almacen_seleccionado.id)
            else:
                almacen_seleccionado = None
    else:
        almacen_seleccionado = get_object_or_404(Almacen, id=filtro_almacen_id)
        if not perfil.tiene_acceso_almacen(almacen_seleccionado):
            messages.error(request, f"Violación de Seguridad: No tiene autorización para auditar: {almacen_seleccionado.nombre}")
            return redirect('kardex', id=material.id)

    desde_str = request.GET.get('desde', '').strip()
    hasta_str = request.GET.get('hasta', '').strip()
    filtro_gestion = int(request.GET.get('gestion', '2026'))

    # 2. Arrastre de Saldo Inicial Aislado a ese Almacén
    fecha_limite_inicial = parse_date(desde_str) if desde_str else None

    if fecha_limite_inicial:
        query_previos = MovimientoInventario.objects.filter(
            material=material, 
            almacen=almacen_seleccionado, 
            fecha__date__lt=fecha_limite_inicial
        )
    else:
        query_previos = MovimientoInventario.objects.filter(
            material=material, 
            almacen=almacen_seleccionado, 
            fecha__year__lt=filtro_gestion
        )

    saldo_inicial_fisico = 0
    saldo_inicial_valorado = Decimal('0.00')

    for m in query_previos.order_by('fecha', 'id'):
        if m.tipo == 'ENTRADA':
            saldo_inicial_fisico += m.cantidad
            saldo_inicial_valorado += (m.costo_total or Decimal('0.00'))
        else:
            saldo_inicial_fisico -= m.cantidad
            saldo_inicial_valorado -= (m.costo_total or Decimal('0.00'))

    # 3. Movimientos del periodo filtrados exclusivamente por el Almacén seleccionado
    movimientos_query = MovimientoInventario.objects.filter(
        material=material, 
        almacen=almacen_seleccionado,
        fecha__year=filtro_gestion
    )

    if fecha_limite_inicial:
        movimientos_query = movimientos_query.filter(fecha__date__gte=fecha_limite_inicial)
    if hasta_str:
        hasta_date = parse_date(hasta_str)
        if hasta_date:
            movimientos_query = movimientos_query.filter(fecha__date__lte=hasta_date)

    movimientos_db = movimientos_query.select_related('unidad_destino', 'almacen', 'usuario').order_by('fecha', 'id')

    # 4. Procesar el Libro de Almacén del Almacén Seleccionado
    filas_kardex = []
    saldo_fisico = saldo_inicial_fisico
    saldo_valorado = saldo_inicial_valorado

    total_compras_periodo = Decimal('0.00')
    total_salidas_periodo = Decimal('0.00')
    total_cant_entradas = 0
    total_cant_salidas = 0

    for mov in movimientos_db:
        if mov.tipo == 'ENTRADA':
            saldo_fisico += mov.cantidad
            saldo_valorado += (mov.costo_total or Decimal('0.00'))
            ent_cant, sal_cant = mov.cantidad, 0
            ent_pt, sal_pt = (mov.costo_total or Decimal('0.00')), Decimal('0.00')
            total_compras_periodo += ent_pt
            total_cant_entradas += mov.cantidad
            detalle_concepto = f"INGRESO: {mov.referencia}"
        else:
            saldo_fisico -= mov.cantidad
            saldo_valorado -= (mov.costo_total or Decimal('0.00'))
            ent_cant, sal_cant = 0, mov.cantidad
            ent_pt, sal_pt = Decimal('0.00'), (mov.costo_total or Decimal('0.00'))
            total_salidas_periodo += sal_pt
            total_cant_salidas += mov.cantidad
            detalle_concepto = f"DESPACHO: {mov.unidad_destino.nombre}" if mov.unidad_destino else mov.referencia

        filas_kardex.append({
            'fecha': mov.fecha,
            'detalle': detalle_concepto,
            'almacen': mov.almacen.nombre if mov.almacen else 'Central',
            'ent_cant': ent_cant,
            'ent_pu': mov.costo_unitario if mov.tipo == 'ENTRADA' else Decimal('0.00'),
            'ent_pt': ent_pt,
            'sal_cant': sal_cant,
            'sal_pu': mov.costo_unitario if mov.tipo != 'ENTRADA' else Decimal('0.00'),
            'sal_pt': sal_pt,
            'saldo_cant': saldo_fisico,
            'saldo_pt': saldo_valorado,
            'fecha_vencimiento': mov.fecha_vencimiento,
            'estado_vencimiento': mov.estado_vencimiento,
            'dias_para_vencer': mov.dias_para_vencer,
        })

    filas_kardex.reverse()

    # Stock físico real actual en la estantería del almacén auditado
    inv_almacen = InventarioAlmacen.objects.filter(material=material, almacen=almacen_seleccionado).first()
    stock_custodia_real = inv_almacen.stock_fisico if inv_almacen else 0

    comprobacion_contable = {
        'inv_inicial_val': saldo_inicial_valorado,
        'inv_inicial_cant': saldo_inicial_fisico,
        'compras_val': total_compras_periodo,
        'compras_cant': total_cant_entradas,
        'salidas_val': total_salidas_periodo,
        'salidas_cant': total_cant_salidas,
        'inv_final_val': saldo_valorado,
        'inv_final_cant': saldo_fisico,
    }

    return render(request, 'inventario/kardex.html', {
        'material': material,
        'movimientos': filas_kardex,
        'comprobacion': comprobacion_contable,
        'almacenes_disponibles': almacenes_disponibles,
        'almacen_seleccionado': almacen_seleccionado,
        'filtro_almacen_id': filtro_almacen_id,
        'stock_custodia': stock_custodia_real,
        'desde': desde_str,
        'hasta': hasta_str,
        'filtro_gestion': filtro_gestion,
    })


@login_required
@rol_requerido(['ALMACENERO', 'KARDISTA', 'ADMINISTRADOR', 'ADMIN_ALMACENES'])
def kardex_fisico_pdf(request, id):
    """
    Kardex Físico (Ficha amarilla): Filtrable por Almacén y Rango de Fechas (Desde / Hasta).
    Calcula el saldo anterior físico y muestra solo los movimientos del periodo.
    """
    material = get_object_or_404(Material, id=id)
    perfil = request.user.perfilusuario
    rol = perfil.rol

    # 1. Filtros de Almacén y Fechas
    filtro_almacen_id = request.GET.get('almacen', '').strip()
    almacen = get_object_or_404(Almacen, id=filtro_almacen_id) if filtro_almacen_id else Almacen.objects.filter(tipo='CENTRAL', is_active=True).first()

    desde_str = request.GET.get('desde', '').strip()
    hasta_str = request.GET.get('hasta', '').strip()
    desde = parse_date(desde_str) if desde_str else None
    hasta = parse_date(hasta_str) if hasta_str else None

    # 2. Saldo Físico Anterior (Arrastre)
    saldo_anterior = 0
    if desde:
        movs_previos = MovimientoInventario.objects.filter(
            material=material,
            almacen=almacen,
            fecha__date__lt=desde
        )
        for m in movs_previos:
            if m.tipo == 'ENTRADA':
                saldo_anterior += m.cantidad
            else:
                saldo_anterior -= m.cantidad

    # 3. Movimientos del Periodo
    movs_periodo = MovimientoInventario.objects.filter(material=material, almacen=almacen)
    if desde:
        movs_periodo = movs_periodo.filter(fecha__date__gte=desde)
    if hasta:
        movs_periodo = movs_periodo.filter(fecha__date__lte=hasta)
    
    movs_db = movs_periodo.select_related('unidad_destino').order_by('fecha', 'id')

    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="kardex_fisico_{material.codigo}.pdf"'

    pdf = canvas.Canvas(response, pagesize=letter)
    width, height = letter
    pdf.setTitle(f"Kardex Físico - {material.codigo}")

    # Membrete
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(50, height - 40, "KARDEX DE CONTROL DE EXISTENCIAS")
    pdf.drawString(50, height - 52, "GOBIERNO AUTÓNOMO DEPARTAMENTAL DE POTOSÍ")
    pdf.setFont("Helvetica", 8)
    periodo_txt = f"Del {desde.strftime('%d/%m/%Y')} al {hasta.strftime('%d/%m/%Y')}" if (desde and hasta) else ("Desde " + desde.strftime('%d/%m/%Y') if desde else "Gestión Completa")
    pdf.drawString(50, height - 64, f"DEPÓSITO: {almacen.nombre.upper()} • PERÍODO: {periodo_txt}")

    pdf.drawString(50, height - 85, "Artículo / Subartículo:")
    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawString(160, height - 85, material.nombre)

    pdf.setFont("Helvetica", 9)
    pdf.drawString(50, height - 98, "Código Material:")
    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawString(160, height - 98, material.codigo)

    pdf.setFont("Helvetica", 9)
    pdf.drawString(420, height - 85, "U. de Manejo:")
    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawString(495, height - 85, material.unidad_medida_fk.codigo if material.unidad_medida_fk else material.unidad_medida)

    pdf.setFont("Helvetica", 9)
    pdf.drawString(420, height - 98, "Partida:")
    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawString(495, height - 98, material.partida.codigo if material.partida else "—")

    # Tabla
    headers_1 = ['FECHA', 'DETALLE / DESTINO', 'Nº INGRESO', 'Nº SALIDA', 'CONTROL FISICO (Cantidades)', '', '']
    headers_2 = ['', '', '', '', 'Entrada', 'Salida', 'Saldo']
    data = [headers_1, headers_2]

    saldo_fisico = saldo_anterior

    # Si hay fecha de inicio, pintar la fila de Arrastre
    if desde:
        data.append([desde.strftime('%d/%m/%Y'), 'SALDO ANTERIOR / ARRASTRE', '—', '—', '—', '—', str(saldo_anterior)])

    for mov in movs_db:
        nro_ingreso = mov.referencia.replace("NOTA INGRESO NRO ", "").replace("INGRESO: ", "") if "INGRESO" in mov.referencia else "—"
        nro_salida = mov.referencia.replace("DESPACHO: ", "").replace("SALIDA: ", "") if ("DESPACHO" in mov.referencia or "SALIDA" in mov.referencia) else "—"

        if mov.tipo == 'ENTRADA':
            saldo_fisico += mov.cantidad
            ent, sal = str(mov.cantidad), ""
        else:
            saldo_fisico -= mov.cantidad
            ent, sal = "", str(mov.cantidad)

        detalle = mov.unidad_destino.nombre if mov.unidad_destino else mov.referencia
        data.append([mov.fecha.strftime('%d/%m/%Y'), detalle[:35], nro_ingreso[:15], nro_salida[:15], ent, sal, str(saldo_fisico)])

    col_widths = [65, 175, 65, 65, 45, 45, 50]
    t = Table(data, colWidths=col_widths)
    last_row = len(data) - 1

    t.setStyle(TableStyle([
        ('SPAN', (0,0), (0,1)),
        ('SPAN', (1,0), (1,1)),
        ('SPAN', (2,0), (2,1)),
        ('SPAN', (3,0), (3,1)),
        ('SPAN', (4,0), (6,0)),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('ALIGN', (1,2), (1, last_row), 'LEFT'),
        ('FONTNAME', (0,0), (-1,1), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 8),
        ('GRID', (0,0), (-1, last_row), 0.5, colors.HexColor('#9CA3AF')),
        ('BACKGROUND', (0,0), (-1,1), colors.HexColor('#FDF2F2')),
        ('FONTNAME', (0,2), (-1, last_row), 'Helvetica'),
    ]))

    w_act, h_act = t.wrapOn(pdf, width - 100, 1500)
    pdf_y = height - 120 - h_act
    t.drawOn(pdf, 50, pdf_y)

    pdf.save()
    return response


@login_required
@rol_requerido(['ALMACENERO', 'KARDISTA', 'ADMINISTRADOR', 'ADMIN_ALMACENES'])
def kardex_pdf(request, id):
    """
    Kardex Valorado (PDF Oficial): Filtrable por Almacén y Rango de Fechas (Desde / Hasta).
    Calcula Saldos Físicos y Monetarios Anteriores y saldos por periodo.
    """
    material = get_object_or_404(Material, id=id)

    filtro_almacen_id = request.GET.get('almacen', '').strip()
    almacen = get_object_or_404(Almacen, id=filtro_almacen_id) if filtro_almacen_id else Almacen.objects.filter(tipo='CENTRAL', is_active=True).first()

    desde_str = request.GET.get('desde', '').strip()
    hasta_str = request.GET.get('hasta', '').strip()
    desde = parse_date(desde_str) if desde_str else None
    hasta = parse_date(hasta_str) if hasta_str else None

    # 1. Saldo Inicial Físico y Valorado Anterior
    saldo_anterior_cant = 0
    saldo_anterior_val = Decimal('0.00')

    if desde:
        movs_previos = MovimientoInventario.objects.filter(material=material, almacen=almacen, fecha__date__lt=desde)
        for m in movs_previos:
            if m.tipo == 'ENTRADA':
                saldo_anterior_cant += m.cantidad
                saldo_anterior_val += (m.costo_total or Decimal('0.00'))
            else:
                saldo_anterior_cant -= m.cantidad
                saldo_anterior_val -= (m.costo_total or Decimal('0.00'))

    # 2. Movimientos del periodo
    movs_periodo = MovimientoInventario.objects.filter(material=material, almacen=almacen)
    if desde:
        movs_periodo = movs_periodo.filter(fecha__date__gte=desde)
    if hasta:
        movs_periodo = movs_periodo.filter(fecha__date__lte=hasta)

    movs_db = movs_periodo.select_related('unidad_destino').order_by('fecha', 'id')

    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="kardex_valorado_{material.codigo}.pdf"'

    pdf = canvas.Canvas(response, pagesize=landscape(letter))
    width, height = landscape(letter)

    pdf.setTitle(f"Kardex Valorado - {material.codigo}")

    # Cabecera
    pdf.setFont("Helvetica-Bold", 15)
    pdf.drawString(50, height - 40, "KARDEX DE EXISTENCIAS VALORADO")
    pdf.setFont("Helvetica", 8)
    periodo_txt = f"Del {desde.strftime('%d/%m/%Y')} al {hasta.strftime('%d/%m/%Y')}" if (desde and hasta) else "Gestión Completa"
    pdf.drawString(50, height - 55, f"GOBIERNO AUTÓNOMO DEPARTAMENTAL DE POTOSÍ • DEPÓSITO: {almacen.nombre.upper()} • PERÍODO: {periodo_txt}")

    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawString(50, height - 75, f"Subartículo: {material.nombre}")
    pdf.drawString(50, height - 88, f"Código: {material.codigo}")
    pdf.drawString(420, height - 75, f"Partida: {material.partida.codigo} - {material.partida.nombre[:30]}")
    pdf.drawString(420, height - 88, f"U. Medida: {material.unidad_medida_fk.codigo if material.unidad_medida_fk else material.unidad_medida}")

    # Tabla 9 columnas
    data = [
        ['Fecha', 'Detalle / Referencia', 'Cantidades (Físico)', '', '', 'P. Unitario\n(Bs.)', 'Importes (Valorado)', '', ''],
        ['', '', 'Entrada', 'Salida', 'Saldo', '', 'Entrada', 'Salida', 'Saldo']
    ]

    saldo_fisico = saldo_anterior_cant
    saldo_valorado = saldo_anterior_val

    if desde:
        data.append([
            desde.strftime('%d/%m/%Y'), 'SALDO ANTERIOR / ARRASTRE',
            '—', '—', str(saldo_anterior_cant),
            '—', '—', '—', f"{saldo_anterior_val:.2f}"
        ])

    for mov in movs_db:
        if mov.tipo == 'ENTRADA':
            saldo_fisico += mov.cantidad
            saldo_valorado += (mov.costo_total or Decimal('0.00'))
            ent_c, sal_c = str(mov.cantidad), ""
            ent_v, sal_v = f"{(mov.costo_total or Decimal('0.00')):.2f}", ""
        else:
            saldo_fisico -= mov.cantidad
            saldo_valorado -= (mov.costo_total or Decimal('0.00'))
            ent_c, sal_c = "", str(mov.cantidad)
            ent_v, sal_v = "", f"{(mov.costo_total or Decimal('0.00')):.2f}"

        detalle = mov.unidad_destino.nombre if mov.unidad_destino else mov.referencia

        data.append([
            mov.fecha.strftime('%d/%m/%Y'),
            detalle[:30],
            ent_c, sal_c, str(saldo_fisico),
            f"{(mov.costo_unitario or Decimal('0.00')):.2f}",
            ent_v, sal_v, f"{saldo_valorado:.2f}"
        ])

    col_widths = [65, 185, 45, 45, 50, 60, 60, 60, 65]
    t = Table(data, colWidths=col_widths)
    last_row = len(data) - 1

    t.setStyle(TableStyle([
        ('SPAN', (0,0), (0,1)),
        ('SPAN', (1,0), (1,1)),
        ('SPAN', (2,0), (4,0)),
        ('SPAN', (5,0), (5,1)),
        ('SPAN', (6,0), (8,0)),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('ALIGN', (1,2), (1, last_row), 'LEFT'),
        ('ALIGN', (5,2), (-1,-1), 'RIGHT'),
        ('FONTNAME', (0,0), (-1,1), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 7.5),
        ('GRID', (0,0), (-1, last_row), 0.5, colors.HexColor('#D1D5DB')),
        ('BACKGROUND', (0,0), (-1,1), colors.HexColor('#F3F4F6')),
        ('FONTNAME', (0,2), (-1, last_row), 'Helvetica'),
    ]))

    w_act, h_act = t.wrapOn(pdf, width - 100, 2000)
    pdf_y = height - 105 - h_act
    t.drawOn(pdf, 50, pdf_y)

    pdf.save()
    return response

@login_required
@rol_requerido(['ADMINISTRADOR', 'ADMIN_ALMACENES'])
def almacen_list(request):
    query = request.GET.get('q', '').strip()
    almacenes = Almacen.objects.select_related(
        'unidad_organizacional', 'responsable'
    ).prefetch_related(
        'unidades_atendidas__secretaria'
    ).all()

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
@rol_requerido(['ADMINISTRADOR', 'ADMIN_ALMACENES'])
def crear_almacen(request):
    unidades = UnidadOrganizacional.objects.filter(is_active=True).order_by('nombre')
    usuarios = User.objects.filter(is_active=True).order_by('username')
    almacenes_padre = Almacen.objects.filter(is_active=True).order_by('nombre')

    if request.method == 'POST':
        nombre = request.POST.get('nombre', '').strip()
        descripcion = request.POST.get('descripcion', '').strip()
        tipo = request.POST.get('tipo', 'SUBALMACEN')
        unidad_id = request.POST.get('unidad_organizacional')
        responsable_id = request.POST.get('responsable')
        almacen_padre_id = request.POST.get('almacen_padre')
        unidades_atendidas_ids = request.POST.getlist('unidades_atendidas')

        if not nombre or not tipo:
            messages.error(request, "El Nombre y el Tipo de Almacén son campos obligatorios.")
            return redirect('crear_almacen')

        if Almacen.objects.filter(nombre=nombre).exists():
            messages.error(request, f"Ya existe un almacén registrado con el nombre '{nombre}'.")
            return redirect('crear_almacen')

        unidad = get_object_or_404(UnidadOrganizacional, id=unidad_id) if unidad_id else None
        responsable = get_object_or_404(User, id=responsable_id) if responsable_id else None
        padre = get_object_or_404(Almacen, id=almacen_padre_id) if almacen_padre_id else None

        try:
            with transaction.atomic():
                almacen = Almacen.objects.create(
                    nombre=nombre,
                    descripcion=descripcion,
                    tipo=tipo,
                    almacen_padre=padre,
                    unidad_organizacional=unidad,
                    responsable=responsable,
                    is_active=True
                )
                if unidades_atendidas_ids:
                    almacen.unidades_atendidas.set(unidades_atendidas_ids)
                    
                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Inventario',
                    accion='Crear Almacén',
                    descripcion=f'Se creó el almacén "{nombre}" de tipo {tipo}.'
                )
            messages.success(request, f"Almacén '{nombre}' registrado correctamente.")
            return redirect('almacen_list')

        except Exception as e:
            messages.error(request, f"Error de base de datos al registrar: {str(e)}")
            return redirect('crear_almacen')

    return render(request, 'inventario/crear_almacen.html', {
        'unidades': unidades,
        'usuarios': usuarios,
        'almacenes_padre': almacenes_padre
    })


@login_required
@rol_requerido(['ADMINISTRADOR', 'ADMIN_ALMACENES'])
def editar_almacen(request, id):
    almacen = get_object_or_404(Almacen, id=id)
    unidades = UnidadOrganizacional.objects.filter(is_active=True).order_by('nombre')
    usuarios = User.objects.filter(is_active=True).order_by('username')
    almacenes_padre = Almacen.objects.filter(is_active=True).exclude(id=almacen.id).order_by('nombre')

    if request.method == 'POST':
        nombre = request.POST.get('nombre', '').strip()
        descripcion = request.POST.get('descripcion', '').strip()
        tipo = request.POST.get('tipo', 'SUBALMACEN')
        unidad_id = request.POST.get('unidad_organizacional')
        responsable_id = request.POST.get('responsable')
        almacen_padre_id = request.POST.get('almacen_padre')
        unidades_atendidas_ids = request.POST.getlist('unidades_atendidas')
        is_active = request.POST.get('is_active') == 'true'

        if not nombre or not tipo:
            messages.error(request, "El Nombre y el Tipo son obligatorios.")
            return redirect('editar_almacen', id=almacen.id)

        try:
            with transaction.atomic():
                almacen.nombre = nombre
                almacen.descripcion = descripcion
                almacen.tipo = tipo
                almacen.unidad_organizacional = get_object_or_404(UnidadOrganizacional, id=unidad_id) if unidad_id else None
                almacen.responsable = get_object_or_404(User, id=responsable_id) if responsable_id else None
                almacen.almacen_padre = get_object_or_404(Almacen, id=almacen_padre_id) if almacen_padre_id else None
                almacen.is_active = is_active
                almacen.save()

                if unidades_atendidas_ids is not None:
                    almacen.unidades_atendidas.set(unidades_atendidas_ids)

                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Inventario',
                    accion='Editar Almacén',
                    descripcion=f'Se modificaron los datos del almacén "{nombre}".'
                )
            messages.success(request, f"Almacén '{nombre}' actualizado correctamente.")
            return redirect('almacen_list')

        except Exception as e:
            messages.error(request, f"Error al actualizar: {str(e)}")
            return redirect('editar_almacen', id=almacen.id)

    return render(request, 'inventario/editar_almacen.html', {
        'almacen': almacen,
        'unidades': unidades,
        'usuarios': usuarios,
        'almacenes_padre': almacenes_padre
    })




@login_required
@rol_requerido(['ALMACENERO', 'KARDISTA', 'ADMINISTRADOR', 'ADMIN_ALMACENES'])
def reporte_inventario(request):
    """
    Genera el reporte de Inventario Físico Valorado tabular oficial en PDF (Horizontal/Landscape).
    Soporta paginación automática y evita KeyErrors en ReportLab.
    """
    desde_str = request.GET.get('desde')
    hasta_str = request.GET.get('hasta')

    desde = parse_date(desde_str) if desde_str else datetime.date(2026, 1, 1)
    hasta = parse_date(hasta_str) if hasta_str else datetime.date(2026, 12, 31)

    materiales = Material.objects.filter(is_active=True).order_by('codigo')

    # Encabezados de doble nivel reglamentarios
    headers_1 = ['Código', 'Descripción del Material', 'Unid.', 'Saldo Inicial / Apertura', '', 'Entradas del Periodo', '', 'Salidas del Periodo', '', 'Saldos de Cierre', '']
    headers_2 = ['', '', '', 'Cant.', 'Importe (Bs.)', 'Cant.', 'Importe (Bs.)', 'Cant.', 'Importe (Bs.)', 'Cant.', 'Importe (Bs.)']

    filas_datos = []

    # Procesar matemáticamente los saldos físicos y monetarios por período
    for mat in materiales:
        mov_previos = MovimientoInventario.objects.filter(material=mat, fecha__date__lt=desde)
        ini_cant = 0
        ini_val = Decimal('0.00')
        for m in mov_previos:
            if m.tipo == 'ENTRADA':
                ini_cant += m.cantidad
                ini_val += (m.costo_total or Decimal('0.00'))
            else:
                ini_cant -= m.cantidad
                ini_val -= (m.costo_total or Decimal('0.00'))

        mov_periodo = MovimientoInventario.objects.filter(material=mat, fecha__date__range=[desde, hasta])
        ent_cant = 0
        ent_val = Decimal('0.00')
        sal_cant = 0
        sal_val = Decimal('0.00')
        for m in mov_periodo:
            if m.tipo == 'ENTRADA':
                ent_cant += m.cantidad
                ent_val += (m.costo_total or Decimal('0.00'))
            else:
                sal_cant += m.cantidad
                sal_val += (m.costo_total or Decimal('0.00'))

        # Cálculo de Saldos de Cierre
        fin_cant = ini_cant + ent_cant - sal_cant
        fin_val = ini_val + ent_val - sal_val

        filas_datos.append([
            mat.codigo,
            mat.nombre[:32], 
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
    response['Content-Disposition'] = f'inline; filename="inventario_fisico_valorado_{desde}_{hasta}.pdf"'

    pdf = canvas.Canvas(response, pagesize=landscape(letter))
    width, height = landscape(letter)

    pdf.setTitle(f"Inventario Valorado ({desde} a {hasta})")
    pdf.setSubject("SGA - Gobierno Autónomo Departamental de Potosí")
    pdf.setAuthor("Sistema de Gestión de Almacenes")

    # Paginación inteligente: 18 filas por página para que no se desborde
    filas_por_pagina = 18
    total_filas = len(filas_datos)
    total_paginas = max(1, (total_filas + filas_por_pagina - 1) // filas_por_pagina)

    col_widths = [65, 142, 35, 45, 60, 45, 60, 45, 60, 45, 65]

    for num_pagina in range(total_paginas):
        inicio = num_pagina * filas_por_pagina
        fin = inicio + filas_por_pagina
        filas_bloque = filas_datos[inicio:fin]

        # Encabezado de página
        pdf.setFont("Helvetica-Bold", 14)
        pdf.drawString(50, height - 40, "INVENTARIO FÍSICO VALORADO DE ALMACENES")
        pdf.setFont("Helvetica", 9)
        pdf.drawString(50, height - 55, "Gobierno Autónomo Departamental de Potosí — Unidad de Almacenes")
        pdf.drawString(50, height - 68, f"Período de Evaluación: Desde {desde.strftime('%d/%m/%Y')} hasta {hasta.strftime('%d/%m/%Y')}")
        pdf.drawRightString(width - 50, height - 40, f"Pág. {num_pagina + 1} de {total_paginas}")

        tabla_pagina = [headers_1, headers_2] + filas_bloque

        t = Table(tabla_pagina, colWidths=col_widths)
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
            ('FONTSIZE', (0, 0), (-1, 1), 7.5),
            ('BACKGROUND', (0, 0), (-1, 1), colors.HexColor('#F3F4F6')),

            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#D1D5DB')),
            ('LINEBELOW', (0, 1), (-1, 1), 1, colors.HexColor('#9CA3AF')),

            ('FONTNAME', (0, 2), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 2), (-1, -1), 7),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
        ])
        t.setStyle(t_style)

        # Dimensiones seguras sin colisiones de split
        w_act, h_act = t.wrapOn(pdf, width - 100, 1000)
        pdf_y = height - 85 - h_act
        t.drawOn(pdf, 50, pdf_y)

        # Salto a la siguiente página si hay más bloques
        if num_pagina < total_paginas - 1:
            pdf.showPage()

    pdf.save()

    Bitacora.objects.create(
        usuario=request.user,
        modulo='Inventario',
        accion='Exportar Inventario Valorado',
        descripcion=f'Se exportó el reporte de Inventario Físico Valorado desde {desde} hasta {hasta}.'
    )

    return response

@login_required
@rol_requerido(['ALMACENERO', 'KARDISTA', 'ADMINISTRADOR', 'ADMIN_ALMACENES'])
def movimientos(request):
    movimientos = MovimientoInventario.objects.all().order_by('-id')
    return render(request, 'inventario/movimientos.html', {'movimientos': movimientos})


@login_required
@rol_requerido(['ALMACENERO', 'KARDISTA', 'ADMINISTRADOR', 'ADMIN_ALMACENES'])
def inventario_por_unidad(request):
    """
    Tarjeta 4.5: Consulta de Stock y Cuotas del Formulario 005.
    FILTRO EN CASCADA:
    - Almacén -> Filtra Secretarías y Unidades que están autorizadas a ser atendidas por ese depósito.
    - Secretaría -> Filtra Unidades pertenecientes a esa secretaría.
    - Unidad -> Cruza existencias de ese almacén con las cuotas del Formulario 005 de su POA 2026.
    """
    from presupuestos.models import POA, DetalleProgramacionPOA
    from inventario.models import PartidaPresupuestaria

    perfil = request.user.perfilusuario
    rol = perfil.rol

    # 1. Almacenes a los que el usuario tiene acceso
    almacenes_usuario = obtener_almacenes_usuario(request.user)
    es_admin = (rol in ['ADMINISTRADOR', 'ADMIN_ALMACENES'])

    if es_admin:
        almacenes_disponibles = Almacen.objects.filter(is_active=True).order_by('nombre')
    else:
        almacenes_disponibles = almacenes_usuario

    # Captura de almacén seleccionado
    filtro_almacen_id = request.GET.get('almacen_id', '').strip()
    almacen_seleccionado = None

    if filtro_almacen_id:
        almacen_seleccionado = get_object_or_404(Almacen, id=filtro_almacen_id)
        if not es_admin and not perfil.tiene_acceso_almacen(almacen_seleccionado):
            messages.error(request, f"No tiene autorización para ver: {almacen_seleccionado.nombre}")
            almacen_seleccionado = almacenes_disponibles.first()
    else:
        # Por defecto Almacén Central o el asignado al encargado
        almacen_central = Almacen.objects.filter(tipo='CENTRAL', is_active=True).first()
        if almacen_central and (es_admin or perfil.tiene_acceso_almacen(almacen_central)):
            almacen_seleccionado = almacen_central
            filtro_almacen_id = str(almacen_central.id)
        else:
            almacen_seleccionado = almacenes_disponibles.first()
            if almacen_seleccionado:
                filtro_almacen_id = str(almacen_seleccionado.id)

    almacenes_a_consultar = [almacen_seleccionado] if almacen_seleccionado else almacenes_disponibles

    # ========================================================
    # 2. FILTRADO EN CASCADA: DEPENDENCIAS ATENDIDAS POR ESTE ALMACÉN
    # ========================================================
    if almacen_seleccionado:
        if almacen_seleccionado.tipo == 'CENTRAL':
            # Central atiende a todas las unidades salvo que tenga asignación restrictiva
            if almacen_seleccionado.unidades_atendidas.exists():
                unidades_del_almacen = almacen_seleccionado.unidades_atendidas.filter(is_active=True)
            else:
                unidades_del_almacen = UnidadOrganizacional.objects.filter(is_active=True)
        else:
            # Subalmacén (SIGEPRO, UNASBA): SOLO atiende a sus unidades asignadas y a su propia unidad
            unidades_ids = list(almacen_seleccionado.unidades_atendidas.filter(is_active=True).values_list('id', flat=True))
            if almacen_seleccionado.unidad_organizacional_id:
                unidades_ids.append(almacen_seleccionado.unidad_organizacional_id)
            unidades_del_almacen = UnidadOrganizacional.objects.filter(id__in=unidades_ids, is_active=True)
    else:
        unidades_del_almacen = UnidadOrganizacional.objects.filter(is_active=True)

    # Secretarías que tienen oficinas atendidas por este almacén
    secretarias = Secretaria.objects.filter(
        unidades_organizacionales__in=unidades_del_almacen,
        is_active=True
    ).distinct().order_by('nombre')

    filtro_secretaria_id = request.GET.get('secretaria_id', '').strip()
    filtro_unidad_id = request.GET.get('unidad_id', '').strip()

    # Si cambió de almacén y la secretaría previa ya no corresponde, se resetea
    if filtro_secretaria_id and not secretarias.filter(id=filtro_secretaria_id).exists():
        filtro_secretaria_id = ''

    # Unidades dependientes de la secretaría seleccionada
    if filtro_secretaria_id:
        unidades_query = unidades_del_almacen.filter(secretaria_id=filtro_secretaria_id).order_by('nombre')
    else:
        unidades_query = unidades_del_almacen.order_by('nombre')

    # Si la unidad previa ya no pertenece a este almacén/secretaría, se resetea
    if filtro_unidad_id and not unidades_query.filter(id=filtro_unidad_id).exists():
        filtro_unidad_id = ''

    secretaria_seleccionada = Secretaria.objects.filter(id=filtro_secretaria_id).first() if filtro_secretaria_id else None
    unidad_seleccionada = UnidadOrganizacional.objects.filter(id=filtro_unidad_id).first() if filtro_unidad_id else None

    # ========================================================
    # 3. BASE DE INVENTARIO: AISLADO AL ALMACÉN Y AL POA DE LA UNIDAD
    # ========================================================
    inventario_qs = InventarioAlmacen.objects.filter(
        almacen__in=almacenes_a_consultar,
        stock_fisico__gt=0
    )

    if unidad_seleccionada:
        partidas_poa_ids = POA.objects.filter(
            unidad=unidad_seleccionada,
            gestion=2026
        ).values_list('partida_id', flat=True)

        inventario_qs = inventario_qs.filter(material__partida_id__in=partidas_poa_ids)

    elif secretaria_seleccionada:
        unidades_sec = unidades_del_almacen.filter(secretaria=secretaria_seleccionada)
        partidas_poa_sec = POA.objects.filter(
            unidad__in=unidades_sec,
            gestion=2026
        ).values_list('partida_id', flat=True)

        inventario_qs = inventario_qs.filter(material__partida_id__in=partidas_poa_sec)

    # Métricas superiores
    total_materiales = inventario_qs.values('material_id').distinct().count()
    total_almacenes = len(almacenes_a_consultar)
    total_stock_fisico = inventario_qs.aggregate(total=Sum('stock_fisico'))['total'] or 0

    # 4. Agrupación por Partidas
    partidas_ids = inventario_qs.values_list('material__partida_id', flat=True).distinct()
    partidas_en_inventario = PartidaPresupuestaria.objects.filter(id__in=partidas_ids).order_by('codigo')

    grupos_por_partida = []

    for part in partidas_en_inventario:
        items_partida = inventario_qs.filter(material__partida=part).select_related(
            'almacen', 'material', 'material__unidad_medida_fk'
        ).order_by('material__nombre')

        filas = []
        for inv in items_partida:
            mat = inv.material
            item_005 = None
            poa_partida = None

            if unidad_seleccionada:
                item_005 = DetalleProgramacionPOA.objects.filter(
                    poa__unidad=unidad_seleccionada,
                    poa__gestion=2026,
                    material=mat
                ).first()

                poa_partida = POA.objects.filter(
                    unidad=unidad_seleccionada,
                    partida=mat.partida,
                    gestion=2026
                ).first()

            lote_activo = MovimientoInventario.objects.filter(
                material=mat,
                almacen=inv.almacen,
                tipo='ENTRADA',
                saldo_disponible_lote__gt=0
            ).order_by('fecha', 'id').first()
            costo_ref = lote_activo.costo_unitario if lote_activo else Decimal('0.00')

            stock_disp = inv.stock_disponible
            if item_005:
                saldo_cuota = item_005.cantidad_disponible
                tope_permitido = min(stock_disp, saldo_cuota)
                tiene_cuota = True
            else:
                saldo_cuota = None
                tope_permitido = stock_disp
                tiene_cuota = False

            filas.append({
                'inventario': inv,
                'material': mat,
                'almacen': inv.almacen,
                'stock_fisico': inv.stock_fisico,
                'stock_disponible': stock_disp,
                'item_005': item_005,
                'tiene_cuota': tiene_cuota,
                'cuota_programada': item_005.cantidad_programada if item_005 else None,
                'cuota_consumida': item_005.cantidad_consumida if item_005 else 0,
                'saldo_cuota': saldo_cuota,
                'tope_permitido': tope_permitido,
                'costo_referencial': costo_ref,
                'saldo_poa': poa_partida.monto_disponible if poa_partida else None
            })

        grupos_por_partida.append({
            'partida': part,
            'items': filas,
            'total_items': len(filas),
            'stock_total_partida': sum(f['stock_disponible'] for f in filas)
        })

    return render(request, 'inventario/stock_por_unidad.html', {
        'secretarias': secretarias,
        'unidades': unidades_query,
        'almacenes_disponibles': almacenes_disponibles,
        'almacen_seleccionado': almacen_seleccionado,
        'filtro_almacen_id': filtro_almacen_id,
        'secretaria_seleccionada': secretaria_seleccionada,
        'unidad_seleccionada': unidad_seleccionada,
        'filtro_secretaria_id': filtro_secretaria_id,
        'filtro_unidad_id': filtro_unidad_id,
        'grupos_por_partida': grupos_por_partida,
        'total_materiales': total_materiales,
        'total_stock_fisico': total_stock_fisico,
        'total_almacenes': total_almacenes,
        'es_admin': es_admin,
    })

@login_required
@rol_requerido(['ALMACENERO', 'ADMINISTRADOR', 'ADMIN_ALMACENES'])
def entrada_inventario(request):
    perfil = getattr(request.user, 'perfilusuario', None)
    rol = perfil.rol if perfil else 'UNIDAD_SOLICITANTE'
    
    if rol in ['ADMINISTRADOR', 'ADMIN_ALMACENES']:
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
        payload_raw = request.POST.get('payload')

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

        if not perfil.tiene_acceso_almacen(almacen):
            messages.error(request, f"No tiene autorización para registrar ingresos en: {almacen.nombre}")
            return redirect('entrada_inventario')

        if NotaIngreso.objects.filter(nro_nota=nro_nota).exists():
            messages.error(request, f"Ya existe una Nota de Ingreso registrada con el nro: {nro_nota}.")
            return redirect('entrada_inventario')

        try:
            with transaction.atomic():
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

                for item_key, item_data in payload.items():
                    material = Material.objects.get(id=item_key)
                    cantidad = int(item_data.get('cantidad', 0))
                    precio_u = Decimal(str(item_data.get('precio_unitario', '0.00')))

                    if cantidad <= 0 or precio_u < 0:
                        raise ValueError("Las cantidades y precios de los materiales deben ser mayores a cero.")

                    precio_total = cantidad * precio_u

                    NotaIngresoDetalle.objects.create(
                        nota_ingreso=nota,
                        material=material,
                        cantidad=cantidad,
                        precio_unitario=precio_u,
                        precio_total=precio_total
                    )

                    inv, created = InventarioAlmacen.objects.get_or_create(
                        material=material,
                        almacen=almacen,
                        defaults={'stock_fisico': 0, 'stock_reservado': 0}
                    )
                    
                    stock_anterior = inv.stock_fisico
                    inv.stock_fisico += cantidad
                    inv.save()

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
                        saldo_disponible_lote=cantidad
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
@rol_requerido(['ALMACENERO', 'ADMINISTRADOR', 'ADMIN_ALMACENES'])
def salida_inventario(request):
    materiales = Material.objects.filter(stock_actual__gt=0)
    unidades = UnidadOrganizacional.objects.all().order_by('nombre')
    
    perfil = getattr(request.user, 'perfilusuario', None)
    if perfil and perfil.rol in ['ADMINISTRADOR', 'ADMIN_ALMACENES']:
        almacenes_disponibles = Almacen.objects.filter(is_active=True)
    else:
        almacenes_disponibles = perfil.almacenes_autorizados.filter(is_active=True)
        
    material_preseleccionado = request.GET.get('material')
    
    if request.method == 'POST':
        material_id = request.POST.get('material')
        cantidad = int(request.POST.get('cantidad', 0))
        referencia = request.POST.get('referencia', '').strip()
        unidad_destino_id = request.POST.get('unidad_destino')
        almacen_origen_id = request.POST.get('almacen_origen')

        if not material_id or not unidad_destino_id or not almacen_origen_id or cantidad <= 0:
            messages.error(request, 'Debe completar todos los campos obligatorios.')
            return redirect('salida_inventario')

        material = get_object_or_404(Material, id=material_id)
        unidad_destino = get_object_or_404(UnidadOrganizacional, id=unidad_destino_id)
        almacen = get_object_or_404(Almacen, id=almacen_origen_id)

        try:
            validar_operacion_almacen(request.user, almacen)
        except ValidationError as e:
            messages.error(request, str(e))
            return redirect('salida_inventario')

        if not request.user.is_superuser and perfil.rol not in ['ADMINISTRADOR', 'ADMIN_ALMACENES']:
            if not almacen.unidades_atendidas.filter(id=unidad_destino.id).exists():
                messages.error(request, f"Violación de Regla: El almacén '{almacen.nombre}' no está autorizado para despachar materiales a la unidad '{unidad_destino.nombre}'.")
                return redirect('salida_inventario')

        try:
            registrar_salida_valorada_peps(
                material=material,
                almacen=almacen,
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
                descripcion=f'Despacho de {cantidad} u. de {material.nombre} a {unidad_destino.nombre} desde {almacen.nombre}.'
            )

            messages.success(request, f'Salida procesada correctamente desde {almacen.nombre}.')
            return redirect('inventario')

        except Exception as e:
            messages.error(request, f'Error al procesar la salida: {str(e)}')
            return redirect('salida_inventario')

    return render(request, 'inventario/salida.html', {
        'materiales': materiales,
        'unidades': unidades,
        'almacenes': almacenes_disponibles,
        'material_preseleccionado': material_preseleccionado
    })


@login_required
@rol_requerido(['ALMACENERO', 'ADMINISTRADOR', 'ADMIN_ALMACENES'])
def registrar_baja(request):
    materiales = Material.objects.filter(stock_actual__gt=0).order_by('codigo')
    material_preseleccionado = request.GET.get('material')

    if request.method == 'POST':
        material_id = request.POST.get('material')
        cantidad = int(request.POST.get('cantidad', 0))
        motivo = request.POST.get('motivo', '').strip()
        referencia = request.POST.get('referencia', '').strip()

        if not material_id or not motivo or not referencia:
            messages.error(request, 'Todos los campos son obligatorios.')
            return redirect('registrar_baja')

        material = get_object_or_404(Material, id=material_id)

        if cantidad <= 0:
            messages.error(request, 'La cantidad de baja debe ser mayor a cero.')
            return redirect('registrar_baja')

        if cantidad > material.stock_actual:
            messages.error(request, 'No puede dar de baja una cantidad superior al stock actual disponible en almacén.')
            return redirect('registrar_baja')

        almacenes_user = obtener_almacenes_usuario(request.user)
        almacen_id_form = request.POST.get('almacen')

        if almacen_id_form:
            almacen = get_object_or_404(Almacen, id=almacen_id_form)
        else:
            almacen = almacenes_user.first()

        if not almacen:
            messages.error(request, 'No tiene ningún almacén asignado para procesar bajas de inventario.')
            return redirect('registrar_baja')
        try:
            registrar_salida_valorada_peps(
                material=material,
                almacen=almacen,
                cantidad_salida=cantidad,
                tipo_movimiento='BAJA',
                referencia=f"BAJA: {motivo} ({referencia})",
                usuario=request.user
            )

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

    return render(request, 'inventario/baja.html', {
        'materiales': materiales,
        'material_preseleccionado': material_preseleccionado
    })


@login_required
@rol_requerido(['ALMACENERO', 'ADMINISTRADOR'])
def nuevo_material(request):
    partidas = PartidaPresupuestaria.objects.all().order_by('codigo')
    unidades = UnidadMedida.objects.all().order_by('nombre')

    if request.method == 'POST':
        partida_id = request.POST.get('partida')
        codigo = request.POST.get('codigo', '').strip()
        nombre = request.POST.get('nombre', '').strip()
        descripcion = request.POST.get('descripcion', '').strip()
        unidad_id = request.POST.get('unidad_medida_fk')
        stock_minimo = request.POST.get('stock_minimo', 5)
        
        # Captura fecha de vencimiento (opcional)
        fecha_venc_raw = request.POST.get('fecha_vencimiento', '').strip()
        fecha_vencimiento = parse_date(fecha_venc_raw) if fecha_venc_raw else None

        if not codigo or not nombre or not unidad_id:
            messages.error(request, "Los campos Código, Nombre y Unidad de Medida son obligatorios.")
            return redirect('nuevo_material')

        partida = get_object_or_404(PartidaPresupuestaria, id=partida_id) if partida_id else None
        unidad = get_object_or_404(UnidadMedida, id=unidad_id)

        if Material.objects.filter(codigo=codigo).exists():
            messages.error(request, f"Ya existe un material con el código '{codigo}'.")
            return redirect('nuevo_material')

        try:
            Material.objects.create(
                partida=partida,
                codigo=codigo,
                nombre=nombre,
                descripcion=descripcion,
                unidad_medida_fk=unidad,
                unidad_medida=unidad.nombre,
                stock_actual=0,
                stock_minimo=int(stock_minimo) if stock_minimo else 5,
                fecha_vencimiento=fecha_vencimiento  # <-- Guardar vencimiento
            )

            Bitacora.objects.create(
                usuario=request.user,
                modulo='Inventario',
                accion='Registrar Material',
                descripcion=f'Se registró el material {nombre} ({codigo}). Vencimiento: {fecha_venc_raw or "No perecedero"}'
            )

            messages.success(request, f"El material '{nombre}' ha sido registrado en el catálogo.")
            return redirect('inventario')

        except Exception as e:
            messages.error(request, f"Error al guardar: {str(e)}")
            return redirect('nuevo_material')

    return render(request, 'inventario/nuevo_material.html', {
        'partidas': partidas,
        'unidades': unidades
    })


@login_required
@rol_requerido(['ALMACENERO', 'ADMINISTRADOR'])
def editar_material(request, id):
    material = get_object_or_404(Material, id=id)
    unidades = UnidadMedida.objects.all().order_by('nombre')
    partidas = PartidaPresupuestaria.objects.all().order_by('codigo')

    if request.method == 'POST':
        nombre = request.POST.get('nombre', '').strip()
        descripcion = request.POST.get('descripcion', '').strip()
        unidad_id = request.POST.get('unidad_medida_fk')
        stock_minimo = request.POST.get('stock_minimo', 5)
        
        # Actualiza fecha de vencimiento (opcional)
        fecha_venc_raw = request.POST.get('fecha_vencimiento', '').strip()
        fecha_vencimiento = parse_date(fecha_venc_raw) if fecha_venc_raw else None

        if not nombre or not unidad_id:
            messages.error(request, "Nombre y Unidad de Medida son obligatorios.")
            return redirect('editar_material', id=id)

        unidad = get_object_or_404(UnidadMedida, id=unidad_id)

        try:
            with transaction.atomic():
                material.nombre = nombre
                material.descripcion = descripcion
                material.unidad_medida_fk = unidad
                material.unidad_medida = unidad.nombre
                material.stock_minimo = int(stock_minimo) if stock_minimo else 5
                material.fecha_vencimiento = fecha_vencimiento  # <-- Actualizar
                material.save()

                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Inventario',
                    accion='Editar Material',
                    descripcion=f'Se actualizaron los datos de {nombre} ({material.codigo})'
                )

            messages.success(request, f"Ficha del material '{nombre}' actualizada correctamente.")
            return redirect('inventario')

        except Exception as e:
            messages.error(request, f"Error al actualizar: {str(e)}")
            return redirect('editar_material', id=id)

    return render(request, 'inventario/editar_material.html', {
        'material': material,
        'unidades': unidades,
        'partidas': partidas
    })

@login_required
@rol_requerido(['ALMACENERO', 'ADMINISTRADOR', 'ADMIN_ALMACENES'])
def crear_unidad_medida_ajax(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            codigo = data.get('codigo', '').strip().upper()
            nombre = data.get('nombre', '').strip()

            if not codigo or not nombre:
                return JsonResponse({'ok': False, 'error': 'Código y Nombre son obligatorios.'}, status=400)

            if UnidadMedida.objects.filter(codigo=codigo).exists():
                return JsonResponse({'ok': False, 'error': f"El código de unidad '{codigo}' ya existe en el sistema."}, status=400)

            unidad = UnidadMedida.objects.create(codigo=codigo, nombre=nombre)
            return JsonResponse({'ok': True, 'id': unidad.id, 'codigo': unidad.codigo, 'nombre': unidad.nombre})
        except Exception as e:
            return JsonResponse({'ok': False, 'error': str(e)}, status=400)

    return JsonResponse({'ok': False, 'error': 'Método no permitido.'}, status=405)


@login_required
@rol_requerido(['ALMACENERO', 'ADMINISTRADOR', 'ADMIN_ALMACENES'])
def establecer_saldo_inicial(request, id):
    material = get_object_or_404(Material, id=id)

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
                material.stock_actual += cantidad
                material.save()

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

    return render(request, 'inventario/establecer_saldo_inicial.html', {'material': material})




@login_required
@rol_requerido(['ADMINISTRADOR', 'ADMIN_ALMACENES'])
def toggle_material(request, id):
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
@rol_requerido(['ALMACENERO', 'KARDISTA', 'ADMINISTRADOR', 'ADMIN_ALMACENES'])
def lotes_list(request):
    perfil = getattr(request.user, 'perfilusuario', None)
    rol = perfil.rol if perfil else 'UNIDAD_SOLICITANTE'
    
    if rol in ['ADMINISTRADOR', 'ADMIN_ALMACENES']:
        almacenes_disponibles = Almacen.objects.filter(is_active=True).order_by('nombre')
    else:
        almacenes_disponibles = perfil.almacenes_autorizados.filter(is_active=True).order_by('nombre')

    filtro_almacen_id = request.GET.get('almacen', '').strip()
    query_busqueda = request.GET.get('q', '').strip()

    lotes_query = MovimientoInventario.objects.filter(
        tipo='ENTRADA',
        saldo_disponible_lote__gt=0
    ).select_related('material', 'almacen', 'usuario')

    if filtro_almacen_id:
        almacen_seleccionado = get_object_or_404(Almacen, id=filtro_almacen_id)
        if not perfil.tiene_acceso_almacen(almacen_seleccionado):
            messages.error(request, f"No tiene autorización para auditar lotes en: {almacen_seleccionado.nombre}")
            return redirect('lotes_list')
        lotes_query = lotes_query.filter(almacen=almacen_seleccionado)

    if query_busqueda:
        lotes_query = lotes_query.filter(
            Q(material__nombre__icontains=query_busqueda) |
            Q(material__codigo__icontains=query_busqueda) |
            Q(referencia__icontains=query_busqueda)
        )

    lotes_query = lotes_query.order_by('fecha', 'id')
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
@rol_requerido(['ALMACENERO', 'KARDISTA', 'ADMINISTRADOR', 'ADMIN_ALMACENES'])
def reporte_consumo_unidades(request):
    """
    Tarjeta 6: Reporte Consolidado Institucional de Consumo por Dependencia (Formulario 005 / SABS).
    Agrupa por Secretaría y Unidad, calculando insumos físicos retirados,
    gasto valorado por PEPS y comparativa porcentual frente al techo POA.
    """
    from organizacion.models import Secretaria, UnidadOrganizacional
    from presupuestos.models import POA
    from datetime import date

    # 1. Filtros de Fechas y Secretaría
    desde_str = request.GET.get('desde', '').strip()
    hasta_str = request.GET.get('hasta', '').strip()
    filtro_secretaria_id = request.GET.get('secretaria_id', '').strip()

    desde = parse_date(desde_str) if desde_str else date(2026, 1, 1)
    hasta = parse_date(hasta_str) if hasta_str else date(2026, 12, 31)

    secretarias = Secretaria.objects.filter(is_active=True).order_by('nombre')
    secretarias_query = secretarias
    if filtro_secretaria_id:
        secretarias_query = secretarias_query.filter(id=filtro_secretaria_id)

    # 2. Base de movimientos de salida en el periodo
    salidas_periodo = MovimientoInventario.objects.filter(
        tipo='SALIDA',
        fecha__date__range=[desde, hasta]
    )

    datos_por_secretaria = []
    total_general_fisico = 0
    total_general_valorado = Decimal('0.00')
    total_general_poa = Decimal('0.00')

    for sec in secretarias_query:
        unidades_sec = sec.unidades_organizacionales.filter(is_active=True).order_by('nombre')
        filas_unidades = []

        total_sec_fisico = 0
        total_sec_valorado = Decimal('0.00')
        total_sec_poa = Decimal('0.00')

        for u in unidades_sec:
            # Salidas dirigidas a esta oficina
            salidas_u = salidas_periodo.filter(unidad_destino=u)
            
            tot_cant = salidas_u.aggregate(s=Sum('cantidad'))['s'] or 0
            tot_val = salidas_u.aggregate(s=Sum('costo_total'))['s'] or Decimal('0.00')

            # Presupuesto POA total asignado a esta oficina en 2026
            poas_u = POA.objects.filter(unidad=u, gestion=2026)
            poa_inicial = poas_u.aggregate(s=Sum('monto_inicial'))['s'] or Decimal('0.00')
            poa_disponible = poas_u.aggregate(s=Sum('monto_disponible'))['s'] or Decimal('0.00')

            # Porcentaje de consumo sobre el presupuesto de apertura
            pct_consumo = round((float(tot_val) / float(poa_inicial) * 100), 1) if poa_inicial > 0 else 0.0

            total_sec_fisico += tot_cant
            total_sec_valorado += tot_val
            total_sec_poa += poa_inicial

            # Listado de los 5 materiales más retirados por esta oficina (para detalle rápido)
            top_materiales = salidas_u.values(
                'material__codigo', 'material__nombre', 'material__unidad_medida'
            ).annotate(
                cant_total=Sum('cantidad'),
                val_total=Sum('costo_total')
            ).order_by('-val_total')[:4]

            filas_unidades.append({
                'unidad': u,
                'total_cant': tot_cant,
                'total_val': tot_val,
                'poa_inicial': poa_inicial,
                'poa_disponible': poa_disponible,
                'pct_consumo': pct_consumo,
                'top_materiales': top_materiales,
                'tiene_consumo': (tot_cant > 0)
            })

        total_general_fisico += total_sec_fisico
        total_general_valorado += total_sec_valorado
        total_general_poa += total_sec_poa

        pct_sec = round((float(total_sec_valorado) / float(total_sec_poa) * 100), 1) if total_sec_poa > 0 else 0.0

        datos_por_secretaria.append({
            'secretaria': sec,
            'unidades': filas_unidades,
            'total_fisico': total_sec_fisico,
            'total_valorado': total_sec_valorado,
            'total_poa': total_sec_poa,
            'pct_consumo': pct_sec,
            'tiene_consumo': (total_sec_fisico > 0)
        })

    pct_global = round((float(total_general_valorado) / float(total_general_poa) * 100), 1) if total_general_poa > 0 else 0.0

    return render(request, 'inventario/consumo_unidades.html', {
        'secretarias': secretarias,
        'datos_por_secretaria': datos_por_secretaria,
        'filtro_secretaria_id': filtro_secretaria_id,
        'desde': desde.strftime('%Y-%m-%d'),
        'hasta': hasta.strftime('%Y-%m-%d'),
        'total_general_fisico': total_general_fisico,
        'total_general_valorado': total_general_valorado,
        'total_general_poa': total_general_poa,
        'pct_global': pct_global,
    })

@login_required
def obtener_items_compra_view(request):
    """
    API AJAX: Jala de la Orden de Compra los materiales, proveedor,
    cantidades solicitadas, precios, ejecutora y observaciones.
    """
    compra_id = request.GET.get('compra_id')
    if not compra_id:
        return JsonResponse({'ok': False, 'items': []})

    from compras.models import CompraMenor
    compra = get_object_or_404(
        CompraMenor.objects.select_related('proveedor', 'solicitud_origen', 'solicitud_origen__unidad_solicitante'),
        id=compra_id
    )
    solicitud = compra.solicitud_origen

    items_data = []
    observaciones = ""
    cod_ejecutora = ""

    if solicitud:
        observaciones = f"REGISTRO POR ADQUISICIÓN: {solicitud.justificacion}"
        cod_ejecutora = solicitud.unidad_solicitante.codigo_sigep or solicitud.unidad_solicitante.nombre[:25]

        for d in solicitud.detalles.select_related('material', 'material__partida', 'material__unidad_medida_fk'):
            if d.material:
                cant = d.cantidad_aprobada if d.cantidad_aprobada is not None else d.cantidad_solicitada
                precio = float(d.precio_unitario_referencial) if d.precio_unitario_referencial > 0 else 0.0

                items_data.append({
                    'material_id': d.material.id,
                    'codigo': d.material.codigo,
                    'nombre': d.material.nombre,
                    'unidad': d.material.unidad_medida_fk.nombre.upper() if d.material.unidad_medida_fk else d.material.unidad_medida.upper(),
                    'partida': d.material.partida.codigo if d.material.partida else "—",
                    'codigo_ejecutora': cod_ejecutora,
                    'codigo_presupuestario': solicitud.codigo,
                    'cantidad': cant,
                    'precio_unitario': precio,
                    'precio_total': round(cant * precio, 2)
                })

    return JsonResponse({
        'ok': True,
        'proveedor_id': compra.proveedor.id if compra.proveedor else None,
        'proveedor_nombre': compra.proveedor.razon_social if compra.proveedor else '',
        'proveedor_nit': compra.proveedor.nit if compra.proveedor else '',
        'nro_orden': compra.nro_orden,
        'observaciones': observaciones,
        'items': items_data
    })
@login_required
def inventario_por_almacen(request):
    perfil = getattr(request.user, 'perfilusuario', None)
    rol = perfil.rol if perfil else 'UNIDAD_SOLICITANTE'
    
    if rol in ['ADMINISTRADOR', 'ADMIN_ALMACENES']:
        almacenes_disponibles = Almacen.objects.filter(is_active=True).order_by('nombre')
    else:
        almacenes_disponibles = perfil.almacenes_autorizados.filter(is_active=True).order_by('nombre')

    filtro_almacen_id = request.GET.get('almacen', '').strip()
    filtro_sin_stock = request.GET.get('sin_stock', 'false').lower() == 'true'
    query_busqueda = request.GET.get('q', '').strip()

    almacen_seleccionado = None
    if filtro_almacen_id:
        almacen_seleccionado = get_object_or_404(Almacen, id=filtro_almacen_id)
        if not perfil.tiene_acceso_almacen(almacen_seleccionado):
            messages.error(request, f"No tiene autorización para auditar el almacén: {almacen_seleccionado.nombre}")
            return redirect('inventario_por_almacen')

    if almacen_seleccionado:
        inventario_query = InventarioAlmacen.objects.filter(almacen=almacen_seleccionado).select_related('material', 'material__partida')
        if query_busqueda:
            inventario_query = inventario_query.filter(
                Q(material__nombre__icontains=query_busqueda) |
                Q(material__codigo__icontains=query_busqueda)
            )
        if filtro_sin_stock:
            inventario_query = inventario_query.filter(stock_fisico__lte=F('stock_reservado'))
        inventario_query = inventario_query.order_by('material__nombre')
    else:
        inventario_query = Material.objects.all().select_related('partida')
        if query_busqueda:
            inventario_query = inventario_query.filter(
                Q(nombre__icontains=query_busqueda) |
                Q(codigo__icontains=query_busqueda)
            )
        if filtro_sin_stock:
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
@rol_requerido(['ALMACENERO', 'KARDISTA', 'ADMINISTRADOR', 'ADMIN_ALMACENES'])
def nota_ingreso_list(request):
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

    return render(request, 'inventario/entrada_list.html', {'page_obj': page_obj, 'query': query})


@login_required
@rol_requerido(['ALMACENERO', 'KARDISTA', 'ADMINISTRADOR', 'ADMIN_ALMACENES'])
def nota_salida_list(request):
    query = request.GET.get('q', '').strip()
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

    return render(request, 'inventario/salida_list.html', {'page_obj': page_obj, 'query': query})


@login_required
@rol_requerido(['ALMACENERO', 'KARDISTA', 'ADMINISTRADOR', 'ADMIN_ALMACENES'])
def detalle_nota_ingreso(request, id):
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
    return render(request, 'inventario/detalle_entrada.html', {'nota': nota})


@login_required
@rol_requerido(['ALMACENERO', 'KARDISTA', 'ADMINISTRADOR', 'ADMIN_ALMACENES'])
def detalle_nota_salida(request, id):
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
    return render(request, 'inventario/detalle_salida.html', {'nota': nota})


@login_required
@rol_requerido(['ADMINISTRADOR', 'ADMIN_ALMACENES'])
def toggle_almacen(request, id):
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
@rol_requerido(['ALMACENERO', 'KARDISTA', 'ADMINISTRADOR', 'ADMIN_ALMACENES'])
def transferencia_list(request):
    perfil = getattr(request.user, 'perfilusuario', None)
    rol = perfil.rol if perfil else 'UNIDAD_SOLICITANTE'
    
    if rol in ['ADMINISTRADOR', 'ADMIN_ALMACENES']:
        transferencias = Transferencia.objects.select_related('origen', 'destino', 'usuario_envia', 'usuario_recibe').all()
    else:
        almacenes_user = perfil.almacenes_autorizados.all()
        transferencias = Transferencia.objects.filter(
            Q(origen__in=almacenes_user) | Q(destino__in=almacenes_user)
        ).select_related('origen', 'destino', 'usuario_envia', 'usuario_recibe')

    transferencias = transferencias.order_by('-id')
    paginator = Paginator(transferencias, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'inventario/transferencia_list.html', {'page_obj': page_obj})


@login_required
@rol_requerido(['ALMACENERO', 'ADMINISTRADOR', 'ADMIN_ALMACENES'])
def enviar_transferencia(request):
    perfil = getattr(request.user, 'perfilusuario', None)
    rol = perfil.rol if perfil else 'UNIDAD_SOLICITANTE'
    
    if rol in ['ADMINISTRADOR', 'ADMIN_ALMACENES']:
        almacenes_origen = Almacen.objects.filter(is_active=True).order_by('nombre')
    else:
        almacenes_origen = perfil.almacenes_autorizados.filter(is_active=True).order_by('nombre')

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

        if not perfil.tiene_acceso_almacen(origen):
            messages.error(request, f"No tiene autorización para despachar materiales desde: {origen.nombre}")
            return redirect('enviar_transferencia')

        try:
            with transaction.atomic():
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

                for item_key, item_data in payload.items():
                    material = Material.objects.get(id=item_key)
                    cantidad = int(item_data.get('cantidad', 0))

                    if cantidad <= 0:
                        raise ValueError(f"La cantidad de '{material.nombre}' debe ser mayor a cero.")

                    mov = registrar_salida_valorada_peps(
                        material=material,
                        almacen=origen,
                        cantidad_salida=cantidad,
                        tipo_movimiento='SALIDA',
                        referencia=f"TRANSFERENCIA ENVIADA {nro_correlativo}",
                        usuario=request.user
                    )

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
@rol_requerido(['ALMACENERO', 'ADMINISTRADOR', 'ADMIN_ALMACENES'])
def recibir_transferencia(request, id):
    transferencia = get_object_or_404(Transferencia, id=id)
    perfil = getattr(request.user, 'perfilusuario', None)

    if transferencia.estado != 'EN_TRANSITO':
        messages.error(request, "Esta transferencia ya ha sido procesada o cancelada.")
        return redirect('transferencia_list')

    if not perfil.tiene_acceso_almacen(transferencia.destino):
        messages.error(request, f"No tiene autorización para recibir inventario en el almacén: {transferencia.destino.nombre}")
        return redirect('transferencia_list')

    try:
        with transaction.atomic():
            transferencia = Transferencia.objects.select_for_update().get(id=id)
            detalles = transferencia.detalles.all()

            for det in detalles:
                inv_destino, created = InventarioAlmacen.objects.get_or_create(
                    material=det.material,
                    almacen=transferencia.destino,
                    defaults={'stock_fisico': 0, 'stock_reservado': 0}
                )
                stock_anterior = inv_destino.stock_fisico
                inv_destino.stock_fisico += det.cantidad
                inv_destino.save()

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
                    saldo_disponible_lote=det.cantidad
                )

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
    Genera el formato oficial exacto de la "NOTA DE RECEPCIÓN" del GAD Potosí (Horizontal/Landscape).
    Idéntico al documento físico oficial N° 162 con 10 columnas y 3 firmas.
    """
    nota = get_object_or_404(
        NotaIngreso.objects.prefetch_related('detalles__material__partida', 'detalles__material__unidad_medida_fk')
        .select_related('proveedor', 'usuario', 'almacen_destino', 'compra_menor_origen', 'compra_menor_origen__solicitud_origen'),
        id=id
    )

    response = HttpResponse(content_type='application/pdf')
    nro_limpio = nota.nro_nota.replace("NI-", "").replace("NI", "")
    response['Content-Disposition'] = f'inline; filename="nota_recepcion_{nro_limpio}.pdf"'

    # 1. Configuración Horizontal Landscape
    pdf = canvas.Canvas(response, pagesize=landscape(letter))
    width, height = landscape(letter)  # 792 x 612 pt
    pdf.setTitle(f"Nota de Recepción N° {nro_limpio}")

    # --- CABECERA INSTITUCIONAL ---
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawCentredString(width / 2.0, height - 35, "GOBIERNO AUTÓNOMO DEL DEPARTAMENTO DE POTOSÍ")
    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawCentredString(width / 2.0, height - 47, "UNIDAD DE ALMACENES")

    # Título Principal con espaciado oficial
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawCentredString(width / 2.0, height - 70, "N O T A    D E    R E C E P C I Ó N")

    # Número correlativo a la derecha (N°. 162)
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawRightString(width - 45, height - 35, f"Nº. {nro_limpio}")

    # --- METADATOS DEL DOCUMENTO (Idéntico a la foto de la cabecera) ---
    pdf.setFont("Helvetica-Bold", 8)
    # Fila 1
    pdf.drawString(45, height - 95, "Proveedor:")
    pdf.setFont("Helvetica", 8)
    pdf.drawString(100, height - 95, f"{nota.proveedor.razon_social.upper()}")

    pdf.setFont("Helvetica-Bold", 8)
    pdf.drawString(520, height - 95, "Fac. Nº.")
    pdf.setFont("Helvetica", 8)
    pdf.drawString(565, height - 95, f"{nota.factura or '—'}")

    pdf.setFont("Helvetica-Bold", 8)
    pdf.drawString(670, height - 95, "C-31:")
    pdf.setFont("Helvetica", 8)
    pdf.drawString(700, height - 95, f"{nota.c31 or '—'}")

    # Fila 2
    pdf.setFont("Helvetica-Bold", 8)
    pdf.drawString(45, height - 110, "C.I. NIT. Nº.")
    pdf.setFont("Helvetica", 8)
    pdf.drawString(105, height - 110, f"{nota.proveedor.nit}")

    pdf.setFont("Helvetica-Bold", 8)
    pdf.drawString(320, height - 110, "Fecha de FACTURA:")
    pdf.setFont("Helvetica", 8)
    fecha_fac = nota.fecha.strftime('POTOSI, %Y-%m-%d') if nota.fecha else "POTOSI, —"
    pdf.drawString(420, height - 110, f"{fecha_fac}")

    pdf.setFont("Helvetica-Bold", 8)
    orden_txt = nota.compra_menor_origen.nro_orden if nota.compra_menor_origen else (f"COMPRA {nota.id}/2026")
    pdf.drawString(580, height - 110, f"Orden de: {orden_txt.upper()}")

    # --- TABLA OFICIAL DE 10 COLUMNAS ---
    headers = [
        'ITEM',
        'DESCRIPCION',
        'UNIDAD\nMANEJO',
        'PEDIDO',
        'ENTREG.',
        'COSTO\nUNITARIO',
        'COSTO\nTOTAL',
        'CODIGO\nPRESUPUESTARIO',
        'CODIGO\nEJECUTORA',
        'PARTIDA\nPRESUP.'
    ]
    data = [headers]

    # Recuperar datos de la solicitud y unidad ejecutora
    solicitud_origen = nota.compra_menor_origen.solicitud_origen if nota.compra_menor_origen else None
    cod_ejecutora = solicitud_origen.unidad_solicitante.codigo_sigep if (solicitud_origen and solicitud_origen.unidad_solicitante.codigo_sigep) else ""
    cod_presup = solicitud_origen.codigo if solicitud_origen else ""

    total_nota = Decimal('0.00')
    detalles = nota.detalles.select_related('material', 'material__partida', 'material__unidad_medida_fk').all()

    for idx, det in enumerate(detalles, start=1):
        total_nota += det.precio_total
        u_manejo = det.material.unidad_medida_fk.nombre.upper() if (det.material and det.material.unidad_medida_fk) else (det.material.unidad_medida.upper() if det.material else "PAQUETE")
        partida_cod = det.material.partida.codigo if (det.material and det.material.partida) else "—"

        data.append([
            str(idx),
            det.material.nombre.upper()[:55],
            u_manejo[:10],
            str(det.cantidad),  # Cantidad Pedida
            str(det.cantidad),  # Cantidad Entregada
            f"{det.precio_unitario:.2f}",
            f"{det.precio_total:.2f}",
            str(cod_presup),
            str(cod_ejecutora),
            str(partida_cod)
        ])

    # Fila de Destino y Costo Total (Idéntico a la fila gris de la foto)
    data.append([
        'DESTINO: ALMACENES', '', '', '', '',
        'COSTO TOTAL:', f"{total_nota:.2f}", '', '', ''
    ])

    # Anchos de columna exactos para landscape (Suma = 705 pt, márgenes seguros de 43.5 pt)
    col_widths = [25, 205, 55, 40, 40, 55, 65, 80, 75, 65]
    t = Table(data, colWidths=col_widths)
    last_row = len(data) - 1

    t_style = TableStyle([
        ('SPAN', (0, last_row), (4, last_row)),  # DESTINO: ALMACENES ocupa 5 columnas
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('ALIGN', (1, 1), (1, last_row - 1), 'LEFT'),      # Descripción a la izquierda
        ('ALIGN', (0, last_row), (0, last_row), 'LEFT'),   # DESTINO: ALMACENES a la izquierda
        ('ALIGN', (5, 1), (6, last_row), 'RIGHT'),         # Precios a la derecha
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 7),
        ('GRID', (0, 0), (-1, last_row), 0.5, colors.HexColor('#9CA3AF')),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#E5E7EB')),
        ('FONTNAME', (0, last_row), (-1, last_row), 'Helvetica-Bold'),
        ('BACKGROUND', (0, last_row), (-1, last_row), colors.HexColor('#F3F4F6')),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2.5),
        ('TOPPADDING', (0, 0), (-1, -1), 2.5),
    ])
    t.setStyle(t_style)

    # Dibujar la tabla
    w_act, h_act = t.wrapOn(pdf, 705, height - 170)
    pdf_y = height - 130 - h_act
    t.drawOn(pdf, 45, pdf_y)

    # --- OBSERVACIONES ---
    obs_y = pdf_y - 20
    pdf.setFont("Helvetica-Bold", 7.5)
    pdf.drawString(45, obs_y, "Observaciones:")
    pdf.setFont("Helvetica", 7.5)
    obs_texto = getattr(nota, 'observaciones', None) or (solicitud_origen.justificacion if solicitud_origen else "REGISTRO POR LA ADQUISICION DE MATERIALES PARA LAS DIFERENTES UNIDADES DE LA INSTITUCION")
    pdf.drawString(115, obs_y, f"{obs_texto.upper()[:120]}")

    # --- 3 FIRMAS OFICIALES (Almacenes, Bienes y Servicios, Kardex Valorado) ---
    y_firmas = 45
    pdf.setFont("Helvetica", 7.5)

    # Firma 1: Responsable Almacenes
    pdf.drawString(80, y_firmas + 15, "___________________________________")
    pdf.drawString(95, y_firmas, "Responsable Almacenes")

    # Firma 2: Responsable Bienes y Servicios
    pdf.drawString(310, y_firmas + 15, "___________________________________")
    pdf.drawString(320, y_firmas, "Responsable Bienes y Servicios")

    # Firma 3: Responsable Kardex Valorado
    pdf.drawString(560, y_firmas + 15, "___________________________________")
    pdf.drawString(570, y_firmas, "Responsable Kardex Valorado")

    pdf.save()
    return response


@login_required
@rol_requerido(['ALMACENERO', 'KARDISTA', 'ADMINISTRADOR', 'ADMIN_ALMACENES'])
def reporte_inventario_oficial_pdf(request):
    desde_str = request.GET.get('desde')
    hasta_str = request.GET.get('hasta')

    desde = parse_date(desde_str) if desde_str else datetime.date(2026, 1, 1)
    hasta = parse_date(hasta_str) if hasta_str else datetime.date(2026, 5, 31)

    materiales = Material.objects.all().order_by('codigo')

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

    pdf = canvas.Canvas(response, pagesize=landscape(letter))
    pdf.setTitle(f"Inventario Valorado ({desde.strftime('%d/%m/%Y')} a {hasta.strftime('%d/%m/%Y')})")
    pdf.setSubject("SGA - Gobierno Autónomo Departamental de Potosí")
    pdf.setAuthor("Sistema de Gestión de Almacenes")

    width, height = landscape(letter)

    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(50, height - 40, "GOBIERNO AUTÓNOMO DEPARTAMENTAL DE POTOSÍ")
    pdf.drawString(50, height - 52, "SECRETARÍA DPTAL. ADMINISTRATIVA Y FINANCIERA")
    pdf.drawString(50, height - 64, "UNIDAD DE CONTABILIDAD")

    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(250, height - 90, "INVENTARIO FÍSICO VALORADO DE MATERIALES")
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

    Bitacora.objects.create(
        usuario=request.user,
        modulo='Inventario',
        accion='Exportar Inventario Oficial PDF',
        descripcion=f'Se exportó en PDF el Inventario Físico Valorado desde {desde} hasta {hasta}.'
    )
    return response


@login_required
@rol_requerido(['ADMINISTRADOR', 'ADMIN_ALMACENES'])
def cierre_conciliacion_view(request):
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
                    conteo_real_raw = request.POST.get(f"real_{item.id}", '').strip()
                    if conteo_real_raw == "":
                        continue
                    
                    conteo_real = int(conteo_real_raw)
                    stock_sistema_previo = item.stock_fisico
                    diferencia = conteo_real - stock_sistema_previo

                    if diferencia > 0:
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
                        registrar_salida_valorada_peps(
                            material=material,
                            almacen=almacen_op,
                            cantidad_salida=abs(diferencia),
                            tipo_movimiento='BAJA',
                            referencia=f"AJUSTE MERMA CONCILIACION {gestion_cierre}",
                            usuario=request.user
                        )
                        item.refresh_from_db()

                    last_entrada_valorada = MovimientoInventario.objects.filter(material=material, tipo='ENTRADA').order_by('-fecha').first()
                    costo_u_apertura = last_entrada_valorada.costo_unitario if last_entrada_valorada else Decimal('0.00')
                    fecha_apertura = datetime.datetime(gestion_nueva, 1, 1, 0, 0, 0, tzinfo=timezone.get_current_timezone())

                    MovimientoInventario.objects.create(
                        material=material,
                        almacen=almacen_op,
                        tipo='ENTRADA',
                        cantidad=item.stock_fisico,
                        costo_unitario=costo_u_apertura,
                        costo_total=item.stock_fisico * costo_u_apertura,
                        stock_anterior=0,
                        stock_resultante=item.stock_fisico,
                        referencia=f"SALDO INICIAL APERTURA GESTIÓN {gestion_nueva}",
                        usuario=request.user,
                        saldo_disponible_lote=item.stock_fisico,
                        fecha=fecha_apertura
                    )

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