from decimal import Decimal
from datetime import date, datetime, timedelta
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.utils import timezone
from django.db import transaction

from organizacion.models import Secretaria, UnidadOrganizacional
from inventario.models import (
    Almacen, PartidaPresupuestaria, UnidadMedida, Material,
    InventarioAlmacen, Proveedor, NotaIngreso, NotaIngresoDetalle,
    AsignacionEntradaUnidad, NotaSalida, NotaSalidaDetalle, MovimientoInventario
)
from solicitudes.models import Solicitud, DetalleSolicitud
from compras.models import (
    AutoridadInstitucional, ProcesoAdquisicion, ProcesoItemDetalle
)


class Command(BaseCommand):
    help = 'Genera datos específicos para detonar alertas tempranas, notificaciones y reportes DGCF.'

    def handle(self, *args, **kwargs):
        self.stdout.write(self.style.WARNING('Iniciando simulación de alertas y reportes para Gestión 2026...'))

        with transaction.atomic():
            hoy = timezone.now().date()
            ahora = timezone.now()

            # 1. Recuperar entidades básicas
            alm_central = Almacen.objects.filter(tipo='CENTRAL').first()
            if not alm_central:
                self.stdout.write(self.style.ERROR('Error: No se encontró Almacén Central. Ejecute primero "poblar_datos_oficiales".'))
                return

            u_contrataciones = UnidadOrganizacional.objects.filter(nombre__icontains='Contrataciones').first()
            u_sistemas = UnidadOrganizacional.objects.filter(nombre__icontains='Sistemas').first()
            u_archivo = UnidadOrganizacional.objects.filter(nombre__icontains='Archivo').first()
            u_admin = UnidadOrganizacional.objects.filter(nombre__icontains='Administrativa').first()

            user_admin = User.objects.filter(username='emilia_admin').first() or User.objects.filter(is_superuser=True).first()
            user_marcos = User.objects.filter(username='marcos_solicitante').first() or user_admin
            user_mario = User.objects.filter(username='mario_almacen').first() or user_admin
            aut_rpa = AutoridadInstitucional.objects.filter(cargo='RPA').first()
            aut_saf = AutoridadInstitucional.objects.filter(cargo='SAF').first()
            prov = Proveedor.objects.first()

            p321 = PartidaPresupuestaria.objects.filter(codigo='32100').first()
            p391 = PartidaPresupuestaria.objects.filter(codigo='39100').first()
            p395 = PartidaPresupuestaria.objects.filter(codigo='39500').first()
            p398 = PartidaPresupuestaria.objects.filter(codigo='39800').first()
            um_pza = UnidadMedida.objects.filter(codigo='PIEZA').first() or UnidadMedida.objects.first()

            # =========================================================================
            # A. DETONAR ALERTAS DE STOCK MÍNIMO Y CADUCIDAD
            # =========================================================================
            # Material 1: Con stock crítico (Stock actual = 2, Mínimo = 10)
            mat_toner, _ = Material.objects.get_or_create(
                codigo='39800-005',
                defaults={
                    'nombre': 'TONER LASERJET NEGRO DE ALTO RENDIMIENTO',
                    'partida': p398,
                    'unidad_medida_fk': um_pza,
                    'unidad_medida': 'PIEZA',
                    'stock_actual': 2,
                    'stock_minimo': 10,
                    'is_active': True
                }
            )
            inv_toner, _ = InventarioAlmacen.objects.get_or_create(material=mat_toner, almacen=alm_central, defaults={'stock_fisico': 2, 'stock_reservado': 0})
            inv_toner.stock_fisico = 2
            inv_toner.save()

            # Material 2: Perecedero con Lote por vencer en 12 días
            mat_alcohol, _ = Material.objects.get_or_create(
                codigo='39100-088',
                defaults={
                    'nombre': 'ALCOHOL EN GEL ANTIBACTERIAL 500ML',
                    'partida': p391,
                    'unidad_medida_fk': um_pza,
                    'unidad_medida': 'FRASCO',
                    'stock_actual': 30,
                    'stock_minimo': 5,
                    'is_active': True
                }
            )
            inv_alc, _ = InventarioAlmacen.objects.get_or_create(material=mat_alcohol, almacen=alm_central, defaults={'stock_fisico': 30, 'stock_reservado': 0})
            inv_alc.stock_fisico = 30
            inv_alc.save()

            # Movimiento con lote por vencer (12 días)
            MovimientoInventario.objects.get_or_create(
                material=mat_alcohol,
                referencia='INGRESO LOTE P12 - ALCOHOL GEL',
                defaults={
                    'almacen': alm_central,
                    'tipo': 'ENTRADA',
                    'cantidad': 30,
                    'costo_unitario': Decimal('18.00'),
                    'costo_total': Decimal('540.00'),
                    'stock_anterior': 0,
                    'stock_resultante': 30,
                    'saldo_disponible_lote': 30,
                    'fecha': ahora - timedelta(days=60),
                    'fecha_vencimiento': hoy + timedelta(days=12),  # <-- ALERTA CADUCIDAD EN 12 DÍAS
                    'usuario': user_admin
                }
            )

            # Material 3: Lote ya vencido (hace 4 días)
            mat_reactivo, _ = Material.objects.get_or_create(
                codigo='39100-099',
                defaults={
                    'nombre': 'PASTILLAS CLORADAS PARA TANQUE DE AGUA',
                    'partida': p391,
                    'unidad_medida_fk': um_pza,
                    'unidad_medida': 'CAJA',
                    'stock_actual': 10,
                    'stock_minimo': 2,
                    'is_active': True
                }
            )
            inv_rea, _ = InventarioAlmacen.objects.get_or_create(material=mat_reactivo, almacen=alm_central, defaults={'stock_fisico': 10, 'stock_reservado': 0})
            inv_rea.stock_fisico = 10
            inv_rea.save()

            MovimientoInventario.objects.get_or_create(
                material=mat_reactivo,
                referencia='INGRESO LOTE CLORO VENCIDO',
                defaults={
                    'almacen': alm_central,
                    'tipo': 'ENTRADA',
                    'cantidad': 10,
                    'costo_unitario': Decimal('25.00'),
                    'costo_total': Decimal('250.00'),
                    'stock_anterior': 0,
                    'stock_resultante': 10,
                    'saldo_disponible_lote': 10,
                    'fecha': ahora - timedelta(days=180),
                    'fecha_vencimiento': hoy - timedelta(days=4),  # <-- VENCIDO HACE 4 DÍAS
                    'usuario': user_admin
                }
            )

            # =========================================================================
            # B. NOTIFICACIONES FLUJO 1 (PEDIDOS DE ALMACÉN)
            # =========================================================================
            # 1. Pedido Listo en Ventanilla (Para que Marcos vea campana activa)
            sol_lista, _ = Solicitud.objects.get_or_create(
                codigo='PED-00010',
                defaults={
                    'unidad_solicitante': u_contrataciones,
                    'solicitante': user_marcos,
                    'fecha': hoy - timedelta(days=1),
                    'justificacion': 'Material de oficina urgente para licitaciones públicas',
                    'tipo_requerimiento': 'BIEN',
                    'flujo_atencion': 'SALIDA_ALMACEN',
                    'estado': 'PREPARADA',  # <-- ACTIVA CAMPANA DE SOLICITANTE Y ALMACENERO
                    'aprobado_por': 'Emilia Saavedra',
                    'revisado_por': user_admin,
                    'fecha_revision': ahora - timedelta(hours=6),
                    'preparado_por': user_mario,
                    'fecha_preparado': ahora - timedelta(hours=2),
                }
            )
            DetalleSolicitud.objects.get_or_create(solicitud=sol_lista, material=mat_toner, defaults={'cantidad_solicitada': 2, 'cantidad_aprobada': 2, 'precio_unitario_referencial': Decimal('450.00')})

            # 2. Pedido Rechazado con motivo
            sol_rech, _ = Solicitud.objects.get_or_create(
                codigo='PED-00012',
                defaults={
                    'unidad_solicitante': u_contrataciones,
                    'solicitante': user_marcos,
                    'fecha': hoy - timedelta(days=2),
                    'justificacion': 'Solicitud extraordinaria de insumos no programados',
                    'tipo_requerimiento': 'BIEN',
                    'flujo_atencion': 'SALIDA_ALMACEN',
                    'estado': 'RECHAZADA',  # <-- AVISO DE OBSERVACIÓN EN CAMPANA
                    'motivo_rechazo': 'Cantidad solicitada excede la asignación física disponible para su unidad.'
                }
            )

            # =========================================================================
            # C. NOTIFICACIONES FLUJO 2 (COMPRAS MENORES SABS)
            # =========================================================================
            # 1. Para Presupuestos: Esperando Certificación
            adq_presup, _ = ProcesoAdquisicion.objects.get_or_create(
                codigo='ADQ-2026-0010',
                defaults={
                    'tipo': 'BIEN',
                    'unidad_solicitante': u_sistemas,
                    'solicitante': user_admin,
                    'objeto_contratacion': 'ADQUISICION DE DISCOS SOLIDOS SSD PARA SERVIDORES CENTRALES',
                    'justificacion': 'Renovación de discos duros con fallas de almacenamiento en el Datacenter',
                    'cite_solicitud': 'CITE-SIS-098/2026',
                    'cite_nota_saf': 'CITE-SAF-180/2026',
                    'autoridad_saf': aut_saf,
                    'autoridad_rpa': aut_rpa,
                    'programa': '000',
                    'proyecto_actividad': '001',
                    'fuente_financiamiento': '20',
                    'organismo_financiador': '220',
                    'estado': 'NOTA_SAF_EMITIDA'  # <-- ACTIVA CAMPANA DE PRESUPUESTOS
                }
            )
            ProcesoItemDetalle.objects.get_or_create(proceso=adq_presup, descripcion='DISCO SOLIDO SSD 1TB ENTERPRISE', unidad_medida='PIEZA', cantidad=4, precio_referencial_estimado=Decimal('620.00'), partida=p398)

            # 2. Para RPA: Esperando Aprobación de Inicio
            adq_rpa, _ = ProcesoAdquisicion.objects.get_or_create(
                codigo='ADQ-2026-0011',
                defaults={
                    'tipo': 'BIEN',
                    'unidad_solicitante': u_archivo,
                    'solicitante': user_admin,
                    'objeto_contratacion': 'ADQUISICION DE ESTANTERIAS METALICAS Y CAJAS DE ARCHIVO',
                    'justificacion': 'Resguardo de legajos documentales de la gestión 2026',
                    'cite_solicitud': 'CITE-ARCH-044/2026',
                    'cite_nota_saf': 'CITE-SAF-185/2026',
                    'certificacion_nro': 'CP-2026-0450',
                    'monto_certificado_bs': Decimal('4800.00'),
                    'autoridad_saf': aut_saf,
                    'autoridad_rpa': aut_rpa,
                    'programa': '000',
                    'proyecto_actividad': '001',
                    'fuente_financiamiento': '20',
                    'organismo_financiador': '220',
                    'estado': 'CERTIFICADA'  # <-- ACTIVA CAMPANA DEL RPA
                }
            )
            ProcesoItemDetalle.objects.get_or_create(proceso=adq_rpa, descripcion='ESTANTERIA METALICA DE 5 BANDEJAS REFORZADA', unidad_medida='PIEZA', cantidad=6, precio_referencial_estimado=Decimal('800.00'), partida=p395, monto_certificado=Decimal('4800.00'))

            # 3. Para Bienes y Servicios: Esperando Orden de Compra
            adq_bbss, _ = ProcesoAdquisicion.objects.get_or_create(
                codigo='ADQ-2026-0012',
                defaults={
                    'tipo': 'BIEN',
                    'unidad_solicitante': u_admin,
                    'solicitante': user_admin,
                    'objeto_contratacion': 'ADQUISICION DE DISPENSADORES DE AGUA Y BIDONES',
                    'justificacion': 'Suministro de agua potable para salas de espera y atención ciudadana',
                    'cite_solicitud': 'CITE-DA-155/2026',
                    'cite_nota_saf': 'CITE-SAF-190/2026',
                    'certificacion_nro': 'CP-2026-0480',
                    'monto_certificado_bs': Decimal('1950.00'),
                    'cite_aprobacion_rpa': 'RES-RPA-088/2026',
                    'autoridad_saf': aut_saf,
                    'autoridad_rpa': aut_rpa,
                    'programa': '000',
                    'proyecto_actividad': '001',
                    'fuente_financiamiento': '20',
                    'organismo_financiador': '220',
                    'estado': 'APROBADA_RPA'  # <-- ACTIVA CAMPANA DE BIENES Y SERVICIOS
                }
            )
            ProcesoItemDetalle.objects.get_or_create(proceso=adq_bbss, descripcion='DISPENSADOR DE AGUA FRIO/CALIENTE DE PEDESTAL', unidad_medida='PIEZA', cantidad=3, precio_referencial_estimado=Decimal('650.00'), partida=p391, monto_certificado=Decimal('1950.00'))

            # =========================================================================
            # D. MOVIMIENTOS HISTÓRICOS DE CONSUMO (PARA REPORTES DGCF Y CONSUMO)
            # =========================================================================
            # Consumo de Sistemas (Salida de 15 Botes de Lavandina en Febrero 2026)
            MovimientoInventario.objects.get_or_create(
                referencia='DESPACHO DE PRUEBA: SIS-CONSUMO-01',
                defaults={
                    'material': Material.objects.filter(codigo='39100-013').first(),
                    'almacen': alm_central,
                    'tipo': 'SALIDA',
                    'cantidad': 15,
                    'costo_unitario': Decimal('14.00'),
                    'costo_total': Decimal('210.00'),
                    'stock_anterior': 50,
                    'stock_resultante': 35,
                    'saldo_disponible_lote': 0,
                    'fecha': datetime(2026, 2, 20, 11, 0, 0, tzinfo=timezone.get_current_timezone()),
                    'usuario': user_mario,
                    'unidad_destino': u_sistemas
                }
            )

            # Consumo de Archivo (Salida de 10 paquetes de papel en Marzo 2026)
            mat_papel = Material.objects.filter(codigo='32100-002').first()
            if mat_papel:
                MovimientoInventario.objects.get_or_create(
                    referencia='DESPACHO DE PRUEBA: ARCH-CONSUMO-01',
                    defaults={
                        'material': mat_papel,
                        'almacen': alm_central,
                        'tipo': 'SALIDA',
                        'cantidad': 10,
                        'costo_unitario': Decimal('32.50'),
                        'costo_total': Decimal('325.00'),
                        'stock_anterior': 180,
                        'stock_resultante': 170,
                        'saldo_disponible_lote': 0,
                        'fecha': datetime(2026, 3, 5, 15, 30, 0, tzinfo=timezone.get_current_timezone()),
                        'usuario': user_mario,
                        'unidad_destino': u_archivo
                    }
                )

        self.stdout.write(self.style.SUCCESS('\n================================================================='))
        self.stdout.write(self.style.SUCCESS('¡DATOS DE ALERTAS, NOTIFICACIONES Y REPORTES CARGADOS EXITOSAMENTE!'))
        self.stdout.write(self.style.SUCCESS('================================================================='))
        self.stdout.write(self.style.SUCCESS('🚨 ALERTAS DISPARADAS EN EL DASHBOARD:'))
        self.stdout.write(self.style.SUCCESS(' • Stock Crítico: Toner LaserJet (Quedan 2 u., mínimo 10)'))
        self.stdout.write(self.style.SUCCESS(' • Lote por Vencer: Alcohol en Gel vence en 12 días'))
        self.stdout.write(self.style.SUCCESS(' • Lote Vencido: Pastillas Cloradas venció hace 4 días'))
        self.stdout.write(self.style.SUCCESS('-----------------------------------------------------------------'))
        self.stdout.write(self.style.SUCCESS('🔔 NOTIFICACIONES EN LA CAMPANA DEL NAVBAR:'))
        self.stdout.write(self.style.SUCCESS(' • marcos_solicitante -> Verá campana activa (Pedido PED-00010 listo en ventanilla y PED-00012 observado).'))
        self.stdout.write(self.style.SUCCESS(' • mario_almacen      -> Verá campana activa (Ventanilla en espera y alertas de stock bajo).'))
        self.stdout.write(self.style.SUCCESS(' • pedro_presupuestos -> Verá notificación urgente para certificar ADQ-2026-0010.'))
        self.stdout.write(self.style.SUCCESS(' • juan_rpa           -> Verá notificación para aprobar inicio de ADQ-2026-0011.'))
        self.stdout.write(self.style.SUCCESS(' • ana_bbss           -> Verá notificación para emitir orden de ADQ-2026-0012.'))
        self.stdout.write(self.style.SUCCESS('-----------------------------------------------------------------'))
        self.stdout.write(self.style.SUCCESS('📊 REPORTES CON DATOS ENRIQUECIDOS:'))
        self.stdout.write(self.style.SUCCESS(' • Hub de Reportes (/inventario/reportes/): R1.05 y R1.06 con múltiples partidas.'))
        self.stdout.write(self.style.SUCCESS(' • Consumo por Dependencias: Con consumos reales de Sistemas, Archivo y Contrataciones.'))
        self.stdout.write(self.style.SUCCESS('=================================================================\n'))