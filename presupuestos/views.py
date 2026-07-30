from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.core.paginator import Paginator
from django.db.models import Q
from decimal import Decimal

from usuarios.decorators import rol_requerido
from auditoria.models import Bitacora
from organizacion.models import UnidadOrganizacional
from inventario.models import PartidaPresupuestaria
from .models import POA

GESTION_DEFAULT = 2026

@login_required
@rol_requerido(['PRESUPUESTOS', 'ADMINISTRADOR'])
def poa_list(request):
    """
    Lista las asignaciones presupuestarias POA del año fiscal actual [28].
    """
    query = request.GET.get('q', '').strip()
    poas = POA.objects.select_related('unidad', 'partida').all()

    if query:
        poas = poas.filter(
            Q(unidad__nombre__icontains=query) |
            Q(partida__codigo__icontains=query) |
            Q(partida__nombre__icontains=query)
        )

    poas = poas.order_by('unidad__nombre', 'partida__codigo')

    paginator = Paginator(poas, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'presupuestos/poa_list.html', {
        'page_obj': page_obj,
        'query': query
    })

@login_required
@rol_requerido(['PRESUPUESTOS', 'ADMINISTRADOR'])
def crear_poa(request):
    """
    Asigna un nuevo techo presupuestario (POA) a una unidad para una partida específica [28].
    """
    unidades = UnidadOrganizacional.objects.all().order_by('nombre')
    partidas = PartidaPresupuestaria.objects.all().order_by('codigo')

    if request.method == 'POST':
        unidad_id = request.POST.get('unidad')
        partida_id = request.POST.get('partida')
        monto_inicial_raw = request.POST.get('monto_inicial', '0.00')
        gestion = request.POST.get('gestion', GESTION_DEFAULT)

        if not unidad_id or not partida_id or not monto_inicial_raw:
            messages.error(request, 'Todos los campos con asterisco son obligatorios.')
            return redirect('crear_poa')

        unidad = get_object_or_404(UnidadOrganizacional, id=unidad_id)
        partida = get_object_or_404(PartidaPresupuestaria, id=partida_id)

        # Evitar duplicados de POA para la misma unidad, partida y año
        if POA.objects.filter(unidad=unidad, partida=partida, gestion=gestion).exists():
            messages.error(request, f'Ya existe un registro POA para esta unidad y partida presupuestaria en la gestión {gestion}.')
            return redirect('crear_poa')

        try:
            monto_inicial = Decimal(monto_inicial_raw)
            if monto_inicial < 0:
                raise ValueError
        except (ValueError, ArithmeticError):
            messages.error(request, 'El monto inicial debe ser un número decimal válido y no negativo.')
            return redirect('crear_poa')

        try:
            with transaction.atomic():
                # El saldo disponible se inicia igual al monto de apertura
                POA.objects.create(
                    unidad=unidad,
                    partida=partida,
                    gestion=gestion,
                    monto_inicial=monto_inicial,
                    monto_disponible=monto_inicial
                )
                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Presupuestos',
                    accion='Asignar Presupuesto POA',
                    descripcion=f'Asignación POA de {monto_inicial} Bs. a la unidad {unidad.nombre} para la partida {partida.codigo}'
                )

            messages.success(request, 'Presupuesto POA asignado correctamente.')
            return redirect('poa_list')

        except Exception as e:
            messages.error(request, f'Error de base de datos al guardar: {str(e)}')
            return redirect('crear_poa')

    return render(
        request, 
        'presupuestos/crear_poa.html', 
        {
            'unidades': unidades,
            'partidas': partidas,
            'gestion_default': GESTION_DEFAULT
        }
    )

@login_required
@rol_requerido(['PRESUPUESTOS', 'ADMINISTRADOR'])
def editar_poa(request, id):
    """
    Modifica los montos de una asignación POA existente [28].
    """
    poa = get_object_or_404(POA, id=id)

    if request.method == 'POST':
        monto_inicial_raw = request.POST.get('monto_inicial', '0.00')
        monto_disponible_raw = request.POST.get('monto_disponible', '0.00')

        try:
            monto_inicial = Decimal(monto_inicial_raw)
            monto_disponible = Decimal(monto_disponible_raw)
            if monto_inicial < 0 or monto_disponible < 0:
                raise ValueError
        except (ValueError, ArithmeticError):
            messages.error(request, 'Los montos numéricos deben ser decimales válidos y no negativos.')
            return redirect('editar_poa', id=id)

        try:
            with transaction.atomic():
                poa.monto_inicial = monto_inicial
                poa.monto_disponible = monto_disponible
                poa.save()

                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Presupuestos',
                    accion='Editar POA',
                    descripcion=f'Modificación de montos POA para {poa.unidad.nombre} - Partida {poa.partida.codigo}'
                )

            messages.success(request, 'Montos presupuestarios actualizados correctamente.')
            return redirect('poa_list')

        except Exception as e:
            messages.error(request, f'Error al actualizar el presupuesto POA: {str(e)}')
            return redirect('editar_poa', id=id)

    return render(request, 'presupuestos/editar_poa.html', {'poa': poa})