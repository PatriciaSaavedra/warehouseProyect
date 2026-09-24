from decimal import Decimal
from auditoria.models import Bitacora
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
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

from inventario.models import Material
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
@rol_requerido(['BIENES_SERVICIOS', 'ADMINISTRADOR'])
def proveedores_list(request):
  """Directorio de Proveedores administrado por Bienes y Servicios."""
  proveedores = Proveedor.objects.all().order_by('razon_social')
  return render(
      request, 'compras/proveedores_list.html', {'proveedores': proveedores}
  )


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
@rol_requerido(['BIENES_SERVICIOS', 'ADMINISTRADOR'])
def editar_proveedor(request, id):
  proveedor = get_object_or_404(Proveedor, id=id)

  if request.method == 'POST':
    nit = request.POST.get('nit', '').strip()
    razon_social = request.POST.get('razon_social', '').strip()

    if not nit or not razon_social:
      messages.error(request, 'El NIT y la Razón Social son campos obligatorios.')
      return redirect('editar_proveedor', id=id)

    if Proveedor.objects.filter(nit=nit).exclude(id=id).exists():
      messages.error(
          request, 'El NIT ingresado ya pertenece a otro proveedor.'
      )
      return redirect('editar_proveedor', id=id)

    try:
      with transaction.atomic():
        proveedor.nit = nit
        proveedor.razon_social = razon_social
        proveedor.telefono = request.POST.get('telefono', '').strip() or None
        proveedor.direccion = request.POST.get('direccion', '').strip() or None
        proveedor.save()

        Bitacora.objects.create(
            usuario=request.user,
            modulo='Compras',
            accion='Editar Proveedor',
            descripcion=(
                f'Se modificaron los datos del proveedor: {razon_social}'
            ),
        )

      messages.success(request, 'Proveedor actualizado correctamente.')
      return redirect('proveedores_list')

    except Exception as e:
      messages.error(request, f'Error al actualizar el proveedor: {str(e)}')
      return redirect('editar_proveedor', id=id)

  return render(request, 'compras/editar_proveedor.html', {'proveedor': proveedor})


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