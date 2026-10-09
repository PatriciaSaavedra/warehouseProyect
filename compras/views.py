from decimal import Decimal
from auditoria.models import Bitacora
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.core.paginator import Paginator
from django.utils import timezone

from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from inventario.models import PartidaPresupuestaria, Proveedor
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle
from solicitudes.models import Solicitud
from usuarios.decorators import rol_requerido
from django.utils import html
import json
from .models import ActaConformidad, CompraMenor
from compras.models import (
    ProcesoAdquisicion, ProcesoChecklistDetalle, ProcesoItemDetalle,
    RequisitoCatalogo, AutoridadInstitucional
)
from inventario.models import Material, UnidadMedida, PartidaPresupuestaria
from .models import CompraMenor, DetalleCompraMenor
# ========================================================
# 1. GESTIÓN DE ÓRDENES DE COMPRA Y SERVICIO
# ========================================================

@login_required
@rol_requerido(['BIENES_SERVICIOS', 'ADMINISTRADOR'])
def compras_list(request):
  """Bandeja de Bienes y Servicios: Monitorea Órdenes de Compra y Órdenes de"""
  """ Servicio."""
  filtro_tipo = request.GET.get('tipo', '').strip()
  query = request.GET.get('q', '').strip()

  compras = CompraMenor.objects.select_related(
      'proveedor',
      'partida',
      'solicitud_origen',
      'solicitud_origen__unidad_solicitante',
  ).all()

  if filtro_tipo:
    compras = compras.filter(tipo_orden=filtro_tipo)
  if query:
    compras = compras.filter(
        Q(nro_orden__icontains=query)
        | Q(proveedor__razon_social__icontains=query)
        | Q(proveedor__nit__icontains=query)
    )

  compras = compras.order_by('-id')

  return render(
      request,
      'compras/compras_list.html',
      {'compras': compras, 'filtro_tipo': filtro_tipo, 'query': query},
  )


@login_required
@rol_requerido(['BIENES_SERVICIOS', 'ADMINISTRADOR'])
def crear_compra_menor(request):
    proveedores = Proveedor.objects.all().order_by('razon_social')
    partidas = PartidaPresupuestaria.objects.all().order_by('codigo')
    materiales_catalogo = Material.objects.filter(is_active=True).select_related('unidad_medida_fk', 'partida').order_by('nombre')
    
    solicitud_id = request.GET.get('solicitud')
    solicitud_origen = None
    partida_defecto = None
    tipo_orden_sugerido = 'COMPRA'
    monto_estimado = Decimal('0.00')

    if solicitud_id:
        solicitud_origen = get_object_or_404(Solicitud, id=solicitud_id)
        tipo_orden_sugerido = 'SERVICIO' if solicitud_origen.tipo_requerimiento == 'SERVICIO' else 'COMPRA'
        primer_detalle = solicitud_origen.detalles.first()
        if primer_detalle:
            partida_defecto = primer_detalle.partida_afectada

        for detalle in solicitud_origen.detalles.all():
            cant = detalle.cantidad_aprobada if detalle.cantidad_aprobada is not None else detalle.cantidad_solicitada
            monto_estimado += Decimal(cant) * detalle.precio_unitario_referencial

    if request.method == 'POST':
        tipo_orden = request.POST.get('tipo_orden', tipo_orden_sugerido)
        proveedor_id = request.POST.get('proveedor')
        partida_id = request.POST.get('partida')
        gestion = int(request.POST.get('gestion', 2026))
        payload_raw = request.POST.get('payload_items', '[]')

        payload_raw = html.unescape(payload_raw) if hasattr(html, 'unescape') else payload_raw
        try:
            items_data = json.loads(payload_raw)
        except Exception:
            items_data = []

        if not proveedor_id or not partida_id:
            messages.error(request, 'El proveedor y la partida presupuestaria son obligatorios.')
            return redirect('crear_compra_menor')

        if not items_data:
            messages.error(request, 'Debe agregar al menos un artículo o servicio a la orden.')
            return redirect('crear_compra_menor')

        proveedor = get_object_or_404(Proveedor, id=proveedor_id)
        partida = get_object_or_404(PartidaPresupuestaria, id=partida_id)

        # Calcular el monto total sumando los ítems reales
        monto_total = Decimal('0.00')
        for it in items_data:
            c = int(it.get('cantidad', 1))
            p = Decimal(str(it.get('precio_unitario', '0.00')))
            monto_total += Decimal(c) * p

        if monto_total <= 0:
            messages.error(request, 'El monto total debe ser mayor a cero.')
            return redirect('crear_compra_menor')

        if monto_total > 50000:
            messages.error(request, f'El monto total (Bs. {monto_total:.2f}) excede el límite legal de 50,000.00 Bs. para Contratación Menor (RPA).')
            return redirect('crear_compra_menor')

        total_ordenes = CompraMenor.objects.filter(gestion=gestion, tipo_orden=tipo_orden).count() + 1
        nro_orden = f"BB.SS. N° {str(total_ordenes).zfill(3)}/{gestion}"

        try:
            with transaction.atomic():
                compra = CompraMenor.objects.create(
                    tipo_orden=tipo_orden,
                    nro_orden=nro_orden,
                    proveedor=proveedor,
                    partida=partida,
                    solicitud_origen=solicitud_origen,
                    monto_total=monto_total,
                    gestion=gestion,
                    estado='EMITIDA'
                )

                # Guardar cada ítem en DetalleCompraMenor
                for it in items_data:
                    mat_id = it.get('material_id')
                    mat_obj = Material.objects.filter(id=mat_id).first() if mat_id else None
                    c = int(it.get('cantidad', 1))
                    p = Decimal(str(it.get('precio_unitario', '0.00')))

                    DetalleCompraMenor.objects.create(
                        compra=compra,
                        material=mat_obj,
                        descripcion=it.get('descripcion', '').upper(),
                        unidad_medida=it.get('unidad', 'Pza').upper(),
                        cantidad=c,
                        precio_unitario=p,
                        subtotal=Decimal(c) * p
                    )

                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Compras',
                    accion=f'Emitir {compra.get_tipo_orden_display()}',
                    descripcion=f'Se emitió la orden {nro_orden} con {len(items_data)} ítems por un total de Bs. {monto_total:.2f}.'
                )

            messages.success(request, f'{compra.get_tipo_orden_display()} {nro_orden} generada con {len(items_data)} ítems por Bs. {monto_total:.2f}.')
            return redirect('compras_list')

        except Exception as e:
            messages.error(request, f'Error al registrar la orden: {str(e)}')
            return redirect('crear_compra_menor')

    return render(request, 'compras/crear_compra_menor.html', {
        'proveedores': proveedores,
        'partidas': partidas,
        'materiales_catalogo': materiales_catalogo,  # <-- Enviamos el catálogo al template
        'solicitud_origen': solicitud_origen,
        'partida_defecto': partida_defecto,
        'tipo_orden_sugerido': tipo_orden_sugerido,
        'monto_estimado': monto_estimado,
        'gestion_default': 2026
    })
@login_required
@rol_requerido(['BIENES_SERVICIOS', 'ADMINISTRADOR'])
def detalle_compra(request, id):
  """Muestra la consola detallada de la Orden de Compra/Servicio."""
  compra = get_object_or_404(
      CompraMenor.objects.select_related(
          'proveedor', 'partida', 'solicitud_origen'
      ),
      id=id,
  )
  detalles_solicitud = []

  if compra.solicitud_origen:
    detalles_solicitud = compra.solicitud_origen.detalles.select_related(
        'material'
    )

  return render(
      request,
      'compras/detalle_compra.html',
      {'compra': compra, 'detalles': detalles_solicitud},
  )


# ========================================================
# 2. DIRECTORIO Y ADMINISTRACIÓN DE PROVEEDORES
# ========================================================

@login_required
@rol_requerido(['ADMINISTRADOR', 'ADMIN_ALMACENES', 'BIENES_SERVICIOS'])
def proveedores_list(request):
    """Listado y búsqueda de proveedores del Estado."""
    query = request.GET.get('q', '').strip()
    proveedores = Proveedor.objects.all()

    if query:
        proveedores = proveedores.filter(
            Q(razon_social__icontains=query) |
            Q(nit__icontains=query) |
            Q(telefono__icontains=query)
        )

    proveedores = proveedores.order_by('razon_social')
    paginator = Paginator(proveedores, 10)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'compras/proveedores_list.html', {
        'page_obj': page_obj,
        'query': query,
    })

@login_required
@rol_requerido(['BIENES_SERVICIOS', 'ADMINISTRADOR'])
def crear_proveedor(request):
  """Registra un proveedor en el catálogo de adquisiciones."""
  if request.method == 'POST':
    nit = request.POST.get('nit', '').strip()
    razon_social = request.POST.get('razon_social', '').strip()
    telefono = request.POST.get('telefono', '').strip()
    direccion = request.POST.get('direccion', '').strip()

    if not nit or not razon_social:
      messages.error(request, 'El NIT y la Razón Social son campos obligatorios.')
      return redirect('crear_proveedor')

    if Proveedor.objects.filter(nit=nit).exists():
      messages.error(
          request, 'Ya existe un proveedor registrado con este NIT.'
      )
      return redirect('crear_proveedor')

    try:
      with transaction.atomic():
        Proveedor.objects.create(
            nit=nit,
            razon_social=razon_social,
            telefono=telefono if telefono else None,
            direccion=direccion if direccion else None,
        )
        Bitacora.objects.create(
            usuario=request.user,
            modulo='Compras',
            accion='Registrar Proveedor',
            descripcion=(
                f'Bienes y Servicios registró al proveedor: {razon_social} (NIT:'
                f' {nit})'
            ),
        )

      messages.success(request, 'Proveedor registrado correctamente.')
      return redirect('proveedores_list')

    except Exception as e:
      messages.error(request, f'Error al registrar el proveedor: {str(e)}')
      return redirect('crear_proveedor')

  return render(request, 'compras/crear_proveedor.html')


@login_required
@rol_requerido(['ADMINISTRADOR', 'ADMIN_ALMACENES', 'BIENES_SERVICIOS'])
def crear_proveedor(request):
    """Crea un nuevo proveedor desde el formulario o vía AJAX modal."""
    if request.method == 'POST':
        nit = request.POST.get('nit', '').strip()
        razon_social = request.POST.get('razon_social', '').strip().upper()
        telefono = request.POST.get('telefono', '').strip()
        direccion = request.POST.get('direccion', '').strip()

        if not nit or not razon_social:
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({'ok': False, 'error': 'NIT y Razón Social son obligatorios.'}, status=400)
            messages.error(request, "NIT y Razón Social son campos requeridos.")
            return redirect('proveedores_list')

        if Proveedor.objects.filter(nit=nit).exists():
            if request.headers.get('x-requested-with') == 'XMLHttpRequest':
                return JsonResponse({'ok': False, 'error': f'Ya existe un proveedor con el NIT {nit}.'}, status=400)
            messages.error(request, f"Ya existe un proveedor registrado con el NIT {nit}.")
            return redirect('proveedores_list')

        proveedor = Proveedor.objects.create(
            nit=nit,
            razon_social=razon_social,
            telefono=telefono,
            direccion=direccion
        )

        Bitacora.objects.create(
            usuario=request.user,
            modulo='Compras',
            accion='Registrar Proveedor',
            descripcion=f'Se registró al proveedor {razon_social} (NIT: {nit}).'
        )

        # Respuesta rápida si se llamó desde el modal en plena adjudicación
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({
                'ok': True,
                'id': proveedor.id,
                'razon_social': proveedor.razon_social,
                'nit': proveedor.nit
            })

        messages.success(request, f"Proveedor '{razon_social}' registrado correctamente.")
        return redirect('proveedores_list')

    return redirect('proveedores_list')


@login_required
@rol_requerido(['ADMINISTRADOR', 'ADMIN_ALMACENES', 'BIENES_SERVICIOS'])
def editar_proveedor(request, id):
    """Actualiza datos del proveedor."""
    proveedor = get_object_or_404(Proveedor, id=id)

    if request.method == 'POST':
        nit = request.POST.get('nit', '').strip()
        razon_social = request.POST.get('razon_social', '').strip().upper()
        telefono = request.POST.get('telefono', '').strip()
        direccion = request.POST.get('direccion', '').strip()

        if Proveedor.objects.filter(nit=nit).exclude(id=proveedor.id).exists():
            messages.error(request, f"El NIT {nit} ya pertenece a otro proveedor registrado.")
            return redirect('proveedores_list')

        proveedor.nit = nit
        proveedor.razon_social = razon_social
        proveedor.telefono = telefono
        proveedor.direccion = direccion
        proveedor.save()

        messages.success(request, f"Proveedor '{razon_social}' actualizado exitosamente.")

    return redirect('proveedores_list')
# ========================================================
# 3. GENERADOR DE ÓRDENES PDF (COMPRA / SERVICIO)
# ========================================================

def numero_a_letras(numero):
  """Convierte importes numéricos a texto para la glosa legal 'SON:"""
  """ ... BOLIVIANOS'."""
  unidades = [
      '',
      'un',
      'dos',
      'tres',
      'cuatro',
      'cinco',
      'seis',
      'siete',
      'ocho',
      'nueve',
  ]
  decenas = [
      '',
      'diez',
      'veinte',
      'treinta',
      'cuarenta',
      'cincuenta',
      'sesenta',
      'setenta',
      'ochenta',
      'noventa',
  ]
  especiales = {
      11: 'once',
      12: 'doce',
      13: 'trece',
      14: 'catorce',
      15: 'quince',
      16: 'dieciséis',
      17: 'diecisiete',
      18: 'dieciocho',
      19: 'diecinueve',
  }
  centenas = [
      '',
      'cien',
      'doscientos',
      'trescientos',
      'cuatrocientos',
      'quinientos',
      'seiscientos',
      'setecientos',
      'ochocientos',
      'novecientos',
  ]

  if numero == 0:
    return 'Cero 00/100 Bolivianos'

  entero = int(numero)
  decimal = int(round((numero - entero) * 100))

  def convertir_grupo(n):
    if n < 10:
      return unidades[n]
    elif n in especiales:
      return especiales[n]
    elif n < 100:
      u = n % 10
      d = n // 10
      if u == 0:
        return decenas[d]
      return f'{decenas[d]} y {unidades[u]}'
    elif n < 1000:
      d_u = n % 100
      c = n // 100
      if n == 100:
        return 'cien'
      elif c == 1:
        return f'ciento {convertir_grupo(d_u)}'
      if d_u == 0:
        return centenas[c]
      return f'{centenas[c]} {convertir_grupo(d_u)}'
    return ''

  partes = []
  if entero >= 1000:
    miles = entero // 1000
    resto = entero % 1000
    if miles == 1:
      partes.append('mil')
    else:
      partes.append(f'{convertir_grupo(miles)} mil')
    if resto > 0:
      partes.append(convertir_grupo(resto))
  else:
    partes.append(convertir_grupo(entero))

  letras = ' '.join(p for p in partes if p).strip().capitalize()
  return f'{letras} {decimal:02d}/100 Bolivianos'


@login_required
@rol_requerido(['BIENES_SERVICIOS', 'ADMINISTRADOR'])
def compra_pdf(request, id):
    compra = get_object_or_404(
        CompraMenor.objects.select_related(
            'proveedor', 'partida', 'solicitud_origen', 'solicitud_origen__unidad_solicitante'
        ),
        id=id
    )

    es_servicio = (compra.tipo_orden == 'SERVICIO')
    titulo_doc = "ORDEN DE SERVICIO" if es_servicio else "ORDEN DE COMPRA"

    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="{titulo_doc.lower().replace(" ", "_")}_{compra.id}.pdf"'

    pdf = canvas.Canvas(response, pagesize=letter)
    width, height = letter
    pdf.setTitle(f"{titulo_doc} - {compra.nro_orden}")

    # Membrete Oficial Departamental
    pdf.setFont("Helvetica-Bold", 8)
    pdf.drawCentredString(width / 2.0, height - 35, "ESTADO PLURINACIONAL DE BOLIVIA")
    pdf.drawCentredString(width / 2.0, height - 46, "GOBIERNO AUTÓNOMO DEPARTAMENTAL DE POTOSÍ")
    pdf.setFont("Helvetica", 7.5)
    
    unidad_sol_nombre = compra.solicitud_origen.unidad_solicitante.nombre if compra.solicitud_origen else "UNIDAD ADMINISTRATIVA"
    pdf.drawCentredString(width / 2.0, height - 57, unidad_sol_nombre.upper())

    # Título Principal
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawCentredString(width / 2.0, height - 80, f"{titulo_doc}")
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawCentredString(width / 2.0, height - 95, f"{compra.nro_orden}")

    # Cuadro Sello RPA Recibido (Esquina superior derecha)
    pdf.setStrokeColor(colors.HexColor('#1E3A8A'))
    pdf.rect(width - 135, height - 85, 85, 35, stroke=1, fill=0)
    pdf.setFont("Helvetica-Bold", 7)
    pdf.setFillColor(colors.HexColor('#1E3A8A'))
    pdf.drawString(width - 130, height - 60, "R. P. A.")
    pdf.setFont("Helvetica", 6)
    pdf.drawString(width - 130, height - 70, f"Fecha: {compra.fecha_registro.strftime('%d-%m-%Y')}")
    pdf.drawString(width - 130, height - 80, "RECIBIDO")
    pdf.setFillColor(colors.black)
    pdf.setStrokeColor(colors.black)

    # Metadatos del Documento
    pdf.setFont("Helvetica-Bold", 8)
    pdf.drawString(50, height - 120, "Fecha:")
    pdf.drawString(50, height - 136, "Unidad Solicitante:")
    pdf.drawString(50, height - 152, "A la orden de:")
    pdf.drawString(50, height - 168, "Dirección:")

    pdf.setFont("Helvetica", 8)
    fecha_literal = compra.fecha_registro.strftime('%B, %d DE %Y').upper()
    pdf.drawString(145, height - 120, f"{fecha_literal}")
    pdf.drawString(145, height - 136, unidad_sol_nombre.upper())
    pdf.drawString(145, height - 152, compra.proveedor.razon_social.upper())
    pdf.drawString(145, height - 168, (compra.proveedor.direccion or "Zona Central").upper())

    # NIT del Proveedor (Lado derecho)
    pdf.setFont("Helvetica-Bold", 8)
    pdf.drawString(410, height - 152, "NIT/C.I.:")
    pdf.setFont("Helvetica", 8)
    pdf.drawString(455, height - 152, compra.proveedor.nit)

    # Párrafo formal
    pdf.setFont("Helvetica-Oblique", 7.5)
    texto_intro = "Agradeceremos a ustedes tengan la gentileza de atender con la presente orden de servicio:" if es_servicio else "Agradeceremos a ustedes tengan la gentileza de atender con la presente orden de compra:"
    pdf.drawString(50, height - 190, texto_intro)

    # Tabla de Ítems Contratados
    headers = ['Item', 'Descripción', 'Unidad', 'Cantidad', 'Precio Unit.', 'Total']
    data = [headers]

    detalles_compra = compra.detalles.all()
    if detalles_compra.exists():
        for idx, d in enumerate(detalles_compra, start=1):
            data.append([
                str(idx),
                d.descripcion[:60],
                d.unidad_medida.upper()[:8],
                str(d.cantidad),
                f"{d.precio_unitario:.2f}",
                f"{d.subtotal:.2f}"
            ])
    elif compra.solicitud_origen:
        for idx, d in enumerate(compra.solicitud_origen.detalles.all(), start=1):
            nom = d.material.nombre if d.material else (d.descripcion_material_no_catalogado or "CONCEPTO")
            u_med = d.material.unidad_medida_fk.codigo if (d.material and d.material.unidad_medida_fk) else ("PAX" if es_servicio else "Pza")
            cant = d.cantidad_aprobada if d.cantidad_aprobada is not None else d.cantidad_solicitada
            p_unit = d.precio_unitario_referencial
            subt = Decimal(cant) * p_unit
            data.append([str(idx), nom.upper()[:60], u_med.upper(), str(cant), f"{p_unit:.2f}", f"{subt:.2f}"])
    else:
        data.append(["1", "PRESTACIÓN DE SERVICIO" if es_servicio else "ADQUISICIÓN DIRECTA", "GLB", "1", f"{compra.monto_total:.2f}", f"{compra.monto_total:.2f}"])

    data.append(["", "", "", "", "TOTAL Bs.", f"{compra.monto_total:.2f}"])

    col_widths = [30, 230, 50, 45, 75, 80]
    t = Table(data, colWidths=col_widths)
    last_row = len(data) - 1

    t.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('ALIGN', (1, 1), (1, -1), 'LEFT'),
        ('ALIGN', (4, 1), (-1, -1), 'RIGHT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 7.5),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#9CA3AF')),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F3F4F6')),
        ('FONTNAME', (4, last_row), (5, last_row), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
    ]))

    w_act, h_act = t.wrapOn(pdf, 510, height - 250)
    pdf_y = height - 205 - h_act
    t.drawOn(pdf, 50, pdf_y)

    # Total en Letras
    text_y = pdf_y - 20
    pdf.setFont("Helvetica-Bold", 8)
    pdf.drawString(50, text_y, f"SON: {numero_a_letras(compra.monto_total).upper()}")

    # --- NOTAS AL PIE DIFERENCIADAS SEGÚN EL TIPO DE ORDEN ---
    nota_y = text_y - 35
    pdf.setFont("Helvetica", 6.5)

    if es_servicio:
        # Glosa idéntica a tu foto de la Orden de Servicio N° 026
        justif = compra.solicitud_origen.justificacion if compra.solicitud_origen else "las actividades institucionales programadas"
        pdf.drawString(50, nota_y, f"Nota.- El servicio está destinado para: {justif.upper()[:100]}.")
        pdf.drawString(50, nota_y - 10, "El servicio es inmediato según coordinación directa con la Unidad Solicitante.")
        pdf.drawString(50, nota_y - 20, "(D.S. 0181; Art. 5 inc. cc). Aplicable a contratación de servicios con plazo de prestación no mayor a 15 días según RE-SABS Art. 12.")
    else:
        # Glosa idéntica a tu foto de la Orden de Compra N° 081 (Cristian Motors)
        pdf.drawString(50, nota_y, "Nota: Tomar en cuenta las características y especificaciones de lo cotizado para la Gobernación.")
        pdf.drawString(50, nota_y - 10, "La entrega debe realizarse en los almacenes del G.A.D.P. El tiempo de entrega según cotización es de 5 días.")
        pdf.drawString(50, nota_y - 20, "(D.S. 0181; Art. 5 inc. cc). Aplicable a adquisición de bienes con plazo de entrega no mayor a 15 días según RE-SABS Art. 12.")

    # Sello grande AUTORIZADO
    pdf.setFont("Helvetica-Bold", 16)
    pdf.setFillColor(colors.HexColor('#1D4ED8'))
    pdf.drawString(180, 110, "A U T O R I Z A D O")
    pdf.setFillColor(colors.black)

    # --- FIRMAS OFICIALES DIFERENCIADAS ---
    y_firmas = 60
    pdf.setFont("Helvetica", 6.5)

    if es_servicio:
        # FIRMAS SEGÚN TU FOTO DE ORDEN DE SERVICIO (SEDEDE / UNIDAD):
        # 1. Administrador de la Unidad
        pdf.drawString(50, y_firmas, "_________________________")
        pdf.drawString(50, y_firmas - 8, f"ADMINISTRADOR(A)")
        pdf.drawString(50, y_firmas - 16, f"{unidad_sol_nombre[:25].upper()}")

        # 2. Director / Secretario de la Unidad
        pdf.drawString(180, y_firmas, "_________________________")
        pdf.drawString(180, y_firmas - 8, "DIRECTOR / SECRETARIO")
        pdf.drawString(180, y_firmas - 16, f"{unidad_sol_nombre[:25].upper()}")

        # 3. Proveedor (Hostal / Empresa)
        pdf.drawString(320, y_firmas, "_________________________")
        pdf.drawString(320, y_firmas - 8, "FIRMA Y SELLO PROVEEDOR")
        pdf.drawString(320, y_firmas - 16, f"{compra.proveedor.razon_social[:25].upper()}")

        # 4. RPA
        pdf.drawString(460, y_firmas, "_________________________")
        pdf.drawString(460, y_firmas - 8, "RPA CONTRATACIÓN MENOR")
        pdf.drawString(460, y_firmas - 16, "GOBIERNO AUTÓNOMO DPTAL. POTOSÍ")

    else:
        # FIRMAS SEGÚN TU FOTO DE ORDEN DE COMPRA (CRISTIAN MOTORS):
        pdf.drawString(50, y_firmas, "_________________________")
        pdf.drawString(50, y_firmas - 8, "ANALISTA BB.SS.")

        pdf.drawString(180, y_firmas, "_________________________")
        pdf.drawString(180, y_firmas - 8, "RESPONSABLE DE BB.SS.")

        pdf.drawString(320, y_firmas, "_________________________")
        pdf.drawString(320, y_firmas - 8, "JEFE UNIDAD ADMINISTRATIVA")

        pdf.drawString(460, y_firmas, "_________________________")
        pdf.drawString(460, y_firmas - 8, "RPA CONTRATACIÓN MENOR")

    pdf.save()
    return response

@login_required
def adquisiciones_list(request):
    """
    Bandeja de Procesos de Adquisición (Flujo 2).
    Permite acceso institucional a RPA, SAF, Presupuestos, Bienes y Servicios y Almacenes.
    """
    perfil = getattr(request.user, 'perfilusuario', None)
    rol = perfil.rol if perfil else 'UNIDAD_SOLICITANTE'
    unidad = perfil.unidad if perfil else None

    # Roles institucionales globales que ven todo el flujo de la Gobernación
    roles_globales = [
        'ADMINISTRADOR', 'ADMIN_ALMACENES', 'RPA', 'SECRETARIO_SAF', 
        'PRESUPUESTOS', 'BIENES_SERVICIOS', 'ALMACENERO', 'KARDISTA'
    ]

    if rol in roles_globales:
        procesos_qs = ProcesoAdquisicion.objects.all()
    else:
        procesos_qs = ProcesoAdquisicion.objects.filter(unidad_solicitante=unidad)

    query = request.GET.get('q', '').strip()
    filtro_tipo = request.GET.get('tipo', '').strip()
    filtro_estado = request.GET.get('estado', '').strip()

    if query:
        procesos_qs = procesos_qs.filter(
            Q(codigo__icontains=query) |
            Q(objeto_contratacion__icontains=query) |
            Q(cite_solicitud__icontains=query) |
            Q(unidad_solicitante__nombre__icontains=query)
        )
    if filtro_tipo:
        procesos_qs = procesos_qs.filter(tipo=filtro_tipo)
    if filtro_estado:
        procesos_qs = procesos_qs.filter(estado=filtro_estado)

    procesos_qs = procesos_qs.select_related('unidad_solicitante', 'solicitante').order_by('-id')

    # Métricas útiles para el RPA o revisores
    pendientes_rpa = ProcesoAdquisicion.objects.filter(estado='CERTIFICADA').count()

    paginator = Paginator(procesos_qs, 10)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'compras/adquisiciones_list.html', {
        'page_obj': page_obj,
        'query': query,
        'filtro_tipo': filtro_tipo,
        'filtro_estado': filtro_estado,
        'pendientes_rpa': pendientes_rpa,
        'estados': ProcesoAdquisicion.ESTADOS_CHOICES,
        'rol': rol,
    })

@login_required
def crear_adquisicion(request):
    """
    Formulario de Inicio del Flujo 2:
    - Selección Bien o Servicio.
    - Datos generales y CITE de la solicitud.
    - Carga de Ítems (desde catálogo o descripción libre/TDR).
    - Checklist Dinámico de Requisitos Documentales por Ítem.
    """
    perfil = getattr(request.user, 'perfilusuario', None)
    unidad = perfil.unidad if perfil else None

    if not unidad:
        messages.error(request, "Su usuario no tiene una Unidad Organizacional asignada para tramitar adquisiciones.")
        return redirect('adquisiciones_list')

    # Autoridades vigentes automáticas
    autoridad_saf = AutoridadInstitucional.objects.filter(cargo='SAF', is_active=True).first()
    autoridad_rpa = AutoridadInstitucional.objects.filter(cargo='RPA', is_active=True).first()

    # Requisitos activos del catálogo
    requisitos_db = RequisitoCatalogo.objects.filter(is_active=True).order_by('id')
    materiales_catalogo = Material.objects.filter(is_active=True).values('id', 'codigo', 'nombre', 'unidad_medida')
    unidades_medida = UnidadMedida.objects.filter(is_active=True).order_by('nombre')

    if request.method == 'POST':
        tipo = request.POST.get('tipo', 'BIEN')
        objeto = request.POST.get('objeto_contratacion', '').strip()
        justificacion = request.POST.get('justificacion', '').strip()
        cite_solicitud = request.POST.get('cite_solicitud', '').strip()
        payload_items_raw = request.POST.get('payload_items', '{}')

        if not objeto or not justificacion:
            messages.error(request, "El Objeto de Contratación y la Justificación son campos obligatorios.")
            return redirect('crear_adquisicion')

        try:
            payload_items = json.loads(payload_items_raw)
        except json.JSONDecodeError:
            payload_items = {}

        if not payload_items:
            messages.error(request, "Debe agregar al menos un bien o servicio a la solicitud de adquisición.")
            return redirect('crear_adquisicion')

        try:
            with transaction.atomic():
                # Correlativo único (ej: ADQ-2026-0001)
                anio = timezone.now().year
                ultimo = ProcesoAdquisicion.objects.select_for_update().order_by('id').last()
                numero = (ultimo.id + 1) if ultimo else 1
                codigo = f"ADQ-{anio}-{numero:04d}"

                # 1. Crear Cabecera del Proceso
                proceso = ProcesoAdquisicion.objects.create(
                    codigo=codigo,
                    tipo=tipo,
                    unidad_solicitante=unidad,
                    solicitante=request.user,
                    objeto_contratacion=objeto,
                    justificacion=justificacion,
                    cite_solicitud=cite_solicitud,
                    autoridad_saf=autoridad_saf,
                    autoridad_rpa=autoridad_rpa,
                    # Categoría Programática
                    programa=request.POST.get('programa', '000').strip(),
                    proyecto_actividad=request.POST.get('proyecto_actividad', '001').strip(),
                    fuente_financiamiento=request.POST.get('fuente_financiamiento', '20').strip(),
                    organismo_financiador=request.POST.get('organismo_financiador', '220').strip(),
                    estado='CHECKLIST_VERIFICADO'
                )

                # 2. Guardar Ítems Demandados y su Checklist Individual
                total_estimado = Decimal('0.00')
                for _, item_data in payload_items.items():
                    cant = int(item_data.get('cantidad', 1))
                    precio_ref = Decimal(str(item_data.get('precio_unitario', '0.00')))
                    mat_id = item_data.get('material_id')

                    subtot = Decimal(cant) * precio_ref
                    total_estimado += subtot

                    # Crear ítem
                    item_obj = ProcesoItemDetalle.objects.create(
                        proceso=proceso,
                        material_id=mat_id if mat_id else None,
                        descripcion=item_data.get('descripcion', '').strip(),
                        unidad_medida=item_data.get('unidad_medida', 'PIEZA').strip(),
                        cantidad=cant,
                        precio_referencial_estimado=precio_ref,
                        precio_oficial_unitario=Decimal('0.00')
                    )

                    # Guardar Checklist exclusivo de ESTE ítem
                    checklist_dict = item_data.get('checklist', {})
                    for req_id_str, resp in checklist_dict.items():
                        req_id = int(req_id_str)
                        ProcesoChecklistDetalle.objects.create(
                            proceso=proceso,
                            item_adquisicion=item_obj,
                            requisito_id=req_id,
                            estado=resp.get('estado', 'CUMPLE'),
                            documento_respaldo=resp.get('doc_respaldo', '').strip(),
                            observaciones=resp.get('observacion', '').strip()
                        )

                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Compras',
                    accion='Iniciar Proceso Adquisición (Flujo 2)',
                    descripcion=f'Se inició el proceso {codigo} ({tipo}): {objeto[:80]}. Total ref: Bs. {total_estimado:.2f}.'
                )

            messages.success(request, f"Proceso de Adquisición {codigo} registrado con éxito. Checklist verificado.")
            return redirect('detalle_adquisicion', id=proceso.id)

        except Exception as e:
            messages.error(request, f"Error al procesar el requerimiento: {str(e)}")
            return redirect('crear_adquisicion')

    return render(request, 'compras/crear_adquisicion.html', {
        'unidad': unidad,
        'autoridad_saf': autoridad_saf,
        'autoridad_rpa': autoridad_rpa,
        'requisitos': requisitos_db,
        'materiales_catalogo': list(materiales_catalogo),
        'unidades_medida': unidades_medida,
    })
@login_required
def detalle_adquisicion(request, id):
    """
    Ficha de Control del Proceso de Adquisición (Flujo 2).
    """
    proceso = get_object_or_404(
        ProcesoAdquisicion.objects.select_related(
            'unidad_solicitante', 'solicitante', 'autoridad_saf', 'autoridad_rpa', 'proveedor', 'partida'
        ).prefetch_related('items__material', 'checklist_respuestas__requisito'),
        id=id
    )

    perfil = getattr(request.user, 'perfilusuario', None)
    rol = perfil.rol if perfil else 'UNIDAD_SOLICITANTE'

    total_referencial = sum(i.subtotal_referencial for i in proceso.items.all())
    total_oficial = sum(i.subtotal_oficial for i in proceso.items.all())
    proveedores_list = Proveedor.objects.all().order_by('razon_social')

    return render(request, 'compras/detalle_adquisicion.html', {
        'proceso': proceso,
        'total_referencial': total_referencial,
        'total_oficial': total_oficial,
        'proveedores_list': proveedores_list,
        'rol': rol,
    })
# ========================================================
# TRANSICIONES ADMINISTRATIVAS DEL FLUJO 2
# ========================================================

@login_required
def emitir_nota_saf(request, id):
    """Paso 3: Registra el CITE de la nota enviada al Secretario SAF."""
    proceso = get_object_or_404(ProcesoAdquisicion, id=id)
    if request.method == 'POST':
        cite_saf = request.POST.get('cite_nota_saf', '').strip()
        if not cite_saf:
            messages.error(request, "Debe ingresar el CITE oficial de la nota enviada a la SAF.")
            return redirect('detalle_adquisicion', id=proceso.id)

        proceso.cite_nota_saf = cite_saf
        proceso.estado = 'NOTA_SAF_EMITIDA'
        proceso.save()
        messages.success(request, f"Nota formal {cite_saf} registrada y remitida a la SAF.")
    return redirect('detalle_adquisicion', id=proceso.id)


@login_required
@rol_requerido(['PRESUPUESTOS', 'ADMINISTRADOR'])
def certificar_presupuesto_adquisicion(request, id):
    """Paso 4: El área de Presupuestos registra la Certificación Presupuestaria."""
    proceso = get_object_or_404(ProcesoAdquisicion, id=id)
    if request.method == 'POST':
        cert_nro = request.POST.get('certificacion_nro', '').strip()
        fuente = request.POST.get('fuente_financiamiento', '').strip()
        organismo = request.POST.get('organismo_financiador', '').strip()
        monto_str = request.POST.get('monto_certificado_bs', '0').strip()

        if not cert_nro:
            messages.error(request, "El número de Certificación Presupuestaria es obligatorio.")
            return redirect('detalle_adquisicion', id=proceso.id)

        proceso.certificacion_nro = cert_nro
        proceso.fuente_financiamiento = fuente
        proceso.organismo_financiador = organismo
        proceso.monto_certificado_bs = Decimal(monto_str)
        proceso.estado = 'CERTIFICADA'
        proceso.save()
        messages.success(request, f"Certificación Presupuestaria {cert_nro} registrada exitosamente.")
    return redirect('detalle_adquisicion', id=proceso.id)


@login_required
@rol_requerido(['RPA', 'ADMINISTRADOR'])
def aprobar_rpa_adquisicion(request, id):
    """Paso 5: El RPA autoriza la contratación."""
    proceso = get_object_or_404(ProcesoAdquisicion, id=id)
    if request.method == 'POST':
        cite_rpa = request.POST.get('cite_aprobacion_rpa', '').strip()
        proceso.cite_aprobacion_rpa = cite_rpa or "PROVEÍDO RPA"
        proceso.estado = 'APROBADA_RPA'
        proceso.save()
        messages.success(request, f"Proceso {proceso.codigo} aprobado formalmente por el RPA.")
    return redirect('detalle_adquisicion', id=proceso.id)


@login_required
@rol_requerido(['BIENES_SERVICIOS', 'ADMINISTRADOR'])
def emitir_orden_adquisicion(request, id):
    """
    Paso 6: Bienes y Servicios verifica 'Hecho en Bolivia' (D.S. 4505),
    adjudica al Proveedor y fija el Precio Oficial por Ítem.
    """
    proceso = get_object_or_404(ProcesoAdquisicion, id=id)

    if request.method == 'POST':
        proveedor_id = request.POST.get('proveedor')
        nro_orden = request.POST.get('nro_orden_compra', '').strip()
        hecho_bolivia = request.POST.get('hecho_en_bolivia', 'NO_APLICA')
        doc_hb = request.POST.get('documento_hecho_en_bolivia', '').strip()

        if not proveedor_id or not nro_orden:
            messages.error(request, "Debe seleccionar un Proveedor y registrar el N° oficial de Orden de Compra / Contrato.")
            return redirect('detalle_adquisicion', id=proceso.id)

        try:
            with transaction.atomic():
                total_oficial = Decimal('0.00')

                # Guardar el precio oficial adjudicado para cada ítem
                for item in proceso.items.all():
                    precio_str = request.POST.get(f"item_precio_oficial_{item.id}", '0').strip()
                    precio_oficial = Decimal(precio_str) if precio_str else item.precio_referencial_estimado
                    item.precio_oficial_unitario = precio_oficial
                    item.save()
                    total_oficial += item.subtotal_oficial

                proceso.proveedor_id = proveedor_id
                proceso.nro_orden_compra = nro_orden
                proceso.precio_oficial_adjudicado_bs = total_oficial
                proceso.hecho_en_bolivia = hecho_bolivia
                proceso.documento_hecho_en_bolivia = doc_hb
                proceso.estado = 'ORDEN_EMITIDA'
                proceso.save()

                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Compras',
                    accion='Emitir Orden de Compra/Servicio',
                    descripcion=f'Se emitió la Orden {nro_orden} para {proceso.codigo} con precio oficial adjudicado de Bs. {total_oficial:.2f}.'
                )

            messages.success(request, f"Orden de Compra {nro_orden} emitida con éxito por un total oficial de Bs. {total_oficial:.2f}.")
        except Exception as e:
            messages.error(request, f"Error al emitir la orden: {str(e)}")

    return redirect('detalle_adquisicion', id=proceso.id)
@login_required
def doc_orden_compra_pdf(request, id):
    """Genera la Orden de Compra / Servicio Oficial en formato imprimible."""
    proceso = get_object_or_404(
        ProcesoAdquisicion.objects.select_related('unidad_solicitante', 'proveedor', 'autoridad_rpa'),
        id=id
    )
    total_oficial = sum(i.subtotal_oficial for i in proceso.items.all())
    return render(request, 'compras/pdf_orden_compra.html', {
        'proceso': proceso,
        'total_oficial': total_oficial,
    })
@login_required
def doc_nota_recepcion_adq_pdf(request, id):
    """Genera la Nota de Recepción Documental Oficial (Doc. N° 162 - Tránsito Directo)."""
    proceso = get_object_or_404(
        ProcesoAdquisicion.objects.select_related('unidad_solicitante', 'proveedor', 'autoridad_rpa'),
        id=id
    )
    total_oficial = sum(i.subtotal_oficial for i in proceso.items.all())
    return render(request, 'compras/pdf_nota_recepcion_adq.html', {
        'proceso': proceso,
        'total_oficial': total_oficial,
    })

@login_required
def doc_formulario_pedido_adq_pdf(request, id):
    """Paso 8: Genera el Formulario de Pedido Oficial marcado como Nueva Adquisición/Tránsito."""
    proceso = get_object_or_404(
        ProcesoAdquisicion.objects.select_related('unidad_solicitante', 'solicitante', 'autoridad_rpa'),
        id=id
    )
    total_oficial = sum(i.subtotal_oficial for i in proceso.items.all()) or proceso.total_referencial
    return render(request, 'compras/pdf_formulario_pedido_adq.html', {
        'proceso': proceso,
        'total_oficial': total_oficial,
    })

@login_required
@rol_requerido(['ALMACENERO', 'ADMINISTRADOR'])
def recepcion_documental_almacen(request, id):
    """Paso 7: Almacenes registra la recepción documental (REGLA: CERO STOCK / CERO KARDEX)."""
    proceso = get_object_or_404(ProcesoAdquisicion, id=id)
    if request.method == 'POST':
        nro_recepcion = request.POST.get('nro_nota_recepcion', '').strip()
        obs = request.POST.get('observaciones_recepcion', '').strip()

        proceso.recepcion_conforme = True
        proceso.fecha_recepcion = timezone.now().date()
        proceso.nro_nota_recepcion = nro_recepcion or f"REC-DOC-{proceso.id}"
        proceso.observaciones_recepcion = obs
        proceso.estado = 'CONCLUIDA'
        proceso.save()

        # OJO: AQUÍ NO SE CREA MovimientoInventario NI SE SUMA InventarioAlmacen
        messages.success(request, f"Recepción documental {proceso.nro_nota_recepcion} concluida. Trámite archivado sin afectación a stock.")
    return redirect('detalle_adquisicion', id=proceso.id)    

@login_required
@rol_requerido(['PRESUPUESTOS', 'ADMINISTRADOR'])
def certificar_presupuesto_adquisicion(request, id):
    """
    Paso 4: El área de Presupuestos registra el N° de Certificación y
    asigna Partida, Fuente, Organismo y Monto Certificado A CADA ÍTEM.
    """
    proceso = get_object_or_404(ProcesoAdquisicion, id=id)

    if request.method == 'POST':
        cert_nro = request.POST.get('certificacion_nro', '').strip()
        if not cert_nro:
            messages.error(request, "El número de Certificación Presupuestaria es obligatorio.")
            return redirect('detalle_adquisicion', id=proceso.id)

        try:
            with transaction.atomic():
                total_certificado = Decimal('0.00')

                # Procesar cada ítem individualmente
                for item in proceso.items.all():
                    partida_id = request.POST.get(f'item_partida_{item.id}')
                    fuente = request.POST.get(f'item_fuente_{item.id}', '20 - Recursos Específicos').strip()
                    organismo = request.POST.get(f'item_organismo_{item.id}', '111 - TGN').strip()
                    monto_str = request.POST.get(f'item_monto_{item.id}', '0').strip()

                    monto_item = Decimal(monto_str) if monto_str else item.subtotal_referencial

                    item.partida_id = partida_id if partida_id else None
                    item.fuente_financiamiento = fuente
                    item.organismo_financiador = organismo
                    item.monto_certificado = monto_item
                    item.save()

                    total_certificado += monto_item

                proceso.certificacion_nro = cert_nro
                proceso.monto_certificado_bs = total_certificado
                proceso.estado = 'CERTIFICADA'
                proceso.save()

                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Compras',
                    accion='Certificación Presupuestaria por Ítem',
                    descripcion=f'Presupuestos emitió la certificación {cert_nro} para {proceso.codigo} por Bs. {total_certificado:.2f}.'
                )

            messages.success(request, f"Certificación Presupuestaria {cert_nro} aprobada por un total de Bs. {total_certificado:.2f}.")
        except Exception as e:
            messages.error(request, f"Error al guardar certificación presupuestaria: {str(e)}")

    return redirect('detalle_adquisicion', id=proceso.id)


# ========================================================
# GENERADORES DE DOCUMENTOS OFICIALES (PDF / IMPRIMIBLES)
# ========================================================

@login_required
def doc_solicitud_adquisicion_pdf(request, id):
    """1. Formulario de Solicitud de Bienes y Servicios / TDR"""
    proceso = get_object_or_404(ProcesoAdquisicion.objects.select_related('unidad_solicitante', 'solicitante', 'autoridad_saf'), id=id)
    return render(request, 'compras/pdf_solicitud_bienes_servicios.html', {'proceso': proceso})


@login_required
def doc_checklist_pdf(request, id):
    """2. Formulario de Checklist Documental Verificado por Ítem"""
    proceso = get_object_or_404(ProcesoAdquisicion.objects.select_related('unidad_solicitante', 'solicitante'), id=id)
    return render(request, 'compras/pdf_checklist.html', {'proceso': proceso})


@login_required
def doc_nota_saf_pdf(request, id):
    """3. Nota Oficial de Remisión dirigida a la Secretaría SAF"""
    proceso = get_object_or_404(
        ProcesoAdquisicion.objects.select_related('unidad_solicitante', 'solicitante', 'autoridad_saf'), 
        id=id
    )
    total_referencial = sum(i.subtotal_referencial for i in proceso.items.all())
    return render(request, 'compras/pdf_nota_saf.html', {
        'proceso': proceso,
        'total_referencial': total_referencial,
    })


@login_required
def doc_certificacion_presupuestaria_pdf(request, id):
    """4. Certificación Presupuestaria Oficial desglosada por Ítem"""
    proceso = get_object_or_404(ProcesoAdquisicion.objects.select_related('unidad_solicitante'), id=id)
    return render(request, 'compras/pdf_certificacion_presupuestaria.html', {'proceso': proceso})

# ========================================================
# GESTIÓN INSTITUCIONAL DE AUTORIDADES (RPA / SAF / MAE)
# ========================================================

@login_required
@rol_requerido(['ADMINISTRADOR', 'ADMIN_ALMACENES', 'BIENES_SERVICIOS'])
def autoridades_list(request):
    """
    Panel de gestión de autoridades institucionales y vigencias de designación.
    """
    autoridades = AutoridadInstitucional.objects.all().order_by('cargo', '-is_active', '-fecha_designacion')
    
    return render(request, 'compras/autoridades_list.html', {
        'autoridades': autoridades,
        'cargos': AutoridadInstitucional.CARGOS_CHOICES,
    })


@login_required
@rol_requerido(['ADMINISTRADOR'])
def crear_autoridad(request):
    """
    Registra una nueva autoridad. Si se marca como activa, desactiva automáticamente
    la autoridad anterior de ese mismo cargo.
    """
    if request.method == 'POST':
        cargo = request.POST.get('cargo')
        nombre = request.POST.get('nombre_completo', '').strip()
        resolucion = request.POST.get('resolucion_designacion', '').strip()
        fecha_desig = request.POST.get('fecha_designacion')
        fecha_venc = request.POST.get('fecha_vencimiento') or None
        is_active = request.POST.get('is_active') == 'on'

        if not cargo or not nombre or not resolucion or not fecha_desig:
            messages.error(request, "Todos los campos con asterisco son obligatorios.")
            return redirect('autoridades_list')

        try:
            with transaction.atomic():
                # Si se marca como activa, desactivar las otras del mismo cargo
                if is_active:
                    AutoridadInstitucional.objects.filter(cargo=cargo).update(is_active=False)

                AutoridadInstitucional.objects.create(
                    cargo=cargo,
                    nombre_completo=nombre,
                    resolucion_designacion=resolucion,
                    fecha_designacion=fecha_desig,
                    fecha_vencimiento=fecha_venc,
                    is_active=is_active
                )
                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Compras',
                    accion='Registrar Autoridad Institucional',
                    descripcion=f'Se designó a {nombre} en el cargo de {cargo} ({resolucion}).'
                )
            messages.success(request, f"Autoridad '{nombre}' registrada con éxito.")
        except Exception as e:
            messages.error(request, f"Error al registrar la autoridad: {str(e)}")

    return redirect('autoridades_list')


@login_required
@rol_requerido(['ADMINISTRADOR'])
def editar_autoridad(request, id):
    autoridad = get_object_or_404(AutoridadInstitucional, id=id)

    if request.method == 'POST':
        autoridad.nombre_completo = request.POST.get('nombre_completo', '').strip()
        autoridad.resolucion_designacion = request.POST.get('resolucion_designacion', '').strip()
        autoridad.fecha_designacion = request.POST.get('fecha_designacion')
        autoridad.fecha_vencimiento = request.POST.get('fecha_vencimiento') or None
        
        is_active = request.POST.get('is_active') == 'on'
        if is_active and not autoridad.is_active:
            AutoridadInstitucional.objects.filter(cargo=autoridad.cargo).update(is_active=False)
        autoridad.is_active = is_active
        autoridad.save()

        messages.success(request, f"Datos de la autoridad '{autoridad.nombre_completo}' actualizados.")
        return redirect('autoridades_list')

    return redirect('autoridades_list')


@login_required
@rol_requerido(['ADMINISTRADOR'])
def toggle_autoridad(request, id):
    autoridad = get_object_or_404(AutoridadInstitucional, id=id)
    if not autoridad.is_active:
        # Si se va a activar, desactivar las otras del mismo cargo para que solo haya 1 en funciones
        AutoridadInstitucional.objects.filter(cargo=autoridad.cargo).update(is_active=False)
        autoridad.is_active = True
        autoridad.save()
        messages.success(request, f"{autoridad.nombre_completo} ahora está en funciones como {autoridad.get_cargo_display()}.")
    else:
        autoridad.is_active = False
        autoridad.save()
        messages.info(request, f"Se marcó como inactiva a la autoridad {autoridad.nombre_completo}.")

    return redirect('autoridades_list')

@login_required
def actualizar_datos_solicitud_bbss(request, id):
    """
    Permite guardar las correcciones realizadas directamente en el Formulario
    de Solicitud de Bienes y Servicios antes de imprimir.
    """
    proceso = get_object_or_404(ProcesoAdquisicion, id=id)

    if request.method == 'POST':
        proceso.programa = request.POST.get('programa', '000').strip()
        proceso.proyecto_actividad = request.POST.get('proyecto_actividad', '001').strip()
        proceso.fuente_financiamiento = request.POST.get('fuente_financiamiento', '20').strip()
        proceso.organismo_financiador = request.POST.get('organismo_financiador', '220').strip()
        
        # Tipo Bien o Servicio
        proceso.tipo = request.POST.get('tipo', proceso.tipo)
        proceso.save()

        messages.success(request, "Datos del formulario oficial actualizados y guardados correctamente.")

    return redirect('doc_solicitud_adquisicion_pdf', id=proceso.id)
@login_required
def actualizar_checklist_oficial(request, id):
    """Guarda las marcas de US, SDAF y Observaciones del CheckList Oficial."""
    proceso = get_object_or_404(ProcesoAdquisicion, id=id)

    if request.method == 'POST':
        datos = {}
        for key, value in request.POST.items():
            if key.startswith('chk_'):
                datos[key.replace('chk_', '')] = value.strip()

        proceso.checklist_matriz_data = datos
        proceso.save()
        messages.success(request, "CheckList Oficial guardado y actualizado con éxito.")

    return redirect('doc_checklist_pdf', id=proceso.id)

@login_required
def doc_autorizacion_rpa_pdf(request, id):
    """
    Nota Oficial de Autorización de Inicio de Contratación emitida por el RPA (Foto 2 - NOTA Nº 0199/2026).
    """
    proceso = get_object_or_404(
        ProcesoAdquisicion.objects.select_related('unidad_solicitante', 'autoridad_rpa'), 
        id=id
    )
    return render(request, 'compras/pdf_autorizacion_rpa.html', {'proceso': proceso})