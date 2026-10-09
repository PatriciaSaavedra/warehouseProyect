from django.urls import path
from . import views

urlpatterns = [
    # BANDEJA Y PROCESOS DE ADQUISICIÓN (FLUJO 2)
    path('adquisiciones/', views.adquisiciones_list, name='adquisiciones_list'),
    path('adquisiciones/nueva/', views.crear_adquisicion, name='crear_adquisicion'),
    path('adquisiciones/<int:id>/', views.detalle_adquisicion, name='detalle_adquisicion'),

    # DOCUMENTOS OFICIALES IMPRIMIBLES
    path('adquisiciones/<int:id>/pdf/solicitud/', views.doc_solicitud_adquisicion_pdf, name='doc_solicitud_adquisicion_pdf'),
    path('adquisiciones/<int:id>/pdf/checklist/', views.doc_checklist_pdf, name='doc_checklist_pdf'),
    path('adquisiciones/<int:id>/pdf/nota-saf/', views.doc_nota_saf_pdf, name='doc_nota_saf_pdf'),
    path('adquisiciones/<int:id>/pdf/certificacion/', views.doc_certificacion_presupuestaria_pdf, name='doc_certificacion_presupuestaria_pdf'),
    path('adquisiciones/<int:id>/pdf/pedido/', views.doc_formulario_pedido_adq_pdf, name='doc_formulario_pedido_adq_pdf'),
    path('adquisiciones/<int:id>/pdf/autorizacion-rpa/', views.doc_autorizacion_rpa_pdf, name='doc_autorizacion_rpa_pdf'),

    # ACCIONES DE GUARDADO DE FORMULARIOS EDITABLES
    path('adquisiciones/<int:id>/actualizar-bbss/', views.actualizar_datos_solicitud_bbss, name='actualizar_datos_solicitud_bbss'),
    path('adquisiciones/<int:id>/actualizar-checklist/', views.actualizar_checklist_oficial, name='actualizar_checklist_oficial'),
    # GESTIÓN DE PROVEEDORES
    path('proveedores/', views.proveedores_list, name='proveedores_list'),
    path('proveedores/crear/', views.crear_proveedor, name='crear_proveedor'),
    path('proveedores/editar/<int:id>/', views.editar_proveedor, name='editar_proveedor'),

    # FASES DEL TRÁMITE SABS
    path('adquisiciones/<int:id>/emitir-saf/', views.emitir_nota_saf, name='emitir_nota_saf'),
    path('adquisiciones/<int:id>/certificar/', views.certificar_presupuesto_adquisicion, name='certificar_presupuesto_adquisicion'),
    path('adquisiciones/<int:id>/aprobar-rpa/', views.aprobar_rpa_adquisicion, name='aprobar_rpa_adquisicion'),

    path('adquisiciones/<int:id>/pdf/orden/', views.doc_orden_compra_pdf, name='doc_orden_compra_pdf'),
    path('adquisiciones/<int:id>/pdf/recepcion-doc/', views.doc_nota_recepcion_adq_pdf, name='doc_nota_recepcion_adq_pdf'),
    path('adquisiciones/<int:id>/emitir-orden/', views.emitir_orden_adquisicion, name='emitir_orden_adquisicion'),
    path('adquisiciones/<int:id>/recepcion-almacen/', views.recepcion_documental_almacen, name='recepcion_documental_almacen'),

    # AUTORIDADES INSTITUCIONALES (RPA / SAF)
    path('autoridades/', views.autoridades_list, name='autoridades_list'),
    path('autoridades/crear/', views.crear_autoridad, name='crear_autoridad'),
    path('autoridades/editar/<int:id>/', views.editar_autoridad, name='editar_autoridad'),
    path('autoridades/toggle/<int:id>/', views.toggle_autoridad, name='toggle_autoridad'),
]