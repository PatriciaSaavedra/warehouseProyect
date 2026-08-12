# FILE: compras/views.py
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from decimal import Decimal

from usuarios.decorators import rol_requerido
from auditoria.models import Bitacora
from inventario.models import Proveedor, PartidaPresupuestaria, MovimientoInventario  # <-- IMPORTACIÓN AGREGADA
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
