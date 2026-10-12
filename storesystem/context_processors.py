from inventario.models import Almacen

def theme_processor(request):
    """
    Inyecta la preferencia de tema (claro u oscuro) guardada en la cookie del navegador.
    """
    theme = request.COOKIES.get('theme', 'light')
    return {
        'theme': theme
    }


def almacen_context(request):
    """
    Inyecta dinámicamente en todas las plantillas el almacén activo y autorizado
    para el usuario que ha iniciado sesión.
    """
    if not request.user.is_authenticated:
        return {}

    perfil = getattr(request.user, 'perfilusuario', None)
    if not perfil:
        return {}

    almacen_usuario = None
    almacenes_disponibles = []
    es_admin_almacenes = perfil.rol in ['ADMINISTRADOR', 'ADMIN_ALMACENES'] or request.user.is_superuser

    if es_admin_almacenes:
        almacenes_disponibles = Almacen.objects.filter(is_active=True).order_by('nombre')
        almacen_usuario = almacenes_disponibles.filter(tipo='CENTRAL').first() or almacenes_disponibles.first()
    else:
        almacenes_autorizados = perfil.almacenes_autorizados.filter(is_active=True).order_by('nombre')
        if almacenes_autorizados.exists():
            almacenes_disponibles = almacenes_autorizados
            almacen_usuario = almacenes_autorizados.first()
        else:
            almacen_a_cargo = Almacen.objects.filter(responsable=request.user, is_active=True).first()
            if almacen_a_cargo:
                almacen_usuario = almacen_a_cargo
                almacenes_disponibles = [almacen_a_cargo]

    return {
        'almacen_usuario': almacen_usuario,
        'almacenes_disponibles_global': almacenes_disponibles,
        'tiene_multiples_almacenes': len(almacenes_disponibles) > 1,
        'es_admin_almacenes': es_admin_almacenes,
    }