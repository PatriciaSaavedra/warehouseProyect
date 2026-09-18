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
from .services import registrar_salida_valorada_peps, validar_operacion_almacen, obtener_inventario_para_unidad, unidades_ya_asignadas, confirmar_recepcion_transferencia

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
    Vista principal de existencias con acordeón interactivo.
    """
    materiales = Material.objects.filter(is_active=True).select_related(
        'partida', 'unidad_medida_fk'
    ).prefetch_related(
        Prefetch(
            'inventarios_almacen',
            queryset=InventarioAlmacen.objects.select_related(
                'almacen', 
                'almacen__unidad_organizacional', 
                'almacen__unidad_organizacional__secretaria'
            ).filter(stock_fisico__gt=0)
        )
    ).order_by('codigo')

    return render(request, 'inventario/index.html', {'materiales': materiales})


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

        # Tarjeta 2: solo unidades activas y regla de un único almacén por unidad
        unidades_seleccionadas = UnidadOrganizacional.objects.filter(
            id__in=unidades_atendidas_ids, is_active=True
        ) if unidades_atendidas_ids else UnidadOrganizacional.objects.none()

        conflictos = unidades_ya_asignadas(list(unidades_seleccionadas.values_list('id', flat=True)))
        if conflictos:
            detalle = '; '.join(f'"{nombre_u}" ya está asignada a "{nombre_a}"' for nombre_u, nombre_a in conflictos.values())
            messages.error(
                request,
                f'No se puede registrar el almacén: {detalle}. '
                'Una Unidad Organizacional solo puede ser atendida por UN almacén a la vez. '
                'Retire primero la unidad del almacén actual si desea cambiarla.'
            )
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
                    almacen.unidades_atendidas.set(unidades_seleccionadas)

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

        # Tarjeta 2: solo unidades ACTIVAS son administrables desde el formulario.
        # Las asociaciones preexistentes a unidades INACTIVAS se conservan: el formulario
        # no las muestra, por lo que un .set() directo borraría la relación silenciosamente.
        unidades_seleccionadas = UnidadOrganizacional.objects.filter(
            id__in=unidades_atendidas_ids, is_active=True
        ) if unidades_atendidas_ids else UnidadOrganizacional.objects.none()
        inactivas_preexistentes = almacen.unidades_atendidas.filter(is_active=False)

        conflictos = unidades_ya_asignadas(
            list(unidades_seleccionadas.values_list('id', flat=True)),
            almacen_excluir_id=almacen.id,
        )
        if conflictos:
            detalle = '; '.join(f'"{nombre_u}" ya está asignada a "{nombre_a}"' for nombre_u, nombre_a in conflictos.values())
            messages.error(
                request,
                f'No se puede actualizar el almacén: {detalle}. '
                'Una Unidad Organizacional solo puede ser atendida por UN almacén a la vez. '
                'Retire primero la unidad del almacén actual si desea cambiarla.'
            )
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
                    # Preserva las relaciones preexistentes con unidades inactivas
                    ids_finales = list(unidades_seleccionadas.values_list('id', flat=True))
                    ids_finales += list(inactivas_preexistentes.values_list('id', flat=True))
                    almacen.unidades_atendidas.set(ids_finales)

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
        'unidades_inactivas_asignadas': almacen.unidades_atendidas.filter(is_active=False).order_by('nombre'),
        'usuarios': usuarios,
        'almacenes_padre': almacenes_padre
    })


@login_required
@rol_requerido(['ALMACENERO', 'KARDISTA', 'ADMINISTRADOR', 'ADMIN_ALMACENES'])
def kardex_pdf(request, id):
    material = get_object_or_404(Material, id=id)
    movimientos_db = MovimientoInventario.objects.filter(material=material).order_by('fecha')

    data = [
        ['Fecha', 'Detalle / Referencia', 'Cantidades (Físico)', '', '', 'P. Unitario\n(Bs.)', 'Importes (Valorado)', '', ''],
        ['', '', 'Entrada', 'Salida', 'Saldo', '', 'Entrada', 'Salida', 'Saldo']
    ]

    saldo_fisico = 0
    saldo_valorado = Decimal('0.00')

    for mov in movimientos_db:
        if mov.tipo == 'ENTRADA':
            saldo_fisico += mov.cantidad
            saldo_valorado += (mov.costo_total or Decimal('0.00'))
            ent_cant = str(mov.cantidad)
            sal_cant = ""
            ent_imp = f"{(mov.costo_total or Decimal('0.00')):.2f}"
            sal_imp = ""
        else:
            saldo_fisico -= mov.cantidad
            saldo_valorado -= (mov.costo_total or Decimal('0.00'))
            ent_cant = ""
            sal_cant = str(mov.cantidad)
            ent_imp = ""
            sal_imp = f"{(mov.costo_total or Decimal('0.00')):.2f}"

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

    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="kardex_{material.codigo}.pdf"'

    pdf = canvas.Canvas(response, pagesize=landscape(letter))
    width, height = landscape(letter)

    pdf.setTitle(f"Kardex Valorado - {material.codigo}")
    pdf.setSubject("SGA - Gobierno Autónomo Departamental de Potosí")
    pdf.setAuthor("Sistema de Gestión de Almacenes")

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

    col_widths = [70, 182, 50, 50, 50, 60, 60, 60, 60]
    t = Table(data, colWidths=col_widths)
    last_row = len(data) - 1

    t_style = TableStyle([
        ('SPAN', (0, 0), (0, 1)),
        ('SPAN', (1, 0), (1, 1)),
        ('SPAN', (2, 0), (4, 0)),
        ('SPAN', (5, 0), (5, 1)),
        ('SPAN', (6, 0), (8, 0)),
        ('ALIGN', (0, 0), (8, last_row), 'CENTER'),
        ('ALIGN', (1, 2), (1, last_row), 'LEFT'),
        ('ALIGN', (5, 2), (8, last_row), 'RIGHT'),
        ('FONTNAME', (0, 0), (8, 1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (8, 1), 9),
        ('BACKGROUND', (0, 0), (8, 1), colors.HexColor('#F3F4F6')), 
        ('GRID', (0, 0), (8, last_row), 0.5, colors.HexColor('#D1D5DB')), 
        ('FONTNAME', (0, 2), (8, last_row), 'Helvetica'),
        ('FONTSIZE', (0, 2), (8, last_row), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
    ])
    t.setStyle(t_style)

    avail_width = width - 100
    avail_height = height - 150
    w_actual, h_actual = t.wrapOn(pdf, avail_width, avail_height)
    pdf_y = height - 125 - h_actual
    t.drawOn(pdf, 50, pdf_y)

    pdf.save()

    Bitacora.objects.create(
        usuario=request.user,
        modulo='Inventario',
        accion='Exportar PDF Kardex',
        descripcion=f'Se descargó el PDF del Kardex de existencias para {material.nombre} ({material.codigo})'
    )
    return response


@login_required
@rol_requerido(['ALMACENERO', 'KARDISTA', 'ADMINISTRADOR', 'ADMIN_ALMACENES'])
def reporte_inventario(request):
    desde_str = request.GET.get('desde')
    hasta_str = request.GET.get('hasta')

    desde = parse_date(desde_str) if desde_str else datetime.date(2026, 1, 1)
    hasta = parse_date(hasta_str) if hasta_str else datetime.date(2026, 12, 31)

    materiales = Material.objects.all().order_by('codigo')

    headers_1 = ['Código', 'Descripción del Material', 'Unid.', 'Saldo Inicial / Apertura', '', 'Entradas del Periodo', '', 'Salidas del Periodo', '', 'Saldos de Cierre', '']
    headers_2 = ['', '', '', 'Cant.', 'Importe (Bs.)', 'Cant.', 'Importe (Bs.)', 'Cant.', 'Importe (Bs.)', 'Cant.', 'Importe (Bs.)']
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
    response['Content-Disposition'] = f'inline; filename="inventario_fisico_valorado_{desde}_{hasta}.pdf"'

    pdf = canvas.Canvas(response, pagesize=landscape(letter))
    width, height = landscape(letter)

    pdf.setTitle(f"Inventario Valorado ({desde} a {hasta})")
    pdf.setSubject("SGA - Gobierno Autónomo Departamental de Potosí")
    pdf.setAuthor("Sistema de Gestión de Almacenes")

    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(50, height - 50, "INVENTARIO FÍSICO VALORADO DE ALMACENES")
    pdf.setFont("Helvetica", 10)
    pdf.drawString(50, height - 75, "Gobierno Autónomo Departamental de Potosí")
    pdf.drawString(50, height - 90, f"Período de Evaluación: Desde {desde.strftime('%d/%m/%Y')} hasta {hasta.strftime('%d/%m/%Y')}")

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
def kardex(request, id):
    material = get_object_or_404(Material, id=id)
    perfil = request.user.perfilusuario
    rol = perfil.rol

    almacenes_disponibles = obtener_almacenes_usuario(request.user)

    if not almacenes_disponibles.exists() and rol not in ['ADMINISTRADOR', 'ADMIN_ALMACENES']:
        messages.error(request, "No tiene ningún almacén asignado bajo su responsabilidad.")
        return redirect('inventario')

    filtro_almacen_id = request.GET.get('almacen', '').strip()

    if not filtro_almacen_id and rol == 'ALMACENERO':
        almacen_seleccionado = almacenes_disponibles.first()
        filtro_almacen_id = str(almacen_seleccionado.id) if almacen_seleccionado else ''
    elif filtro_almacen_id:
        almacen_seleccionado = get_object_or_404(Almacen, id=filtro_almacen_id)
        if not perfil.tiene_acceso_almacen(almacen_seleccionado):
            messages.error(request, f"Violación de Seguridad: No tiene autorización para auditar el almacén: {almacen_seleccionado.nombre}")
            return redirect('kardex', id=material.id)
    else:
        almacen_seleccionado = None

    desde_str = request.GET.get('desde', '').strip()
    hasta_str = request.GET.get('hasta', '').strip()
    filtro_mes = request.GET.get('mes', '').strip()
    filtro_gestion = request.GET.get('gestion', '2026').strip()
    filtro_tipo = request.GET.get('tipo_movimiento', '').strip()

    movimientos_query = MovimientoInventario.objects.filter(material=material)

    if almacen_seleccionado:
        movimientos_query = movimientos_query.filter(almacen=almacen_seleccionado)
    else:
        movimientos_query = movimientos_query.filter(almacen__in=almacenes_disponibles)

    try:
        gestion_ano = int(filtro_gestion)
        movimientos_query = movimientos_query.filter(fecha__year=gestion_ano)
    except ValueError:
        gestion_ano = 2026

    if filtro_mes:
        try:
            movimientos_query = movimientos_query.filter(fecha__month=int(filtro_mes))
        except ValueError:
            pass

    fecha_limite_inicial = None
    if desde_str:
        fecha_limite_inicial = parse_date(desde_str)
        if fecha_limite_inicial:
            movimientos_query = movimientos_query.filter(fecha__date__gte=fecha_limite_inicial)

    if hasta_str:
        hasta_date = parse_date(hasta_str)
        if hasta_date:
            movimientos_query = movimientos_query.filter(fecha__date__lte=hasta_date)

    if filtro_tipo:
        movimientos_query = movimientos_query.filter(tipo=filtro_tipo)

    query_previos = MovimientoInventario.objects.filter(material=material, fecha__year=gestion_ano)
    if almacen_seleccionado:
        query_previos = query_previos.filter(almacen=almacen_seleccionado)
    else:
        query_previos = query_previos.filter(almacen__in=almacenes_disponibles)

    if fecha_limite_inicial:
        query_previos = query_previos.filter(fecha__date__lt=fecha_limite_inicial)
    elif filtro_mes:
        query_previos = query_previos.filter(fecha__month__lt=int(filtro_mes))

    saldo_inicial_fisico = 0
    saldo_inicial_valorado = Decimal('0.00')

    for m in query_previos.order_by('fecha', 'id'):
        if m.tipo == 'ENTRADA':
            saldo_inicial_fisico += m.cantidad
            saldo_inicial_valorado += (m.costo_total or Decimal('0.00'))
        else:
            saldo_inicial_fisico -= m.cantidad
            saldo_inicial_valorado -= (m.costo_total or Decimal('0.00'))

    movimientos_db = movimientos_query.order_by('fecha', 'id')
    movimientos_valorados = []
    saldo_fisico = saldo_inicial_fisico
    saldo_valorado = saldo_inicial_valorado

    for mov in movimientos_db:
        if mov.tipo == 'ENTRADA':
            saldo_fisico += mov.cantidad
            saldo_valorado += (mov.costo_total or Decimal('0.00'))
            entrada_cant, salida_cant = mov.cantidad, 0
            entrada_imp, salida_imp = (mov.costo_total or Decimal('0.00')), Decimal('0.00')
        else:
            saldo_fisico -= mov.cantidad
            saldo_valorado -= (mov.costo_total or Decimal('0.00'))
            entrada_cant, salida_cant = 0, mov.cantidad
            entrada_imp, salida_imp = Decimal('0.00'), (mov.costo_total or Decimal('0.00'))

        movimientos_valorados.append({
            'fecha': mov.fecha,
            'detalle': mov.unidad_destino.nombre if mov.unidad_destino else mov.referencia,
            'almacen': mov.almacen.nombre if mov.almacen else 'Global',
            'usuario': mov.usuario.username,
            'entrada_cant': entrada_cant,
            'salida_cant': salida_cant,
            'saldo_cant': saldo_fisico,
            'precio_unitario': mov.costo_unitario,
            'entrada_importe': entrada_imp,
            'salida_importe': salida_imp,
            'saldo_importe': saldo_valorado,
        })

    movimientos_valorados.reverse()

    if almacen_seleccionado:
        inv_almacen = InventarioAlmacen.objects.filter(material=material, almacen=almacen_seleccionado).first()
        stock_custodia = inv_almacen.stock_fisico if inv_almacen else 0
    else:
        stock_custodia = material.stock_actual

    meses_lista = [
        (1, 'Enero'), (2, 'Febrero'), (3, 'Marzo'), (4, 'Abril'),
        (5, 'Mayo'), (6, 'Junio'), (7, 'Julio'), (8, 'Agosto'),
        (9, 'Septiembre'), (10, 'Octubre'), (11, 'Noviembre'), (12, 'Diciembre')
    ]

    return render(request, 'inventario/kardex.html', {
        'material': material,
        'movimientos': movimientos_valorados,
        'almacenes_disponibles': almacenes_disponibles,
        'almacen_seleccionado': almacen_seleccionado,
        'filtro_almacen_id': filtro_almacen_id,
        'stock_custodia': stock_custodia,
        'desde': desde_str,
        'hasta': hasta_str,
        'filtro_mes': filtro_mes,
        'filtro_gestion': filtro_gestion,
        'filtro_tipo': filtro_tipo,
        'meses': meses_lista,
        'saldo_inicial_cant': saldo_inicial_fisico,
        'saldo_inicial_val': saldo_inicial_valorado,
        'rol': rol,
    })


@login_required
@rol_requerido(['ALMACENERO', 'KARDISTA', 'ADMINISTRADOR', 'ADMIN_ALMACENES'])
def inventario_por_unidad(request):
    perfil = request.user.perfilusuario
    rol = perfil.rol

    almacenes_usuario = obtener_almacenes_usuario(request.user)

    es_central = (
        rol in ['ADMINISTRADOR', 'ADMIN_ALMACENES'] or 
        almacenes_usuario.filter(tipo='CENTRAL').exists()
    )

    if es_central:
        almacenes_visibles = Almacen.objects.filter(is_active=True)
    else:
        almacenes_visibles = almacenes_usuario

    secretarias = Secretaria.objects.filter(is_active=True).order_by('nombre')
    unidades_query = UnidadOrganizacional.objects.filter(is_active=True).select_related('secretaria').order_by('nombre')

    filtro_secretaria_id = request.GET.get('secretaria_id', '').strip()
    filtro_unidad_id = request.GET.get('unidad_id', '').strip()

    secretaria_seleccionada = None
    unidad_seleccionada = None

    inventario_qs = InventarioAlmacen.objects.filter(
        almacen__in=almacenes_visibles,
        stock_fisico__gt=0
    )

    if filtro_unidad_id:
        unidad_seleccionada = get_object_or_404(UnidadOrganizacional, id=filtro_unidad_id)
        secretaria_seleccionada = unidad_seleccionada.secretaria
        almacenes_atendidos = almacenes_visibles.filter(unidades_atendidas=unidad_seleccionada)
        inventario_qs = inventario_qs.filter(almacen__in=almacenes_atendidos)

    elif filtro_secretaria_id:
        secretaria_seleccionada = get_object_or_404(Secretaria, id=filtro_secretaria_id)
        unidades_sec = unidades_query.filter(secretaria=secretaria_seleccionada)
        almacenes_atendidos = almacenes_visibles.filter(unidades_atendidas__in=unidades_sec)
        inventario_qs = inventario_qs.filter(almacen__in=almacenes_atendidos)

    total_materiales = inventario_qs.values('material_id').distinct().count()
    total_almacenes = inventario_qs.values('almacen_id').distinct().count()
    total_stock_fisico = inventario_qs.aggregate(total=Sum('stock_fisico'))['total'] or 0

    inventario = inventario_qs.select_related(
        'almacen', 
        'almacen__unidad_organizacional',
        'almacen__unidad_organizacional__secretaria', 
        'material', 
        'material__partida', 
        'material__unidad_medida_fk'
    ).order_by('material__nombre')

    return render(request, 'inventario/stock_por_unidad.html', {
        'secretarias': secretarias,
        'unidades': unidades_query,
        'secretaria_seleccionada': secretaria_seleccionada,
        'unidad_seleccionada': unidad_seleccionada,
        'filtro_secretaria_id': filtro_secretaria_id,
        'filtro_unidad_id': filtro_unidad_id,
        'inventario': inventario,
        'total_materiales': total_materiales,
        'total_stock_fisico': total_stock_fisico,
        'total_almacenes': total_almacenes,
        'es_central': es_central,
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
@rol_requerido(['ALMACENERO', 'ADMINISTRADOR', 'ADMIN_ALMACENES'])
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

        if not codigo or not nombre or not unidad_id:
            messages.error(request, "Los campos con asterisco (Código, Nombre y Unidad de Medida) son obligatorios.")
            return redirect('nuevo_material')

        partida = get_object_or_404(PartidaPresupuestaria, id=partida_id) if partida_id else None
        unidad = get_object_or_404(UnidadMedida, id=unidad_id)

        if partida and not codigo.startswith(partida.codigo):
            messages.error(
                request, 
                f"Error de Codificación SABS: El código del material debe comenzar obligatoriamente con el código de su partida presupuestaria ({partida.codigo}). Ejemplo: {partida.codigo}-0001"
            )
            return redirect('nuevo_material')

        if '-' in codigo:
            prefijo, correlativo = codigo.split('-', 1)
            if not correlativo.strip():
                messages.error(request, "Error de Codificación SABS: Debe ingresar un número correlativo después del guion.")
                return redirect('nuevo_material')

        if Material.objects.filter(codigo=codigo).exists():
            messages.error(request, f"Ya existe un material registrado con el código '{codigo}'.")
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
                stock_minimo=int(stock_minimo)
            )

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

    return render(request, 'inventario/nuevo_material.html', {'partidas': partidas, 'unidades': unidades})


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
@rol_requerido(['ALMACENERO', 'ADMINISTRADOR', 'ADMIN_ALMACENES'])
def editar_material(request, id):
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

    return render(request, 'inventario/editar_material.html', {'material': material, 'unidades': unidades})


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
    unidades = UnidadOrganizacional.objects.all().order_by('nombre')
    datos_consumo = []

    for unidad in unidades:
        movimientos_unidad = MovimientoInventario.objects.filter(
            unidad_destino=unidad,
            tipo='SALIDA'
        )
        total_items = movimientos_unidad.aggregate(Sum('cantidad'))['cantidad__sum'] or 0
        total_monto = movimientos_unidad.aggregate(Sum('costo_total'))['costo_total__sum'] or Decimal('0.00')

        if total_items > 0:
            datos_consumo.append({
                'unidad': unidad,
                'total_items': total_items,
                'total_monto': total_monto
            })

    return render(request, 'inventario/consumo_unidades.html', {'datos_consumo': datos_consumo})


@login_required
def obtener_items_compra_view(request):
    compra_id = request.GET.get('compra_id')
    if not compra_id:
        return JsonResponse([], safe=False)

    compra = get_object_or_404(CompraMenor, id=compra_id)
    solicitud = compra.solicitud_origen
    if not solicitud:
        return JsonResponse([], safe=False)

    detalles = solicitud.detalles.select_related('material')
    data = []

    for d in detalles:
        if d.material:
            last_ent = MovimientoInventario.objects.filter(material=d.material, tipo='ENTRADA').order_by('-fecha').first()
            costo_u = last_ent.costo_unitario if last_ent else Decimal('0.00')
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
        transferencias = Transferencia.objects.select_related('origen', 'destino', 'usuario_envia', 'usuario_recibe').prefetch_related('detalles__material').all()
    else:
        almacenes_user = perfil.almacenes_autorizados.all()
        transferencias = Transferencia.objects.filter(
            Q(origen__in=almacenes_user) | Q(destino__in=almacenes_user)
        ).select_related('origen', 'destino', 'usuario_envia', 'usuario_recibe').prefetch_related('detalles__material')

    transferencias = transferencias.order_by('-id')
    paginator = Paginator(transferencias, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # Almacenes donde ESTE usuario puede CONFIRMAR la recepción (presentación del botón).
    # La invariancia se vuelve a validar en recibir_transferencia/el servicio.
    if rol in ['ALMACENERO', 'ADMINISTRADOR', 'ADMIN_ALMACENES'] and perfil is not None:
        almacenes_recepcion_ids = set(perfil.get_almacenes_operables().values_list('id', flat=True))
    else:
        almacenes_recepcion_ids = set()

    return render(request, 'inventario/transferencia_list.html', {
        'page_obj': page_obj,
        'almacenes_recepcion_ids': almacenes_recepcion_ids,
    })


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
    # La recepción es una operación destructiva: SOLO vía POST (nunca GET).
    if request.method != 'POST':
        messages.warning(request, "La recepción de transferencias debe confirmarse mediante el botón correspondiente.")
        return redirect('transferencia_list')

    perfil = getattr(request.user, 'perfilusuario', None)

    transferencia = Transferencia.objects.select_related('destino').filter(id=id).first()
    if transferencia is None:
        messages.error(request, "La transferencia solicitada no existe.")
        return redirect('transferencia_list')

    # Fast-fail amigable: una transferencia fuera de tránsito jamás vuelve a procesarse.
    if transferencia.estado != 'EN_TRANSITO':
        messages.warning(
            request,
            f"La transferencia {transferencia.nro_transferencia} ya fue procesada "
            f"(estado: {transferencia.get_estado_display()})."
        )
        return redirect('transferencia_list')

    # Invariancia de la recepción: el destino debe ser un almacén que el usuario
    # puede operar (impide POST manipulados hacia almacenes ajenos al subalmacén
    # autorizado). El servicio revalida dentro de la transacción.
    if not perfil or not perfil.tiene_acceso_almacen(transferencia.destino):
        messages.error(
            request,
            f"No tiene autorización para recibir inventario en el almacén: {transferencia.destino.nombre}"
        )
        return redirect('transferencia_list')

    try:
        confirmar_recepcion_transferencia(transferencia.id, request.user)
        messages.success(
            request,
            f"Transferencia {transferencia.nro_transferencia} recibida y consolidada en stock con éxito."
        )
    except Exception as e:
        # El bloque del servicio es atómico: si falló, no quedó stock/lote/movimiento parcial.
        messages.error(request, f"Error al procesar la recepción física de la transferencia: {str(e)}")

    return redirect('transferencia_list')


@login_required
@rol_requerido(['ALMACENERO', 'KARDISTA', 'ADMINISTRADOR', 'ADMIN_ALMACENES'])
def nota_recepcion_pdf(request, id):
    nota = get_object_or_404(
        NotaIngreso.objects.prefetch_related('detalles__material__partida').select_related('proveedor', 'usuario'),
        id=id
    )

    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="nota_recepcion_{nota.nro_nota}.pdf"'

    pdf = canvas.Canvas(response, pagesize=letter)
    width, height = letter
    pdf.setTitle(f"Nota de Recepción Nro: {nota.nro_nota}")

    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(50, height - 40, "GOBIERNO AUTÓNOMO DEPARTAMENTAL DE POTOSÍ")
    pdf.drawString(50, height - 52, "SECRETARÍA DPTAL. ADMINISTRATIVA Y FINANCIERA")
    pdf.drawString(50, height - 64, "UNIDAD DE ALMACENES")

    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawRightString(width - 50, height - 50, f"Nº. {nota.nro_nota.replace('NI-', '')}")
    pdf.drawString(50, height - 90, "NOTA DE RECEPCIÓN")

    factura_val = nota.factura if nota.factura else '—'
    c31_val = nota.c31 if nota.c31 else '—'
    fecha_factura_val = nota.fecha.strftime('%Y-%m-%d') if nota.fecha else '—'
    fecha_ingreso_val = nota.fecha_registro.strftime('%Y-%m-%d') if nota.fecha_registro else '—'
    orden_val = nota.compra_menor_origen.nro_orden if nota.compra_menor_origen else '—'

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
            str(det.cantidad),
            str(det.cantidad),
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
        ('ALIGN', (1,1), (1, last_row), 'LEFT'),
        ('ALIGN', (5,1), (6, last_row), 'RIGHT'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 8),
        ('GRID', (0,0), (-1, last_row - 1), 0.5, colors.HexColor('#D1D5DB')),
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#F3F4F6')),
        ('FONTNAME', (1, last_row), (1, last_row), 'Helvetica-Bold'),
        ('FONTNAME', (6, last_row), (6, last_row), 'Helvetica-Bold'),
        ('LINEABOVE', (6, last_row), (6, last_row), 1, colors.black),
    ])
    t_det.setStyle(t_style)

    w_act, h_act = t_det.wrapOn(pdf, width - 100, height - 250)
    pdf_y = height - 190 - h_act
    t_det.drawOn(pdf, 50, pdf_y)

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
@rol_requerido(['ALMACENERO', 'KARDISTA', 'ADMINISTRADOR', 'ADMIN_ALMACENES'])
def kardex_fisico_pdf(request, id):
    material = get_object_or_404(Material, id=id)
    movimientos_db = MovimientoInventario.objects.filter(material=material).order_by('fecha')

    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="kardex_fisico_{material.codigo}.pdf"'

    pdf = canvas.Canvas(response, pagesize=letter)
    width, height = letter
    pdf.setTitle(f"Kardex Físico - {material.codigo}")

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