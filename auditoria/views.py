from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q

from .models import Bitacora
from usuarios.decorators import rol_requerido


@login_required
@rol_requerido(['ADMINISTRADOR'])
def auditoria_view(request):

    query = request.GET.get('q')
    modulo = request.GET.get('modulo')

    registros = Bitacora.objects.select_related(
        'usuario'
    )

    if query:
        registros = registros.filter(
            Q(usuario__username__icontains=query) |
            Q(usuario__first_name__icontains=query) |
            Q(usuario__last_name__icontains=query) |
            Q(accion__icontains=query) |
            Q(descripcion__icontains=query)
        )

    if modulo:
        registros = registros.filter(
            modulo=modulo
        )

    registros = registros.order_by('-fecha')

    paginator = Paginator(
        registros,
        20
    )

    page_obj = paginator.get_page(
        request.GET.get('page')
    )

    # --- CORRECCIÓN AQUÍ ---
    # 1. Excluimos posibles valores nulos o vacíos.
    # 2. Forzamos order_by('modulo') para anular el orden por fecha de la base de datos
    #    y de paso ordenar los módulos alfabéticamente en tu desplegable.
    modulos = Bitacora.objects.exclude(
        modulo__isnull=True
    ).exclude(
        modulo=""
    ).order_by(
        'modulo'
    ).values_list(
        'modulo',
        flat=True
    ).distinct()
    # -----------------------

    return render(
        request,
        'auditoria/index.html',
        {
            'page_obj': page_obj,
            'query': query,
            'modulo_actual': modulo,
            'modulos': modulos
        }
    )