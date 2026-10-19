from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.utils import timezone
from datetime import datetime

from .models import Bitacora
from usuarios.decorators import rol_requerido
from usuarios.models import User


@login_required
@rol_requerido(['ADMINISTRADOR'])
def auditoria_list(request):
    query = request.GET.get('q', '').strip()
    modulo_filtro = request.GET.get('modulo', '').strip()
    usuario_filtro = request.GET.get('usuario', '').strip()
    desde_str = request.GET.get('desde', '').strip()
    hasta_str = request.GET.get('hasta', '').strip()

    logs_qs = Bitacora.objects.select_related('usuario').all()

    if query:
        logs_qs = logs_qs.filter(
            Q(accion__icontains=query) |
            Q(descripcion__icontains=query) |
            Q(usuario__username__icontains=query)
        )
    if modulo_filtro:
        logs_qs = logs_qs.filter(modulo__iexact=modulo_filtro)
    if usuario_filtro:
        logs_qs = logs_qs.filter(usuario_id=usuario_filtro)
    if desde_str:
        logs_qs = logs_qs.filter(fecha__gte=desde_str)
    if hasta_str:
        logs_qs = logs_qs.filter(fecha__lte=hasta_str)

    logs_qs = logs_qs.order_by('-id')

    # Estadísticas para las 4 tarjetas superiores (usando 'bitacora' en singular)
    total_registros = Bitacora.objects.count()
    total_usuarios = User.objects.filter(bitacora__isnull=False).distinct().count()
    total_modulos = Bitacora.objects.values('modulo').distinct().count()
    
    hoy = timezone.now().date()
    eventos_hoy = Bitacora.objects.filter(fecha=hoy).count()

    modulos_disponibles = Bitacora.objects.values_list('modulo', flat=True).distinct().order_by('modulo')
    usuarios_disponibles = User.objects.filter(bitacora__isnull=False).distinct().order_by('username')

    paginator = Paginator(logs_qs, 25)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'auditoria/index.html', {
        'page_obj': page_obj,
        'query': query,
        'modulo_filtro': modulo_filtro,
        'usuario_filtro': usuario_filtro,
        'desde': desde_str,
        'hasta': hasta_str,
        'total_logs': total_registros,
        'total_registros': total_registros,
        'total_usuarios': total_usuarios,
        'total_modulos': total_modulos,
        'eventos_hoy': eventos_hoy,
        'modulos_disponibles': modulos_disponibles,
        'usuarios_disponibles': usuarios_disponibles,
    })


@login_required
@rol_requerido(['ADMINISTRADOR'])
def exportar_logs_excel(request):
    import csv
    from django.http import HttpResponse

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="bitacora_auditoria_sga_{datetime.now().strftime("%Y%m%d_%H%M")}.csv"'
    response.write('\ufeff'.encode('utf8'))

    writer = csv.writer(response, delimiter=';')
    writer.writerow(['ID', 'FECHA', 'USUARIO', 'MÓDULO', 'ACCIÓN', 'DESCRIPCIÓN'])

    logs = Bitacora.objects.select_related('usuario').order_by('-id')[:1000]
    for log in logs:
        writer.writerow([
            log.id,
            log.fecha.strftime('%d/%m/%Y %H:%M:%S') if hasattr(log.fecha, 'strftime') else log.fecha,
            log.usuario.username if log.usuario else 'Sistema',
            log.modulo,
            log.accion,
            log.descripcion
        ])

    return response


@login_required
@rol_requerido(['ADMINISTRADOR'])
def purgar_logs_antiguos(request):
    import csv
    from django.http import HttpResponse
    from django.contrib import messages

    if request.method == 'POST':
        accion_tipo = request.POST.get('accion_purga')
        
        if accion_tipo == 'exportar_y_limpiar_1000':
            logs_a_borrar = Bitacora.objects.order_by('id')[:1000]
            count = logs_a_borrar.count()
            
            if count == 0:
                messages.warning(request, "No hay registros en la bitácora para purgar.")
                return redirect('auditoria_list')

            response = HttpResponse(content_type='text/csv; charset=utf-8')
            response['Content-Disposition'] = f'attachment; filename="historico_respaldo_1000_logs_{datetime.now().strftime("%Y%m%d")}.csv"'
            response.write('\ufeff'.encode('utf8'))

            writer = csv.writer(response, delimiter=';')
            writer.writerow(['ID', 'FECHA', 'USUARIO', 'MÓDULO', 'ACCIÓN', 'DESCRIPCIÓN'])

            ids_a_eliminar = []
            for log in logs_a_borrar:
                writer.writerow([
                    log.id, 
                    log.fecha.strftime('%d/%m/%Y %H:%M:%S') if hasattr(log.fecha, 'strftime') else log.fecha,
                    log.usuario.username if log.usuario else 'Sistema',
                    log.modulo, log.accion, log.descripcion
                ])
                ids_a_eliminar.append(log.id)

            Bitacora.objects.filter(id__in=ids_a_eliminar).delete()

            Bitacora.objects.create(
                usuario=request.user,
                modulo='Auditoría',
                accion='Purgar y Descargar Logs Históricos',
                descripcion=f'Se descargaron y eliminaron los {count} registros más antiguos de la bitácora.'
            )
            return response

        elif accion_tipo == 'vaciar_todo':
            total = Bitacora.objects.count()
            Bitacora.objects.all().delete()
            messages.success(request, f"Se han depurado y eliminado los {total} registros de la bitácora correctamente.")
            return redirect('auditoria_list')

    return redirect('auditoria_list')