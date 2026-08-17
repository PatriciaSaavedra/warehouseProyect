from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.platypus import Table, TableStyle

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from decimal import Decimal
from django.http import HttpResponse, JsonResponse, HttpResponseForbidden

# --- AGREGA ESTE BLOQUE DE REPORTLAB AQUÍ ---
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.platypus import Table, TableStyle
from reportlab.lib import colors
# ---------------------------------------------

from usuarios.decorators import rol_requerido
from auditoria.models import Bitacora
from inventario.models import Proveedor, PartidaPresupuestaria, MovimientoInventario
from solicitudes.models import Solicitud
from .models import CompraMenor, ActaConformidad

@login_required
@rol_requerido(['BIENES_SERVICIOS', 'ADMINISTRADOR']) # <-- EXCLUSIVO BIENES Y SERVICIOS
def compras_list(request):
    """
    Bandeja de trabajo de Bienes y Servicios para monitorear Órdenes de Compra [28].
    """
    compras = CompraMenor.objects.select_related('proveedor', 'partida', 'solicitud_origen').all().order_by('-id')
    return render(
        request, 
        'compras/compras_list.html', 
        {'compras': compras}
    )

@login_required
@rol_requerido(['BIENES_SERVICIOS', 'ADMINISTRADOR'])
def crear_compra_menor(request):
    """
    Registra una Orden de Compra Menor arrastrando automáticamente la partida 
    y pre-calculando el monto estimado de la solicitud origen [28].
    """
    proveedores = Proveedor.objects.all().order_by('razon_social')
    partidas = PartidaPresupuestaria.objects.all().order_by('codigo')
    
    solicitud_id = request.GET.get('solicitud')
    solicitud_origen = None
    partida_defecto = None
    monto_estimado = Decimal('0.00') # <-- NUEVO: Monto pre-calculado

    if solicitud_id:
        solicitud_origen = get_object_or_404(Solicitud, id=solicitud_id)
        
        # 1. Identificar la partida presupuestaria de los materiales sin stock
        primer_detalle = solicitud_origen.detalles.first()
        if primer_detalle:
            partida_defecto = primer_detalle.material.partida

        # 2. Arrastrar y pre-calcular el costo total estimado de la compra [28]
        for detalle in solicitud_origen.detalles.all():
            # Buscamos el último costo de ingreso de cada material para estimar el total
            last_entrada = MovimientoInventario.objects.filter(
                material=detalle.material, 
                tipo='ENTRADA'
            ).order_by('-fecha').first()
            
            costo_u = last_entrada.costo_unitario if last_entrada else Decimal('0.00')
            cantidad = detalle.cantidad_aprobada if detalle.cantidad_aprobada is not None else detalle.cantidad_solicitada
            monto_estimado += cantidad * costo_u

    if request.method == 'POST':
        proveedor_id = request.POST.get('proveedor')
        partida_id = request.POST.get('partida')
        monto_total_raw = request.POST.get('monto_total', '0.00')
        gestion = request.POST.get('gestion', 2026)

        if not proveedor_id or not partida_id or not monto_total_raw:
            messages.error(request, 'El proveedor, la partida y el monto total son campos obligatorios.')
            return redirect('crear_compra_menor')

        proveedor = get_object_or_404(Proveedor, id=proveedor_id)
        partida = get_object_or_404(PartidaPresupuestaria, id=partida_id)

        try:
            monto_total = Decimal(monto_total_raw)
            if monto_total <= 0:
                raise ValueError
                
            # Control SABS de tope legal de contratación menor por el RPA
            if monto_total > 50000:
                messages.error(
                    request, 
                    'El monto excede los 50,000.00 Bs. permitidos para Compra Menor bajo competencia del RPA.'
                )
                return redirect('crear_compra_menor')
                
        except (ValueError, ArithmeticError):
            messages.error(request, 'El monto total debe ser un número decimal válido y mayor a cero.')
            return redirect('crear_compra_menor')

        total_compras = CompraMenor.objects.count() + 1
        nro_orden = f"OC-{str(total_compras).zfill(5)}"

        try:
            with transaction.atomic():
                CompraMenor.objects.create(
                    nro_orden=nro_orden,
                    proveedor=proveedor,
                    partida=partida,
                    solicitud_origen=solicitud_origen,
                    monto_total=monto_total,
                    gestion=gestion
                )

                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Compras',
                    accion='Registrar Orden de Compra Menor',
                    descripcion=f'Bienes y Servicios generó la Orden {nro_orden} con {proveedor.razon_social} por {monto_total} Bs.'
                )

            messages.success(request, f'Orden de Compra {nro_orden} registrada correctamente por la Unidad de Bienes y Servicios.')
            return redirect('compras_list')

        except Exception as e:
            messages.error(request, f'Error al registrar la compra: {str(e)}')
            return redirect('crear_compra_menor')

    return render(
        request,
        'compras/crear_compra_menor.html',
        {
            'proveedores': proveedores,
            'partidas': partidas,
            'solicitud_origen': solicitud_origen,
            'partida_defecto': partida_defecto,
            'monto_estimado': monto_estimado,  # <-- ENVIAMOS EL MONTO PRE-CALCULADO
            'gestion_default': 2026
        }
    )
@login_required
@rol_requerido(['BIENES_SERVICIOS', 'ADMINISTRADOR'])
def proveedores_list(request):
    """
    Directorio de Proveedores administrado por Bienes y Servicios [28].
    """
    proveedores = Proveedor.objects.all().order_by('razon_social')
    return render(request, 'compras/proveedores_list.html', {'proveedores': proveedores})

@login_required
@rol_requerido(['BIENES_SERVICIOS', 'ADMINISTRADOR'])
def crear_proveedor(request):
    """
    Registra un proveedor en el catálogo de adquisiciones antes de emitir Órdenes de Compra [28].
    """
    if request.method == 'POST':
        nit = request.POST.get('nit', '').strip()
        razon_social = request.POST.get('razon_social', '').strip()
        telefono = request.POST.get('telefono', '').strip()
        direccion = request.POST.get('direccion', '').strip()

        if not nit or not razon_social:
            messages.error(request, 'El NIT y la Razón Social son campos obligatorios.')
            return redirect('crear_proveedor')

        if Proveedor.objects.filter(nit=nit).exists():
            messages.error(request, 'Ya existe un proveedor registrado con este NIT.')
            return redirect('crear_proveedor')

        try:
            with transaction.atomic():
                Proveedor.objects.create(
                    nit=nit,
                    razon_social=razon_social,
                    telefono=telefono if telefono else None,
                    direccion=direccion if direccion else None
                )
                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Compras',
                    accion='Registrar Proveedor',
                    descripcion=f'Bienes y Servicios registró al proveedor: {razon_social} (NIT: {nit})'
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
            messages.error(request, 'El NIT ingresado ya pertenece a otro proveedor.')
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
                    descripcion=f'Se modificaron los datos del proveedor: {razon_social}'
                )

            messages.success(request, 'Proveedor actualizado correctamente.')
            return redirect('proveedores_list')

        except Exception as e:
            messages.error(request, f'Error al actualizar el proveedor: {str(e)}')
            return redirect('editar_proveedor', id=id)

    return render(request, 'compras/editar_proveedor.html', {'proveedor': proveedor})


def numero_a_letras(numero):
    """
    Convierte importes numéricos a texto para cumplir con el formato oficial de la Gobernación (Hasta 100,000 Bs.) [28].
    """
    unidades = ["", "un", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve"]
    decenas = ["", "diez", "veinte", "treinta", "cuarenta", "cincuenta", "sesenta", "setenta", "ochenta", "noventa"]
    especiales = {11: "once", 12: "doce", 13: "trece", 14: "catorce", 15: "quince", 16: "dieciséis", 17: "diecisiete", 18: "diecinueve"}
    centenas = ["", "cien", "doscientos", "trescientos", "cuatrocientos", "quinientos", "seiscientos", "setecientos", "ochocientos", "novecientos"]

    if numero == 0:
        return "Cero"

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
            return f"{decenas[d]} y {unidades[u]}"
        elif n < 1000:
            d_u = n % 100
            c = n // 100
            if n == 100:
                return "cien"
            elif c == 1:
                return f"ciento {convertir_grupo(d_u)}"
            if d_u == 0:
                return centenas[c]
            return f"{centenas[c]} {convertir_grupo(d_u)}"
        return ""

    partes = []
    if entero >= 1000:
        miles = entero // 1000
        resto = entero % 1000
        if miles == 1:
            partes.append("mil")
        else:
            partes.append(f"{convertir_grupo(miles)} mil")
        if resto > 0:
            partes.append(convertir_grupo(resto))
    else:
        partes.append(convertir_grupo(entero))

    letras = " ".join(p for p in partes if p).strip().capitalize()
    return f"{letras} {decimal:02d}/100 Bolivianos"


@login_required
@rol_requerido(['BIENES_SERVICIOS', 'ADMINISTRADOR'])
def detalle_compra(request, id):
    """
    Muestra la consola detallada de la Orden de Compra Menor [28].
    """
    compra = get_object_or_404(CompraMenor.objects.select_related('proveedor', 'partida', 'solicitud_origen'), id=id)
    detalles_solicitud = []
    
    if compra.solicitud_origen:
        detalles_solicitud = compra.solicitud_origen.detalles.select_related('material')

    return render(
        request, 
        'compras/detalle_compra.html', 
        {
            'compra': compra,
            'detalles': detalles_solicitud
        }
    )


@login_required
@rol_requerido(['BIENES_SERVICIOS', 'ADMINISTRADOR'])
def compra_pdf(request, id):
    """
    Genera el PDF imprimible oficial de la Orden de Compra de la Gobernación (Fiel al documento escaneado N° 081) [28].
    """
    compra = get_object_or_404(CompraMenor.objects.select_related('proveedor', 'partida', 'solicitud_origen'), id=id)
    
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="orden_compra_{compra.nro_orden}.pdf"'

    # Formato Vertical (Portrait) estándar para hojas de órdenes de compra
    pdf = canvas.Canvas(response, pagesize=letter)
    width, height = letter

    # Configurar metadatos del PDF
    pdf.setTitle(f"Orden de Compra - {compra.nro_orden}")
    pdf.setSubject("SGA - GAD Potosí")
    pdf.setAuthor("Unidad de Bienes y Servicios")

    # --- DIBUJAR CABECERA (GAD Potosí) ---
    pdf.setFont("Helvetica-Bold", 8)
    pdf.drawCentredString(width / 2.0, height - 40, "ESTADO PLURINACIONAL DE BOLIVIA")
    pdf.drawCentredString(width / 2.0, height - 50, "GOBIERNO AUTÓNOMO DEPARTAMENTAL DE POTOSÍ")
    pdf.setFont("Helvetica", 7.5)
    pdf.drawCentredString(width / 2.0, height - 60, "BIENES Y SERVICIOS")

    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawCentredString(width / 2.0, height - 85, f"ORDEN DE COMPRA BB.SS. N° {compra.nro_orden}/{compra.gestion}")

    # --- METADATOS DE ADQUISICIÓN ---
    pdf.setFont("Helvetica-Bold", 8.5)
    pdf.drawString(60, height - 120, "Fecha:")
    pdf.drawString(60, height - 138, "Unidad Solicitante:")
    pdf.drawString(60, height - 156, "A la orden de:")
    pdf.drawString(60, height - 174, "Dirección:")

    pdf.setFont("Helvetica", 8.5)
    fecha_adq = compra.fecha_registro.strftime('%d de %B de %Y').upper()
    pdf.drawString(160, height - 120, f"POTOSÍ, {fecha_adq}")
    
    unidad_sol = compra.solicitud_origen.unidad_solicitante.nombre if compra.solicitud_origen else "ADQUISICIÓN DIRECTA"
    pdf.drawString(160, height - 138, unidad_sol.upper())
    pdf.drawString(160, height - 156, compra.proveedor.razon_social.upper())
    pdf.drawString(160, height - 174, (compra.proveedor.direccion or "Av. Panamericana N° 7 - Cel: 62-42354").upper())

    # NIT del proveedor (Lado derecho)
    pdf.setFont("Helvetica-Bold", 8.5)
    pdf.drawString(420, height - 156, "NIT/C.I.:")
    pdf.setFont("Helvetica", 8.5)
    pdf.drawString(470, height - 156, compra.proveedor.nit)

    # Texto introductorio oficial
    pdf.setFont("Helvetica-Oblique", 8)
    pdf.drawString(60, height - 200, "Agradeceremos a ustedes tengan la gentileza de atender con la presente orden de compra:")

    # --- TABLA DE MATERIALES (Multi-ítem) ---
    headers = ['Item', 'Descripción', 'Unidad', 'Cantidad', 'Precio Unit. (Bs.)', 'Total (Bs.)']
    data = [headers]

    # Poblar ítems
    if compra.solicitud_origen:
        detalles_sol = compra.solicitud_origen.detalles.select_related('material')
        for idx, d in enumerate(detalles_sol, start=1):
            material_nombre = d.material.nombre if d.material else d.descripcion_material_no_catalogado
            unidad_medida = d.material.unidad_medida_fk.codigo if d.material and d.material.unidad_medida_fk else "Pza"
            cantidad = d.cantidad_aprobada if d.cantidad_aprobada is not None else d.cantidad_solicitada

            # Buscamos el costo unitario de esta compra
            last_ent = MovimientoInventario.objects.filter(material=d.material, tipo='ENTRADA').order_by('-fecha').first()
            p_unit = last_ent.costo_unitario if last_ent else Decimal('0.00')
            p_tot = cantidad * p_unit

            data.append([
                str(idx),
                material_nombre.upper(),
                unidad_medida.upper(),
                str(cantidad),
                f"{p_unit:.2f}",
                f"{p_tot:.2f}"
            ])
    else:
        # Fila genérica si es adquisición libre
        data.append(["1", "ADQUISICIÓN GENERAL DE SUMINISTROS", "GLB", "1", f"{compra.monto_total:.2f}", f"{compra.monto_total:.2f}"])

    # Añadir fila del TOTAL al final
    data.append(["", "", "", "", "TOTAL Bs", f"{compra.monto_total:.2f}"])

    # Configurar anchos de columna (Suma 490pt, dejando márgenes laterales de 60pt a los lados en hoja Letter de 612pt)
    col_widths = [30, 210, 50, 50, 75, 75]
    t = Table(data, colWidths=col_widths)

    last_row = len(data) - 1

    t_style = TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('ALIGN', (1, 1), (1, -1), 'LEFT'),  # Descripciones a la izquierda
        ('ALIGN', (4, 1), (-1, -1), 'RIGHT'), # Valores a la derecha
        
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 8),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F3F4F6')),

        # Bordes limpios de la orden
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#9CA3AF')),
        ('FONTNAME', (4, last_row), (5, last_row), 'Helvetica-Bold'), # Fila de TOTAL en negrita
        
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
    ])
    t.setStyle(t_style)

    # Calcular y pintar la tabla
    w_actual, h_actual = t.wrapOn(pdf, 490, height - 280)
    pdf_y = height - 230 - h_actual
    t.drawOn(pdf, 612 - 60 - 490, pdf_y) # Alineado a la derecha con margen

    # --- TEXTO DEL TOTAL EN LETRAS (SON: ...) ---
    text_y = pdf_y - 25
    pdf.setFont("Helvetica-Bold", 8.5)
    total_letras = numero_a_letras(compra.monto_total).upper()
    pdf.drawString(60, text_y, f"SON: {total_letras}")

    # --- TEXTO DE CONDICIONES SABS (Nota de pie de página oficial) ---
    nota_y = text_y - 45
    pdf.setFont("Helvetica", 7)
    pdf.drawString(100, nota_y, "Nota: Tomar en cuenta las características y especificaciones de lo cotizado para la Gobernación.")
    pdf.drawString(100, nota_y - 10, "La entrega debe realizarse en los almacenes del G.A.D.P. El tiempo de entrega según cotización es de 5 días.")
    pdf.drawString(100, nota_y - 20, "D.S. 0181; Art. 5 inc. cc). Será aplicable sólo en casos de adquisición de bienes o servicios de entrega o prestación,")
    pdf.drawString(100, nota_y - 30, "en un plazo no mayor a 15 días calendario según RE-SABS en su art. 12.")

    # --- SECCIÓN DE FIRMAS (3 recuadros de firma oficiales) ---
    y_firma = 80
    pdf.setFont("Helvetica", 7.5)
    
    # Recuadro 1: Bienes y Servicios
    pdf.drawString(60, y_firma, "___________________________")
    pdf.drawString(60, y_firma - 10, "FIRMA")
    pdf.setFont("Helvetica-Bold", 7.5)
    pdf.drawString(60, y_firma - 20, "RESPONSABLE DE BIENES Y SERVICIOS")

    # Recuadro 2: Jefe Unidad Administrativa
    pdf.setFont("Helvetica", 7.5)
    pdf.drawString(240, y_firma, "___________________________")
    pdf.drawString(240, y_firma - 10, "FIRMA")
    pdf.setFont("Helvetica-Bold", 7.5)
    pdf.drawString(240, y_firma - 20, "JEFE UNIDAD ADMINISTRATIVA")

    # Recuadro 3: Proveedor
    pdf.setFont("Helvetica", 7.5)
    pdf.drawString(420, y_firma, "___________________________")
    pdf.drawString(420, y_firma - 10, "FIRMA Y SELLO")
    pdf.setFont("Helvetica-Bold", 7.5)
    pdf.drawString(420, y_firma - 20, "PROVEEDOR ADJUDICADO")

    pdf.save()
    return response