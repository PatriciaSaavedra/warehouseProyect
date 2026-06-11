from django.urls import path
from . import views

urlpatterns = [

    path('', views.solicitudes, name='solicitudes'),

    path('nueva/', views.nueva_solicitud, name='nueva_solicitud'),

    path('<int:id>/', views.detalle_solicitud, name='detalle_solicitud'),

    path(
        'aprobar/<int:id>/',
        views.aprobar_solicitud,
        name='aprobar_solicitud'
    ),
    path(
    'entregar/<int:id>/',
    views.entregar_solicitud,
    name='entregar_solicitud'
    ),

    path(
        'rechazar/<int:id>/',
        views.rechazar_solicitud,
        name='rechazar_solicitud'
    ),
    path(
        'reabrir/<int:id>/',
        views.reabrir_solicitud,
        name='reabrir_solicitud'
    ),

]