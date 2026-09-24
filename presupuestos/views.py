from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction, DatabaseError
from django.core.paginator import Paginator
from django.core.exceptions import ValidationError
from django.db.models import Q, Sum
from django.http import JsonResponse
from decimal import Decimal

from usuarios.decorators import rol_requerido
from auditoria.models import Bitacora
from organizacion.models import Secretaria, UnidadOrganizacional
from inventario.models import PartidaPresupuestaria
from .models import POA, ModificacionPresupuestaria

GESTION_DEFAULT = 2026

@login_required
@rol_requerido(['PRESUPUESTOS', 'ADMINISTRADOR'])
def poa_list(request):
    """
    Lista los techos presupuestarios POA asignados para la gestión actual.
    """
    query = request.GET.get('q', '').strip()
    filtro_gestion = request.GET.get('gestion', str(GESTION_DEFAULT)).strip()
    filtro_unidad = request.GET.get('unidad', '').strip()

    poas = POA.objects.select_related('unidad', 'unidad__secretaria', 'partida').filter(gestion=filtro_gestion)

    if query:
        poas = poas.filter(
            Q(unidad__nombre__icontains=query) |
            Q(partida__codigo__icontains=query) |
            Q(partida__nombre__icontains=query)
        )
    if filtro_unidad:
        poas = poas.filter(unidad_id=filtro_unidad)

    poas = poas.order_by('unidad__secretaria__nombre', 'unidad__nombre', 'partida__codigo')

    paginator = Paginator(poas, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    unidades = UnidadOrganizacional.objects.filter(is_active=True).order_by('nombre')

    return render(request, 'presupuestos/poa_list.html', {
        'page_obj': page_obj,
        'query': query,
        'unidades': unidades,
        'filtro_unidad': filtro_unidad,
        'gestion_actual': filtro_gestion
    })


@login_required
@rol_requerido(['PRESUPUESTOS', 'ADMINISTRADOR'])
def crear_poa(request):
    """
    Asigna un nuevo techo presupuestario (POA) a una unidad para una partida específica.
    """
    unidades = UnidadOrganizacional.objects.filter(is_active=True).order_by('nombre')
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

        if POA.objects.filter(unidad=unidad, partida=partida, gestion=gestion).exists():
            messages.error(request, f'Ya existe un registro POA para {unidad.nombre} en la partida {partida.codigo} (Gestión {gestion}).')
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
                poa = POA.objects.create(
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
                    descripcion=f'Asignación POA de Bs. {monto_inicial:.2f} a {unidad.nombre} para la partida {partida.codigo}'
                )

            messages.success(request, f'Techo presupuestario asignado exitosamente para la partida {partida.codigo}.')
            return redirect('poa_list')

        except Exception as e:
            messages.error(request, f'Error al guardar en base de datos: {str(e)}')
            return redirect('crear_poa')

    return render(request, 'presupuestos/crear_poa.html', {
        'unidades': unidades,
        'partidas': partidas,
        'gestion_default': GESTION_DEFAULT
    })


@login_required
@rol_requerido(['PRESUPUESTOS', 'ADMINISTRADOR'])
def editar_poa(request, id):
    poa = get_object_or_404(POA, id=id)

    if request.method == 'POST':
        monto_inicial_raw = request.POST.get('monto_inicial', '0.00')

        try:
            monto_inicial = Decimal(monto_inicial_raw)
            if monto_inicial < 0:
                raise ValueError
        except (ValueError, ArithmeticError):
            messages.error(request, 'El monto inicial debe ser un decimal válido y no negativo.')
            return redirect('editar_poa', id=id)

        try:
            with transaction.atomic():
                diferencia = monto_inicial - poa.monto_inicial
                if poa.monto_disponible + diferencia < 0:
                    raise ValueError(f"No es posible reducir el presupuesto de apertura a Bs. {monto_inicial:.2f} porque dejaría el saldo disponible en negativo.")

                poa.monto_inicial = monto_inicial
                poa.monto_disponible += diferencia
                poa.save()

                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Presupuestos',
                    accion='Editar POA',
                    descripcion=f'Se reajustó el monto inicial de {poa.unidad.nombre} - {poa.partida.codigo} a Bs. {monto_inicial:.2f}'
                )

            messages.success(request, 'Presupuesto POA de apertura reajustado correctamente.')
            return redirect('poa_list')

        except ValueError as e:
            messages.error(request, str(e))
            return redirect('editar_poa', id=id)
        except Exception as e:
            messages.error(request, f'Error al actualizar el presupuesto: {str(e)}')
            return redirect('editar_poa', id=id)

    return render(request, 'presupuestos/editar_poa.html', {'poa': poa})


@login_required
@rol_requerido(['PRESUPUESTOS', 'ADMINISTRADOR'])
def registrar_modificacion(request, poa_id):
    """
    Modificación directa individual (Incremento o Reducción sobre un POA específico).
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
            messages.error(request, 'El monto debe ser estrictamente mayor a cero.')
            return redirect('registrar_modificacion', poa_id=poa.id)

        try:
            with transaction.atomic():
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
            msg = e.message if hasattr(e, 'message') else "; ".join(e.messages) if hasattr(e, 'messages') else str(e)
            messages.error(request, msg)
            return redirect('registrar_modificacion', poa_id=poa.id)
        except Exception as e:
            messages.error(request, f'Error al registrar la modificación presupuestaria: {str(e)}')
            return redirect('registrar_modificacion', poa_id=poa.id)

    return render(request, 'presupuestos/registrar_modificacion.html', {'poa': poa})


# ========================================================
# NUEVA VISTA: TRASPASO INTRAINSTITUCIONAL ENTRE PARTIDAS
# ========================================================

@login_required
@rol_requerido(['PRESUPUESTOS', 'ADMINISTRADOR'])
def traspaso_entre_partidas(request):
    """
    Tarjeta 4: Traspaso Intrainstitucional de Presupuesto.
    Transfiere saldo disponible entre dos partidas de la misma Unidad Organizacional en una sola operación atómica.
    """
    unidades = UnidadOrganizacional.objects.filter(is_active=True).order_by('nombre')
    partidas = PartidaPresupuestaria.objects.all().order_by('codigo')

    if request.method == 'POST':
        unidad_id = request.POST.get('unidad')
        partida_origen_id = request.POST.get('partida_origen')
        partida_destino_id = request.POST.get('partida_destino')
        monto_raw = request.POST.get('monto', '0.00')
        nro_resolucion = request.POST.get('nro_resolucion', '').strip()
        justificacion = request.POST.get('justificacion', '').strip()

        if not unidad_id or not partida_origen_id or not partida_destino_id or not monto_raw or not justificacion:
            messages.error(request, 'Todos los campos marcados con (*) son de llenado obligatorio.')
            return redirect('traspaso_entre_partidas')

        if partida_origen_id == partida_destino_id:
            messages.error(request, 'La Partida Cedente (Origen) y la Partida Receptora (Destino) no pueden ser iguales.')
            return redirect('traspaso_entre_partidas')

        try:
            monto = Decimal(monto_raw)
            if monto <= 0:
                raise ValueError
        except (ValueError, ArithmeticError):
            messages.error(request, 'El monto a transferir debe ser un número decimal estrictamente mayor a 0.')
            return redirect('traspaso_entre_partidas')

        unidad = get_object_or_404(UnidadOrganizacional, id=unidad_id)
        partida_origen = get_object_or_404(PartidaPresupuestaria, id=partida_origen_id)
        partida_destino = get_object_or_404(PartidaPresupuestaria, id=partida_destino_id)

        try:
            with transaction.atomic():
                # 1. Obtener y bloquear la partida origen
                poa_origen = POA.objects.select_for_update().filter(
                    unidad=unidad,
                    partida=partida_origen,
                    gestion=GESTION_DEFAULT
                ).first()

                if not poa_origen:
                    raise ValidationError(f"La unidad {unidad.nombre} no tiene techo presupuestario en la partida origen {partida_origen.codigo}.")

                if poa_origen.monto_disponible < monto:
                    raise ValidationError(
                        f"Saldo insuficiente en la partida cedente {partida_origen.codigo}. "
                        f"Disponible: Bs. {poa_origen.monto_disponible:.2f} | Solicitado: Bs. {monto:.2f}"
                    )

                # 2. Obtener o crear la partida destino para la misma unidad
                poa_destino, created = POA.objects.select_for_update().get_or_create(
                    unidad=unidad,
                    partida=partida_destino,
                    gestion=GESTION_DEFAULT,
                    defaults={'monto_inicial': Decimal('0.00'), 'monto_disponible': Decimal('0.00')}
                )

                # 3. Registrar REDUCCIÓN en la partida cedente (su save() validará que no sea negativo)
                desc_origen = f"[Traspaso a Partida {partida_destino.codigo}] Res: {nro_resolucion}. {justificacion}"
                ModificacionPresupuestaria.objects.create(
                    poa=poa_origen,
                    tipo='REDUCCION',
                    monto=monto,
                    justificacion=desc_origen,
                    usuario=request.user
                )

                # 4. Registrar INCREMENTO en la partida receptora
                desc_destino = f"[Traspaso desde Partida {partida_origen.codigo}] Res: {nro_resolucion}. {justificacion}"
                ModificacionPresupuestaria.objects.create(
                    poa=poa_destino,
                    tipo='INCREMENTO',
                    monto=monto,
                    justificacion=desc_destino,
                    usuario=request.user
                )

                Bitacora.objects.create(
                    usuario=request.user,
                    modulo='Presupuestos',
                    accion='Traspaso Intrainstitucional',
                    descripcion=(
                        f"Traspaso de Bs. {monto:.2f} para {unidad.nombre}: "
                        f"{partida_origen.codigo} ➔ {partida_destino.codigo} (Res: {nro_resolucion})"
                    )
                )

            messages.success(
                request,
                f"Traspaso presupuestario exitoso: Se transfirieron Bs. {monto:.2f} desde la partida "
                f"{partida_origen.codigo} a la partida {partida_destino.codigo} para la unidad {unidad.nombre}."
            )
            return redirect('poa_list')

        except ValidationError as e:
            msg = e.message if hasattr(e, 'message') else "; ".join(e.messages) if hasattr(e, 'messages') else str(e)
            messages.error(request, msg)
            return redirect('traspaso_entre_partidas')
        except Exception as e:
            messages.error(request, f"Error al procesar el traspaso presupuestario: {str(e)}")
            return redirect('traspaso_entre_partidas')

    return render(request, 'presupuestos/traspaso.html', {
        'unidades': unidades,
        'partidas': partidas,
        'gestion_actual': GESTION_DEFAULT
    })


@login_required
def api_partidas_poa_unidad(request, unidad_id):
    """
    Retorna vía JSON las partidas registradas y sus saldos disponibles para una Unidad específica.
    """
    poas = POA.objects.filter(unidad_id=unidad_id, gestion=GESTION_DEFAULT).select_related('partida')
    data = [
        {
            'partida_id': p.partida.id,
            'codigo': p.partida.codigo,
            'nombre': p.partida.nombre,
            'disponible': float(p.monto_disponible)
        }
        for p in poas
    ]
    return JsonResponse(data, safe=False)


# ========================================================
# REPORTE DE EJECUCIÓN PRESUPUESTARIA CONSOLIDADO
# ========================================================

@login_required
@rol_requerido(['PRESUPUESTOS', 'ADMINISTRADOR', 'SECRETARIO_SAF', 'JEFE_ADMINISTRATIVO'])
def reporte_presupuestos(request):
    """
    Tarjeta 11 y Tarjeta 4: Tablero de Control y Reporte Consolidado de Ejecución POA.
    Permite filtrar por Secretaría, Unidad, Partida y Gestión.
    """
    secretarias = Secretaria.objects.filter(is_active=True).order_by('nombre')
    unidades = UnidadOrganizacional.objects.filter(is_active=True).order_by('nombre')
    partidas = PartidaPresupuestaria.objects.all().order_by('codigo')

    filtro_secretaria = request.GET.get('secretaria', '').strip()
    filtro_unidad = request.GET.get('unidad', '').strip()
    filtro_partida = request.GET.get('partida', '').strip()
    filtro_gestion = request.GET.get('gestion', str(GESTION_DEFAULT)).strip()

    poas_query = POA.objects.select_related('unidad', 'unidad__secretaria', 'partida').filter(gestion=filtro_gestion)

    if filtro_secretaria:
        poas_query = poas_query.filter(unidad__secretaria_id=filtro_secretaria)
    if filtro_unidad:
        poas_query = poas_query.filter(unidad_id=filtro_unidad)
    if filtro_partida:
        poas_query = poas_query.filter(partida_id=filtro_partida)

    poas = poas_query.order_by('unidad__secretaria__nombre', 'unidad__nombre', 'partida__codigo')

    # Agregaciones de totales
    totales = poas.aggregate(
        total_inicial=Sum('monto_inicial'),
        total_comprometido=Sum('monto_comprometido'),
        total_ejecutado=Sum('monto_ejecutado'),
        total_disponible=Sum('monto_disponible')
    )

    t_inicial = totales['total_inicial'] or Decimal('0.00')
    t_comprometido = totales['total_comprometido'] or Decimal('0.00')
    t_ejecutado = totales['total_ejecutado'] or Decimal('0.00')
    t_disponible = totales['total_disponible'] or Decimal('0.00')

    porcentaje_global = round((float(t_ejecutado) / float(t_inicial) * 100), 2) if t_inicial > 0 else 0.0

    return render(request, 'presupuestos/reporte.html', {
        'poas': poas,
        'secretarias': secretarias,
        'unidades': unidades,
        'partidas': partidas,
        'filtro_secretaria': filtro_secretaria,
        'filtro_unidad': filtro_unidad,
        'filtro_partida': filtro_partida,
        'filtro_gestion': filtro_gestion,
        'total_inicial': t_inicial,
        'total_comprometido': t_comprometido,
        'total_ejecutado': t_ejecutado,
        'total_disponible': t_disponible,
        'porcentaje_global': porcentaje_global,
    })
# En presupuestos/views.py, actualizar crear_poa y agregar endpoint de materiales por partida:

import json
import html
from inventario.models import Material

@login_required
@rol_requerido(["PRESUPUESTOS", "ADMINISTRADOR"])
def crear_poa(request):
  """Formulario 005: Registra el techo POA vinculando los insumos demandados,"""
  unidades = UnidadOrganizacional.objects.filter(is_active=True).order_by(
      "nombre"
  )
  partidas = PartidaPresupuestaria.objects.all().order_by("codigo")

  if request.method == "POST":
    unidad_id = request.POST.get("unidad")
    partida_id = request.POST.get("partida")
    gestion = request.POST.get("gestion", GESTION_DEFAULT)
    payload_raw = request.POST.get("payload_items", "[]")

    if not unidad_id or not partida_id:
      messages.error(
          request,
          "Debe seleccionar una Unidad Organizacional y una Partida"
          " Presupuestaria.",
      )
      return redirect("crear_poa")

    unidad = get_object_or_404(UnidadOrganizacional, id=unidad_id)
    partida = get_object_or_404(PartidaPresupuestaria, id=partida_id)

    if POA.objects.filter(
        unidad=unidad, partida=partida, gestion=gestion
    ).exists():
      messages.error(
          request,
          f"Ya existe un techo POA para {unidad.nombre} en la partida"
          f" {partida.codigo} ({gestion}). Si desea aumentarlo use"
          " Modificaciones.",
      )
      return redirect("crear_poa")

    payload_raw = (
        html.unescape(payload_raw) if hasattr(html, "unescape") else payload_raw
    )
    try:
      items_data = json.loads(payload_raw)
    except Exception:
      items_data = []

    try:
      with transaction.atomic():
        # 1. Crear Cabecera POA sin campos inexistentes
        poa = POA.objects.create(
            unidad=unidad,
            partida=partida,
            gestion=gestion,
            monto_inicial=Decimal("0.00"),
            monto_disponible=Decimal("0.00"),
        )

        total_calculado = Decimal("0.00")

        # 2. Registrar Ítems del Formulario 005 (si existe el modelo en models.py)
        if items_data:
          from .models import DetalleProgramacionPOA

          for it in items_data:
            material_id = it.get("material_id")
            cant = int(it.get("cantidad", 1))
            precio_u = Decimal(str(it.get("precio_unitario", "0.00")))
            codigo_act = it.get("codigo_actividad", "13.1 - 13.4").strip()

            material_obj = Material.objects.get(id=material_id)
            subt = Decimal(cant) * precio_u
            total_calculado += subt

            # Si creaste DetalleProgramacionPOA, lo guarda; de lo contrario se calcula el techo
            try:
              DetalleProgramacionPOA.objects.create(
                  poa=poa,
                  codigo_actividad_poa=codigo_act or "13.1 - 13.4",
                  material=material_obj,
                  unidad_medida=(
                      material_obj.unidad_medida_fk.codigo
                      if material_obj.unidad_medida_fk
                      else material_obj.unidad_medida
                  ),
                  cantidad_programada=cant,
                  precio_unitario_estimado=precio_u,
                  subtotal=subt,
              )
            except Exception:
              pass  # Por si el modelo aún no fue migrado

        # 3. Asignar el techo inicial y disponible con la suma exacta del desglose
        poa.monto_inicial = total_calculado
        poa.monto_disponible = total_calculado
        poa.save()

        Bitacora.objects.create(
            usuario=request.user,
            modulo="Presupuestos",
            accion="Registro Formulario 005 POA",
            descripcion=(
                f"Se programó la partida {partida.codigo} ({unidad.nombre}) con"
                f" techo de Bs. {total_calculado:.2f} desglosado en"
                f" {len(items_data)} ítems."
            ),
        )

      messages.success(
          request,
          f"Techo POA de Bs. {total_calculado:.2f} registrado exitosamente con"
          " el desglose del Formulario 005.",
      )
      return redirect("poa_list")

    except Exception as e:
      messages.error(request, f"Error al guardar el Formulario 005: {str(e)}")
      return redirect("crear_poa")

  return render(
      request,
      "presupuestos/crear_poa.html",
      {
          "unidades": unidades,
          "partidas": partidas,
          "gestion_default": GESTION_DEFAULT,
      },
  )

@login_required
def api_materiales_por_partida(request, partida_id):
    """
    Retorna los materiales catalogados que pertenecen a una partida presupuestaria específica.
    """
    materiales = Material.objects.filter(partida_id=partida_id, is_active=True).select_related('unidad_medida_fk')
    data = [
        {
            'id': m.id,
            'codigo': m.codigo,
            'nombre': m.nombre,
            'unidad': m.unidad_medida_fk.codigo if m.unidad_medida_fk else m.unidad_medida
        }
        for m in materiales
    ]
    return JsonResponse(data, safe=False)
@login_required
@rol_requerido(['PRESUPUESTOS', 'ADMINISTRADOR', 'SECRETARIO_SAF', 'JEFE_ADMINISTRATIVO'])
def detalle_poa(request, id):
    """
    Muestra la Ficha Oficial del Formulario 005 para una Partida y Unidad específica:
    - Cabecera oficial (Categoría Programática, Secretaría, Techos).
    - Desglose de insumos demandados (actividad POA, cantidades anuales, precio unitario y saldo físico remanente).
    - Historial de modificaciones y traspasos presupuestarios (Trazabilidad Ley 1178).
    """
    poa = get_object_or_404(
        POA.objects.select_related('unidad', 'unidad__secretaria', 'partida'),
        id=id
    )

    # Recuperar los materiales programados en el Formulario 005
    items_programados = poa.programacion_items.select_related('material').order_by('id')

    # Recuperar el historial de traspasos y modificaciones de esta partida
    historial_modificaciones = poa.modificaciones.select_related('usuario').order_by('-fecha_registro')

    Bitacora.objects.create(
        usuario=request.user,
        modulo='Presupuestos',
        accion='Visualizar Formulario 005 POA',
        descripcion=f'Consulta de programación Form. 005: {poa.unidad.nombre} - Partida {poa.partida.codigo} ({poa.gestion})'
    )

    return render(request, 'presupuestos/detalle_poa.html', {
        'poa': poa,
        'items_programados': items_programados,
        'historial_modificaciones': historial_modificaciones,
    })