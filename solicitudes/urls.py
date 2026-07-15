from django.urls import path
from . import views

urlpatterns = [
    path('', views.solicitudes, name='solicitudes'),
    path('nueva/', views.nueva_solicitud, name='nueva_solicitud'),
    path('buscar-materiales/', views.buscar_materiales, name='buscar_materiales'),
    path('detalle/<int:id>/', views.detalle_solicitud, name='detalle_solicitud'),
    
    # --- RUTA DE EDICIÓN AGREGADA ---
    path('editar/<int:id>/', views.editar_solicitud, name='editar_solicitud'),  # <-- ESTA LÍNEA FALTABA
    
    # --- RUTAS DINÁMICAS DEL NUEVO FLUJO ---
    path('revisar/<int:id>/', views.revisar_solicitud, name='revisar_solicitud'),
    path('aprobar/<int:id>/', views.aprobar_solicitud, name='aprobar_solicitud'),
    path('preparar/<int:id>/', views.preparar_solicitud, name='preparar_solicitud'),
    path('entregar/<int:id>/', views.entregar_solicitud, name='entregar_solicitud'),
    path('cerrar/<int:id>/', views.cerrar_solicitud, name='cerrar_solicitud'),
    path('rechazar/<int:id>/', views.rechazar_solicitud, name='rechazar_solicitud'),
    path('reabrir/<int:id>/', views.reabrir_solicitud, name='reabrir_solicitud'),
    
    # --- EXPORTACIÓN ---
    path('pdf/<int:id>/', views.solicitud_pdf, name='solicitud_pdf'),
]