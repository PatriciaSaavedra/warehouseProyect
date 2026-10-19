from datetime import date, datetime, timezone
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.db import transaction

from organizacion.models import Secretaria, UnidadOrganizacional
from inventario.models import (
    Almacen, Material, InventarioAlmacen, MovimientoInventario,
    Proveedor, NotaIngreso, NotaIngresoDetalle,
    NotaSalida, NotaSalidaDetalle, AsignacionEntradaUnidad
)
from solicitudes.models import Solicitud, DetalleSolicitud


class Command(BaseCommand):
    help = "Genera datos de prueba realistas para el Flujo 1 (Entradas con Asignación Directa, Kárdex PEPS y Pedidos)."

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("=== GENERANDO DATOS DE PRUEBA PARA EL FLUJO 1 ==="))

        with transaction.atomic():
            # 1. Usuario responsable
            usuario = User.objects.filter(username="admin").first() or User.objects.filter(is_superuser=True).first()
            if not usuario:
                self.stdout.write(self.style.ERROR("No se encontró el usuario 'admin'."))
                return

            # 2. Almacén Central
            almacen = Almacen.objects.filter(tipo='CENTRAL', is_active=True).first()
            if not almacen:
                almacen = Almacen.objects.create(nombre="Almacén Central", tipo='CENTRAL', responsable=usuario)
            self.stdout.write(f"✔ Almacén de operaciones: {almacen.nombre}")

            # 3. Secretarías y Unidades Organizacionales
            secretaria, _ = Secretaria.objects.get_or_create(
                nombre="SECRETARIA DEPTAL. ADMINISTRATIVA Y FINANCIERA",
                defaults={"codigo": "SDAF", "is_active": True}
            )

            u_sistemas, _ = UnidadOrganizacional.objects.get_or_create(
                nombre="Sistemas",
                defaults={"secretaria": secretaria, "is_active": True}
            )
            u_contrataciones, _ = UnidadOrganizacional.objects.get_or_create(
                nombre="Contrataciones",
                defaults={"secretaria": secretaria, "is_active": True}
            )
            u_archivo, _ = UnidadOrganizacional.objects.get_or_create(
                nombre="Archivo",
                defaults={"secretaria": secretaria, "is_active": True}
            )

            # 4. Proveedores locales
            prov_papel, _ = Proveedor.objects.get_or_create(
                nit="1024589023",
                defaults={
                    "razon_social": "DISTRIBUIDORA Y LIBRERÍA POTOSÍ S.R.L.",
                    "telefono": "62-24589",
                    "direccion": "Calle Bolivar N° 142, Potosí"
                }
            )
            prov_limpieza, _ = Proveedor.objects.get_or_create(
                nit="5896231018",
                defaults={
                    "razon_social": "COMERCIAL ANDINA DE LIMPIEZA",
                    "telefono": "62-28954",
                    "direccion": "Av. Universitaria N° 580, Potosí"
                }
            )

            # 5. Obtener materiales del catálogo (buscando por coincidencia)
            def get_mat(busqueda, cod_fallback, nom_fallback):
                m = Material.objects.filter(nombre__icontains=busqueda, is_active=True).first()
                if not m:
                    m = Material.objects.filter(codigo__icontains=busqueda, is_active=True).first()
                if not m:
                    m = Material.objects.create(
                        codigo=cod_fallback,
                        nombre=nom_fallback,
                        unidad_medida="PAQUETE",
                        stock_actual=0,
                        is_active=True
                    )
                return m

            mat_papel_carta = get_mat("PAPEL BOND TAMAÑO CARTA", "32100-002", "PAPEL BOND TAMAÑO CARTA COLOR BLANCO")
            mat_papel_oficio = get_mat("PAPEL BOND TAMAÑO OFICIO", "32100-001", "PAPEL BOND TAMAÑO OFICIO COLOR BLANCO")
            mat_boligrafo_azul = get_mat("BOLIGRAFO COLOR AZUL", "39500-007", "BOLIGRAFO COLOR AZUL")
            mat_boligrafo_negro = get_mat("BOLIGRAFO COLOR NEGRO", "39500-008", "BOLIGRAFO COLOR NEGRO")
            mat_lavandina = get_mat("LAVANDINA DE 1 LITRO", "39100-013", "LAVANDINA DE 1 LITRO")
            mat_panos = get_mat("PAÑOS PARA DESEMPOLVAR", "39100-021", "PAÑOS PARA DESEMPOLVAR")

            # ========================================================
            # ENTRADA 1: COMPRA DE PAPEL Y ESCRITORIO (Enero 2026)
            # ========================================================
            fecha_ingreso_1 = date(2026, 1, 20)
            nota_1 = NotaIngreso.objects.create(
                nro_nota="NI-2026-001",
                proveedor=prov_papel,
                almacen_destino=almacen,
                c31="2026-C31-000214",
                factura="F-1580",
                nota_entrega="REM-890",
                nro_orden_compra="COMPRA 045/2026",
                fecha=fecha_ingreso_1,
                usuario=usuario,
                observaciones="ADQUISICION ANUAL DE MATERIAL DE ESCRITORIO PARA REPARTICIONES DE LA GOBERNACION"
            )

            # Detalle 1.1: Papel Bond Carta (100 paq. a Bs. 32.50)
            # Reparto: 50 a Sistemas, 30 a Contrataciones, 20 a Archivo
            det_1_1 = NotaIngresoDetalle.objects.create(
                nota_ingreso=nota_1,
                material=mat_papel_carta,
                cantidad=100,
                precio_unitario=Decimal("32.50"),
                precio_total=Decimal("3250.00")
            )
            AsignacionEntradaUnidad.objects.create(nota_ingreso_detalle=det_1_1, unidad_organizacional=u_sistemas, cantidad_asignada=50, cantidad_retirada=0)
            AsignacionEntradaUnidad.objects.create(nota_ingreso_detalle=det_1_1, unidad_organizacional=u_contrataciones, cantidad_asignada=30, cantidad_retirada=0)
            AsignacionEntradaUnidad.objects.create(nota_ingreso_detalle=det_1_1, unidad_organizacional=u_archivo, cantidad_asignada=20, cantidad_retirada=0)

            inv_1, _ = InventarioAlmacen.objects.get_or_create(material=mat_papel_carta, almacen=almacen)
            inv_1.stock_fisico += 100
            inv_1.save()

            mov_1 = MovimientoInventario.objects.create(
                material=mat_papel_carta,
                almacen=almacen,
                tipo='ENTRADA',
                cantidad=100,
                costo_unitario=Decimal("32.50"),
                costo_total=Decimal("3250.00"),
                stock_anterior=0,
                stock_resultante=inv_1.stock_fisico,
                saldo_disponible_lote=100,
                referencia="INGRESO NOTA NI-2026-001 (COMPRA 045/2026)",
                usuario=usuario
            )
            MovimientoInventario.objects.filter(id=mov_1.id).update(fecha=datetime(2026, 1, 20, 9, 30, tzinfo=timezone.utc))

            # Detalle 1.2: Bolígrafos Azules (120 u. a Bs. 4.50)
            # Reparto: 60 a Sistemas, 40 a Contrataciones, 20 libre almacén
            det_1_2 = NotaIngresoDetalle.objects.create(
                nota_ingreso=nota_1,
                material=mat_boligrafo_azul,
                cantidad=120,
                precio_unitario=Decimal("4.50"),
                precio_total=Decimal("540.00")
            )
            AsignacionEntradaUnidad.objects.create(nota_ingreso_detalle=det_1_2, unidad_organizacional=u_sistemas, cantidad_asignada=60, cantidad_retirada=0)
            AsignacionEntradaUnidad.objects.create(nota_ingreso_detalle=det_1_2, unidad_organizacional=u_contrataciones, cantidad_asignada=40, cantidad_retirada=0)

            inv_2, _ = InventarioAlmacen.objects.get_or_create(material=mat_boligrafo_azul, almacen=almacen)
            inv_2.stock_fisico += 120
            inv_2.save()

            mov_2 = MovimientoInventario.objects.create(
                material=mat_boligrafo_azul,
                almacen=almacen,
                tipo='ENTRADA',
                cantidad=120,
                costo_unitario=Decimal("4.50"),
                costo_total=Decimal("540.00"),
                stock_anterior=0,
                stock_resultante=inv_2.stock_fisico,
                saldo_disponible_lote=120,
                referencia="INGRESO NOTA NI-2026-001 (COMPRA 045/2026)",
                usuario=usuario
            )
            MovimientoInventario.objects.filter(id=mov_2.id).update(fecha=datetime(2026, 1, 20, 10, 0, tzinfo=timezone.utc))

            # ========================================================
            # ENTRADA 2: COMPRA DE LIMPIEZA (Marzo 2026)
            # ========================================================
            fecha_ingreso_2 = date(2026, 3, 5)
            nota_2 = NotaIngreso.objects.create(
                nro_nota="NI-2026-002",
                proveedor=prov_limpieza,
                almacen_destino=almacen,
                c31="2026-C31-000850",
                factura="F-3420",
                nro_orden_compra="COMPRA 082/2026",
                fecha=fecha_ingreso_2,
                usuario=usuario,
                observaciones="PROVISION DE INSUMOS DE HIGIENE PARA EDIFICIO CENTRAL"
            )

            # Detalle 2.1: Lavandina 1L (50 botes a Bs. 14.00)
            det_2_1 = NotaIngresoDetalle.objects.create(
                nota_ingreso=nota_2,
                material=mat_lavandina,
                cantidad=50,
                precio_unitario=Decimal("14.00"),
                precio_total=Decimal("700.00")
            )
            AsignacionEntradaUnidad.objects.create(nota_ingreso_detalle=det_2_1, unidad_organizacional=u_sistemas, cantidad_asignada=15, cantidad_retirada=0)
            AsignacionEntradaUnidad.objects.create(nota_ingreso_detalle=det_2_1, unidad_organizacional=u_contrataciones, cantidad_asignada=10, cantidad_retirada=0)

            inv_3, _ = InventarioAlmacen.objects.get_or_create(material=mat_lavandina, almacen=almacen)
            inv_3.stock_fisico += 50
            inv_3.save()

            mov_3 = MovimientoInventario.objects.create(
                material=mat_lavandina,
                almacen=almacen,
                tipo='ENTRADA',
                cantidad=50,
                costo_unitario=Decimal("14.00"),
                costo_total=Decimal("700.00"),
                stock_anterior=0,
                stock_resultante=inv_3.stock_fisico,
                saldo_disponible_lote=50,
                referencia="INGRESO NOTA NI-2026-002 (COMPRA 082/2026)",
                usuario=usuario
            )
            MovimientoInventario.objects.filter(id=mov_3.id).update(fecha=datetime(2026, 3, 5, 11, 0, tzinfo=timezone.utc))

            # Detalle 2.2: Paños para desempolvar (40 u. a Bs. 15.00)
            det_2_2 = NotaIngresoDetalle.objects.create(
                nota_ingreso=nota_2,
                material=mat_panos,
                cantidad=40,
                precio_unitario=Decimal("15.00"),
                precio_total=Decimal("600.00")
            )
            AsignacionEntradaUnidad.objects.create(nota_ingreso_detalle=det_2_2, unidad_organizacional=u_sistemas, cantidad_asignada=10, cantidad_retirada=0)

            inv_4, _ = InventarioAlmacen.objects.get_or_create(material=mat_panos, almacen=almacen)
            inv_4.stock_fisico += 40
            inv_4.save()

            mov_4 = MovimientoInventario.objects.create(
                material=mat_panos,
                almacen=almacen,
                tipo='ENTRADA',
                cantidad=40,
                costo_unitario=Decimal("15.00"),
                costo_total=Decimal("600.00"),
                stock_anterior=0,
                stock_resultante=inv_4.stock_fisico,
                saldo_disponible_lote=40,
                referencia="INGRESO NOTA NI-2026-002 (COMPRA 082/2026)",
                usuario=usuario
            )
            MovimientoInventario.objects.filter(id=mov_4.id).update(fecha=datetime(2026, 3, 5, 11, 30, tzinfo=timezone.utc))

            self.stdout.write(self.style.SUCCESS("✔ 2 Notas de Ingreso registradas con asignaciones a Sistemas, Contrataciones y Archivo."))

            # ========================================================
            # PEDIDO 1 (HISTÓRICO ENTREGADO): Sistemas pide papel y bolígrafos en Abril
            # ========================================================
            sol_entregada = Solicitud.objects.create(
                codigo="PED-00001",
                unidad_solicitante=u_sistemas,
                solicitante=usuario,
                fecha=date(2026, 4, 15),
                justificacion="Requerimiento de papel y bolígrafos para soporte técnico y atención al usuario",
                tipo_requerimiento="BIEN",
                flujo_atencion="SALIDA_ALMACEN",
                estado="ENTREGADA",
                aprobado_por="Ing. Carlos Mendoza (Jefe)",
                entregado_por=usuario,
                fecha_entrega=datetime(2026, 4, 16, 15, 30, tzinfo=timezone.utc)
            )

            DetalleSolicitud.objects.create(
                solicitud=sol_entregada,
                material=mat_papel_carta,
                cantidad_solicitada=15,
                cantidad_aprobada=15,
                cantidad_entregada=15,
                precio_unitario_referencial=Decimal("32.50")
            )
            DetalleSolicitud.objects.create(
                solicitud=sol_entregada,
                material=mat_boligrafo_azul,
                cantidad_solicitada=20,
                cantidad_aprobada=20,
                cantidad_entregada=20,
                precio_unitario_referencial=Decimal("4.50")
            )

            # Generar Nota de Salida oficial del Pedido 1
            nota_salida_1 = NotaSalida.objects.create(
                nro_nota="NS-00001",
                solicitud_origen=sol_entregada,
                almacen_origen=almacen,
                unidad_destino=u_sistemas,
                fecha=date(2026, 4, 16),
                usuario=usuario
            )
            NotaSalidaDetalle.objects.create(nota_salida=nota_salida_1, material=mat_papel_carta, cantidad=15, costo_unitario_real=Decimal("32.50"), costo_total_real=Decimal("487.50"))
            NotaSalidaDetalle.objects.create(nota_salida=nota_salida_1, material=mat_boligrafo_azul, cantidad=20, costo_unitario_real=Decimal("4.50"), costo_total_real=Decimal("90.00"))

            # Descontar del lote PEPS y del stock físico
            mov_1.saldo_disponible_lote -= 15
            mov_1.save()
            inv_1.stock_fisico -= 15
            inv_1.save()

            mov_2.saldo_disponible_lote -= 20
            mov_2.save()
            inv_2.stock_fisico -= 20
            inv_2.save()

            # Movimientos de salida en Kardex
            mov_sal_1 = MovimientoInventario.objects.create(
                material=mat_papel_carta, almacen=almacen, tipo='SALIDA',
                cantidad=15, costo_unitario=Decimal("32.50"), costo_total=Decimal("487.50"),
                stock_anterior=100, stock_resultante=85,
                referencia="DESPACHO: PED-00001", usuario=usuario, unidad_destino=u_sistemas
            )
            MovimientoInventario.objects.filter(id=mov_sal_1.id).update(fecha=datetime(2026, 4, 16, 16, 0, tzinfo=timezone.utc))

            mov_sal_2 = MovimientoInventario.objects.create(
                material=mat_boligrafo_azul, almacen=almacen, tipo='SALIDA',
                cantidad=20, costo_unitario=Decimal("4.50"), costo_total=Decimal("90.00"),
                stock_anterior=120, stock_resultante=100,
                referencia="DESPACHO: PED-00001", usuario=usuario, unidad_destino=u_sistemas
            )
            MovimientoInventario.objects.filter(id=mov_sal_2.id).update(fecha=datetime(2026, 4, 16, 16, 15, tzinfo=timezone.utc))

            # DESCUENTO DE CUPO ASIGNADO: Sistemas tenía 50 paq de papel y 60 bolígrafos
            asig_papel_sistemas = AsignacionEntradaUnidad.objects.filter(nota_ingreso_detalle=det_1_1, unidad_organizacional=u_sistemas).first()
            asig_papel_sistemas.cantidad_retirada = 15
            asig_papel_sistemas.save()

            asig_boli_sistemas = AsignacionEntradaUnidad.objects.filter(nota_ingreso_detalle=det_1_2, unidad_organizacional=u_sistemas).first()
            asig_boli_sistemas.cantidad_retirada = 20
            asig_boli_sistemas.save()

            self.stdout.write(self.style.SUCCESS("✔ PED-00001 (ENTREGADA): Salida por 15 paq papel y 20 bolígrafos completada."))

            # ========================================================
            # PEDIDO 2 (EN PREPARACIÓN): Contrataciones pide papel (Listo para Despacho Físico)
            # ========================================================
            sol_preparada = Solicitud.objects.create(
                codigo="PED-00002",
                unidad_solicitante=u_contrataciones,
                solicitante=usuario,
                fecha=date(2026, 9, 20),
                justificacion="Material para elaboración de carpetas y pliegos de contratación DBC",
                tipo_requerimiento="BIEN",
                flujo_atencion="SALIDA_ALMACEN",
                estado="PREPARADA",
                aprobado_por="Jefatura Administrativa",
                preparado_por=usuario,
                fecha_preparado=datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)
            )
            DetalleSolicitud.objects.create(
                solicitud=sol_preparada,
                material=mat_papel_carta,
                cantidad_solicitada=10,
                cantidad_aprobada=10,
                cantidad_entregada=0,
                precio_unitario_referencial=Decimal("32.50")
            )
            self.stdout.write(self.style.SUCCESS("✔ PED-00002 (PREPARADA): Lista para probar el botón 'Despacho Físico'."))

            # ========================================================
            # PEDIDO 3 (NUEVO REGISTRADO): Sistemas pide Lavandina y Paños (Listo para Autorizar)
            # ========================================================
            sol_registrada = Solicitud.objects.create(
                codigo="PED-00003",
                unidad_solicitante=u_sistemas,
                solicitante=usuario,
                fecha=date.today(),
                justificacion="Insumos de limpieza y mantenimiento para la sala de servidores y datacenter",
                tipo_requerimiento="BIEN",
                flujo_atencion="SALIDA_ALMACEN",
                estado="REGISTRADA"
            )
            DetalleSolicitud.objects.create(
                solicitud=sol_registrada,
                material=mat_lavandina,
                cantidad_solicitada=5,
                cantidad_aprobada=5,
                precio_unitario_referencial=Decimal("14.00")
            )
            DetalleSolicitud.objects.create(
                solicitud=sol_registrada,
                material=mat_panos,
                cantidad_solicitada=4,
                cantidad_aprobada=4,
                precio_unitario_referencial=Decimal("15.00")
            )
            self.stdout.write(self.style.SUCCESS("✔ PED-00003 (REGISTRADA): Creada para probar el botón 'Autorizar (Jefe)'."))

        self.stdout.write(self.style.SUCCESS(
            "\n¡POBLACIÓN DE DATOS DEL FLUJO 1 COMPLETADA CON ÉXITO!\n"
            "Estado de cupos en Sistemas:\n"
            "- Papel Carta: 35 disponibles (de 50 asignados originalmente)\n"
            "- Bolígrafos Azules: 40 disponibles (de 60 asignados)\n"
            "- Lavandina 1L: 15 disponibles\n"
            "- Paños: 10 disponibles\n"
        ))