from datetime import date, datetime, timezone
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.db import transaction

from organizacion.models import UnidadOrganizacional
from inventario.models import (
    Almacen, Material, InventarioAlmacen, MovimientoInventario,
    Proveedor, NotaIngreso, NotaIngresoDetalle, NotaSalida, NotaSalidaDetalle
)
from solicitudes.models import Solicitud, DetalleSolicitud
from presupuestos.models import POA, DetalleProgramacionPOA


class Command(BaseCommand):
    help = "Simula movimientos de compras (entradas) y salidas de almacén desde Enero 2026 para Sistemas. Incluye flag --revertir."

    def add_arguments(self, parser):
        parser.add_argument(
            '--revertir',
            action='store_true',
            help='Revierte y elimina todos los movimientos, solicitudes y notas simuladas, restaurando stocks.',
        )

    def handle(self, *args, **options):
        revertir = options['revertir']
        TAG_PRUEBA = "TEST26"

        if revertir:
            self.stdout.write(self.style.WARNING("=== REVIRTIENDO PRUEBA REMOTA (Eliminando simulación 2026) ==="))
            with transaction.atomic():
                # 1. Eliminar Notas de Salida y restaurar consumo en POA
                salidas = NotaSalida.objects.filter(nro_nota__startswith=TAG_PRUEBA)
                for sal in salidas:
                    for det in sal.detalles.all():
                        # Restaurar Detalle POA
                        item_poa = DetalleProgramacionPOA.objects.filter(
                            poa__unidad=sal.unidad_destino,
                            poa__gestion=2026,
                            material=det.material
                        ).first()
                        if item_poa:
                            item_poa.cantidad_consumida = max(0, item_poa.cantidad_consumida - det.cantidad)
                            item_poa.save()

                        # Restaurar Techo POA
                        poa = POA.objects.filter(unidad=sal.unidad_destino, partida=det.material.partida, gestion=2026).first()
                        if poa:
                            poa.monto_ejecutado = max(Decimal('0.00'), poa.monto_ejecutado - det.costo_total_real)
                            poa.monto_disponible += det.costo_total_real
                            poa.save()

                salidas.delete()

                # 2. Eliminar Solicitudes de prueba
                Solicitud.objects.filter(codigo__startswith=TAG_PRUEBA).delete()

                # 3. Eliminar Notas de Ingreso y Movimientos de Inventario
                NotaIngreso.objects.filter(nro_nota__startswith=TAG_PRUEBA).delete()
                MovimientoInventario.objects.filter(referencia__icontains=TAG_PRUEBA).delete()

                # 4. Recalcular stocks en InventarioAlmacen y Material
                for mat in Material.objects.all():
                    inv = InventarioAlmacen.objects.filter(material=mat).first()
                    if inv:
                        inv.stock_fisico = 0
                        inv.stock_reservado = 0
                        inv.save()
                    mat.stock_actual = 0
                    mat.save()

                # 5. Borrar proveedor de prueba
                Proveedor.objects.filter(nit="1020304050-SIM").delete()

            self.stdout.write(self.style.SUCCESS("✔ Prueba revertida exitosamente. La base de datos ha vuelto a su estado original."))
            return

        # ========================================================
        # EJECUCIÓN DE LA SIMULACIÓN
        # ========================================================
        self.stdout.write(self.style.NOTICE("=== GENERANDO MOVIMIENTOS HISTÓRICOS DESDE ENERO 2026 ==="))

        with transaction.atomic():
            # 1. Obtener usuario admin
            usuario = User.objects.filter(username="admin").first() or User.objects.filter(is_superuser=True).first()
            if not usuario:
                self.stdout.write(self.style.ERROR("No se encontró el usuario 'admin'."))
                return

            # 2. Obtener Almacén Central
            almacen = Almacen.objects.filter(nombre__icontains="Central").first() or Almacen.objects.first()
            if not almacen:
                almacen = Almacen.objects.create(nombre="Almacen Central", tipo='CENTRAL')

            # 3. Obtener Unidad de Sistemas
            unidad_sistemas = UnidadOrganizacional.objects.filter(nombre__icontains="Sistemas").first()
            if not unidad_sistemas:
                self.stdout.write(self.style.ERROR("No se encontró la unidad 'Sistemas'."))
                return

            # 4. Proveedor oficial de la compra
            proveedor, _ = Proveedor.objects.get_or_create(
                nit="1020304050-SIM",
                defaults={
                    "razon_social": "DISTRIBUIDORA POTOSÍ CENTRAL S.R.L.",
                    "telefono": "62-24589",
                    "direccion": "Av. Murillo Nro 450, Potosí"
                }
            )

            # ----------------------------------------------------
            # FASE 1: ENERO 2026 - ADQUISICIÓN / ENTRADA GENERAL
            # ----------------------------------------------------
            fecha_ingreso = date(2026, 1, 15)
            nota_ingreso = NotaIngreso.objects.create(
                nro_nota=f"{TAG_PRUEBA}-ING-001",
                proveedor=proveedor,
                almacen_destino=almacen,
                c31="C31-2026-000458",
                nota_entrega="NE-8954",
                factura="F-12048",
                fecha=fecha_ingreso,
                usuario=usuario
            )

            # Lotes a comprar
            compras = [
                ('Papel', 15, Decimal('350.00')),
                ('Bolígrafo negro', 60, Decimal('5.00')),
                ('Bolígrafo azul', 60, Decimal('5.00')),
                ('Paños', 20, Decimal('15.00')),
                ('Lavandina', 20, Decimal('15.00')),
                ('Cable de red UTP Cat. 6', 4, Decimal('1500.00')),
                ('Conectores RJ 45', 6, Decimal('250.00')),
                ('Disco duro para servidor', 4, Decimal('3800.00')),
            ]

            materiales_comprados = {}

            for term, cant, precio in compras:
                mat = Material.objects.filter(nombre__icontains=term).first()
                if not mat:
                    continue

                materiales_comprados[term] = mat
                total_linea = Decimal(cant) * precio

                # Detalle Nota Ingreso
                NotaIngresoDetalle.objects.create(
                    nota_ingreso=nota_ingreso,
                    material=mat,
                    cantidad=cant,
                    precio_unitario=precio,
                    precio_total=total_linea
                )

                # Stock físico por Almacén
                inv, _ = InventarioAlmacen.objects.get_or_create(material=mat, almacen=almacen)
                stock_ant = inv.stock_fisico
                inv.stock_fisico += cant
                inv.save()

                # Kardex y Lote PEPS
                mov = MovimientoInventario.objects.create(
                    material=mat,
                    almacen=almacen,
                    tipo='ENTRADA',
                    cantidad=cant,
                    costo_unitario=precio,
                    costo_total=total_linea,
                    stock_anterior=stock_ant,
                    stock_resultante=inv.stock_fisico,
                    referencia=f"{TAG_PRUEBA} Ingreso Fact. 12048 C-31",
                    usuario=usuario,
                    saldo_disponible_lote=cant
                )
                MovimientoInventario.objects.filter(id=mov.id).update(fecha=datetime(2026, 1, 15, 10, 30, tzinfo=timezone.utc))

            self.stdout.write(self.style.SUCCESS("✔ [15/01/2026] Entrada de almacén y lotes PEPS registrados."))

            # ----------------------------------------------------
            # FASE 2: MARZO 2026 - PRIMERA SALIDA PARA SISTEMAS
            # (Útiles de oficina y limpieza)
            # ----------------------------------------------------
            fecha_salida_1 = date(2026, 3, 10)

            sol_1 = Solicitud.objects.create(
                codigo=f"{TAG_PRUEBA}-SOL-001",
                unidad_solicitante=unidad_sistemas,
                solicitante=usuario,
                fecha=fecha_salida_1,
                justificacion="Requerimiento de materiales de oficina y limpieza para el primer trimestre de Sistemas.",
                estado='ENTREGADA',
                aprobado_por="Lic. Flora Colque Calizaya (Jefa Financiera)",
                flujo_atencion='SALIDA_ALMACEN',
                tipo_requerimiento='BIEN',
                entregado_por=usuario,
                fecha_entrega=datetime(2026, 3, 10, 15, 0, tzinfo=timezone.utc),
                cerrado_por=usuario,
                fecha_cierre=datetime(2026, 3, 10, 16, 0, tzinfo=timezone.utc)
            )

            nota_salida_1 = NotaSalida.objects.create(
                nro_nota=f"{TAG_PRUEBA}-SAL-001",
                solicitud_origen=sol_1,
                almacen_origen=almacen,
                unidad_destino=unidad_sistemas,
                fecha=fecha_salida_1,
                usuario=usuario
            )

            items_despacho_1 = [
                ('Papel', 2),
                ('Bolígrafo negro', 6),
                ('Bolígrafo azul', 6),
                ('Paños', 4),
                ('Lavandina', 2),
            ]

            self._procesar_despacho(items_despacho_1, materiales_comprados, sol_1, nota_salida_1, almacen, unidad_sistemas, usuario, fecha_salida_1, TAG_PRUEBA)
            self.stdout.write(self.style.SUCCESS("✔ [10/03/2026] Despacho Nro 001 procesado y descontado del POA."))

            # ----------------------------------------------------
            # FASE 3: JUNIO 2026 - SEGUNDA SALIDA PARA SISTEMAS
            # (Material técnico, cableado estructurado y servidores)
            # ----------------------------------------------------
            fecha_salida_2 = date(2026, 6, 18)

            sol_2 = Solicitud.objects.create(
                codigo=f"{TAG_PRUEBA}-SOL-002",
                unidad_solicitante=unidad_sistemas,
                solicitante=usuario,
                fecha=fecha_salida_2,
                justificacion="Materiales para mantenimiento del cableado de red y ampliación de almacenamiento de servidores.",
                estado='ENTREGADA',
                aprobado_por="Lic. Flora Colque Calizaya (Jefa Financiera)",
                flujo_atencion='SALIDA_ALMACEN',
                tipo_requerimiento='BIEN',
                entregado_por=usuario,
                fecha_entrega=datetime(2026, 6, 18, 11, 30, tzinfo=timezone.utc),
                cerrado_por=usuario,
                fecha_cierre=datetime(2026, 6, 18, 12, 0, tzinfo=timezone.utc)
            )

            nota_salida_2 = NotaSalida.objects.create(
                nro_nota=f"{TAG_PRUEBA}-SAL-002",
                solicitud_origen=sol_2,
                almacen_origen=almacen,
                unidad_destino=unidad_sistemas,
                fecha=fecha_salida_2,
                usuario=usuario
            )

            items_despacho_2 = [
                ('Cable de red UTP Cat. 6', 1),
                ('Conectores RJ 45', 2),
                ('Disco duro para servidor', 1),
            ]

            self._procesar_despacho(items_despacho_2, materiales_comprados, sol_2, nota_salida_2, almacen, unidad_sistemas, usuario, fecha_salida_2, TAG_PRUEBA)
            self.stdout.write(self.style.SUCCESS("✔ [18/06/2026] Despacho Nro 002 (Redes y Servidores) procesado."))

        self.stdout.write(self.style.SUCCESS(
            "\n¡Simulación cronológica completada con éxito!\n"
            "Puedes revisar el kardex, los reportes y los porcentajes de ejecución del POA 2026.\n"
            "Si deseas revertirlo en cualquier momento, ejecuta: python manage.py simular_movimientos_2026 --revertir"
        ))

    def _procesar_despacho(self, items, materiales_map, solicitud, nota_salida, almacen, unidad, usuario, fecha_mov, tag):
        for term, cant_salida in items:
            mat = materiales_map.get(term)
            if not mat:
                continue

            inv = InventarioAlmacen.objects.filter(material=mat, almacen=almacen).first()
            if not inv or inv.stock_fisico < cant_salida:
                continue

            lote = MovimientoInventario.objects.filter(
                material=mat,
                almacen=almacen,
                tipo='ENTRADA',
                saldo_disponible_lote__gt=0
            ).order_by('fecha').first()

            costo_unitario = lote.costo_unitario if lote else Decimal('10.00')
            costo_total = Decimal(cant_salida) * costo_unitario

            if lote:
                lote.saldo_disponible_lote -= cant_salida
                lote.save()

            DetalleSolicitud.objects.create(
                solicitud=solicitud,
                material=mat,
                partida=mat.partida,
                cantidad_solicitada=cant_salida,
                cantidad_aprobada=cant_salida,
                cantidad_entregada=cant_salida,
                precio_unitario_referencial=costo_unitario
            )

            NotaSalidaDetalle.objects.create(
                nota_salida=nota_salida,
                material=mat,
                cantidad=cant_salida,
                costo_unitario_real=costo_unitario,
                costo_total_real=costo_total
            )

            stock_ant = inv.stock_fisico
            inv.stock_fisico -= cant_salida
            inv.save()

            mov = MovimientoInventario.objects.create(
                material=mat,
                almacen=almacen,
                tipo='SALIDA',
                cantidad=cant_salida,
                costo_unitario=costo_unitario,
                costo_total=costo_total,
                stock_anterior=stock_ant,
                stock_resultante=inv.stock_fisico,
                referencia=f"{tag} Salida a {unidad.nombre}",
                usuario=usuario,
                unidad_destino=unidad
            )
            MovimientoInventario.objects.filter(id=mov.id).update(fecha=datetime(fecha_mov.year, fecha_mov.month, fecha_mov.day, 16, 0, tzinfo=timezone.utc))

            det_poa = DetalleProgramacionPOA.objects.filter(
                poa__unidad=unidad,
                poa__gestion=2026,
                material=mat
            ).first()
            if det_poa:
                det_poa.cantidad_consumida += cant_salida
                det_poa.save()

            if mat.partida:
                poa = POA.objects.filter(unidad=unidad, partida=mat.partida, gestion=2026).first()
                if poa:
                    poa.monto_disponible -= costo_total
                    poa.monto_ejecutado += costo_total
                    poa.save()