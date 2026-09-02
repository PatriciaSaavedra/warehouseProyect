# --- TU ARCHIVO presupuestos/views.py INTEGRADO ---

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction, DatabaseError
from django.core.paginator import Paginator
from django.core.exceptions import ValidationError  # <-- NUEVO: Para capturar saldo insuficiente en modificaciones
from django.db.models import Q, Sum  # <-- NUEVO: Sum para agregaciones del reporte
from decimal import Decimal

from usuarios.decorators import rol_requerido
from auditoria.models import Bitacora
from organizacion.models import UnidadOrganizacional
from inventario.models import PartidaPresupuestaria
from .models import POA, ModificacionPresupuestaria  # <-- NUEVO: Importar modelo de modificaciones

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


# ========================================================
# NUEVAS VISTAS OPERATIVAS (TARJETAS 10 Y 11)
# ========================================================

@login_required
@rol_requerido(['PRESUPUESTOS', 'ADMINISTRADOR'])
def registrar_modificacion(request, poa_id):
    """
    Tarjeta 10: Registra un incremento o reducción presupuestaria (traspaso) 
    para un POA específico, manteniendo el historial y actualizando saldos automáticamente.
    """
    poa = get_object_or_404(POA, id=poa_id)

    if request.method == 'POST':
        tipo = request.POST.get('tipo')
        monto_raw = request.POST.get('monto', '0.00')
        justificacion = request.POST.get('justificacion', '').strip()

        if not tipo or not monto_raw or not justificacion:
            messages.error(request, 'Todos los campos son obligatorios.')
            return redirect('registrar_modificacion', poa_id=poa.id)

        try:
            monto = Decimal(monto_raw)
            if monto <= 0:
                raise ValueError
        except (ValueError, ArithmeticError):
            messages.error(request, 'El monto debe ser un número decimal válido y estrictamente mayor a cero.')
            return redirect('registrar_modificacion', poa_id=poa.id)

        try:
            with transaction.atomic():
                # El método save() del modelo ModificacionPresupuestaria se encarga de:
                # 1. Validar fondos antes de aplicar reducciones.
                # 2. Actualizar monto_disponible en el POA de forma segura (Transacción).
                # 3. Registrar el log en la Bitácora de Auditoría.
                ModificacionPresupuestaria.objects.create(
                    poa=poa,
                    tipo=tipo,
                    monto=monto,
                    justificacion=justificacion,
                    usuario=request.user
                )

            messages.success(request, f'Modificación presupuestaria ({tipo}) aplicada correctamente.')
            return redirect('poa_list')

        except ValidationError as e:
            # Captura la validación de fondos insuficientes levantada por el modelo
            messages.error(request, e.message)
            return redirect('registrar_modificacion', poa_id=poa.id)
        except Exception as e:
            messages.error(request, f'Error al registrar la modificación presupuestaria: {str(e)}')
            return redirect('registrar_modificacion', poa_id=poa.id)

    return render(request, 'presupuestos/registrar_modificacion.html', {
        'poa': poa
    })


@login_required
@rol_requerido(['PRESUPUESTOS', 'ADMINISTRADOR'])
def reporte_presupuestos(request):
    """
    Tarjeta 11: Genera el reporte consolidado de ejecución del POA,
    permitiendo búsquedas y filtros cruzados por Unidad, Partida y Gestión.
    """
    unidades = UnidadOrganizacional.objects.all().order_by('nombre')
    partidas = PartidaPresupuestaria.objects.all().order_by('codigo')

    # Capturar parámetros de filtros de URL
    filtro_unidad = request.GET.get('unidad', '').strip()
    filtro_partida = request.GET.get('partida', '').strip()
    filtro_gestion = request.GET.get('gestion', str(GESTION_DEFAULT)).strip()

    poas_query = POA.objects.all()

    if filtro_unidad:
        poas_query = poas_query.filter(unidad_id=filtro_unidad)
    if filtro_partida:
        poas_query = poas_query.filter(partida_id=filtro_partida)
    if filtro_gestion:
        poas_query = poas_query.filter(gestion=filtro_gestion)

    # Ordenar por Unidad y Partida
    poas = poas_query.select_related('unidad', 'partida').order_by('unidad__nombre', 'partida__codigo')

    # Tarjeta 11: Calcular de forma automatizada las sumas y totales de las columnas del reporte
    totales = poas.aggregate(
        total_inicial=Sum('monto_inicial'),
        total_comprometido=Sum('monto_comprometido'),
        total_ejecutado=Sum('monto_ejecutado'),
        total_disponible=Sum('monto_disponible')
    )

    return render(request, 'presupuestos/reporte.html', {
        'poas': poas,
        'unidades': unidades,
        'partidas': partidas,
        'filtro_unidad': filtro_unidad,
        'filtro_partida': filtro_partida,
        'filtro_gestion': filtro_gestion,
        'total_inicial': totales['total_inicial'] or 0.00,
        'total_comprometido': totales['total_comprometido'] or 0.00,
        'total_ejecutado': totales['total_ejecutado'] or 0.00,
        'total_disponible': totales['total_disponible'] or 0.00,
    })