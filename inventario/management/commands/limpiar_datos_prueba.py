from django.core.management.base import BaseCommand
from django.db import transaction

from inventario.models import (
    Material, MovimientoInventario, InventarioAlmacen,
    NotaIngreso, NotaIngresoDetalle, NotaSalida, NotaSalidaDetalle,
    Transferencia, TransferenciaDetalle, Proveedor,
    AsignacionMaterialUnidad, ReporteCierreAuditado
)
from solicitudes.models import Solicitud, DetalleSolicitud
from presupuestos.models import DetalleProgramacionPOA, ModificacionPresupuestaria, POA


class Command(BaseCommand):
    help = "Limpia movimientos de prueba, solicitudes, filas de POA y materiales duplicados con prefijo MAT-."

    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING("=== INICIANDO LIMPIEZA DE BASE DE DATOS ==="))

        with transaction.atomic():
            # 1. Eliminar Notas de Salida y sus detalles
            cant_salidas = NotaSalida.objects.count()
            NotaSalidaDetalle.objects.all().delete()
            NotaSalida.objects.all().delete()
            self.stdout.write(f"✔ Notas de salida eliminadas: {cant_salidas}")

            # 2. Eliminar Solicitudes y sus detalles
            cant_solicitudes = Solicitud.objects.count()
            DetalleSolicitud.objects.all().delete()
            Solicitud.objects.all().delete()
            self.stdout.write(f"✔ Solicitudes de pedido eliminadas: {cant_solicitudes}")

            # 3. Eliminar Transferencias entre almacenes
            cant_transf = Transferencia.objects.count()
            TransferenciaDetalle.objects.all().delete()
            Transferencia.objects.all().delete()
            self.stdout.write(f"✔ Transferencias eliminadas: {cant_transf}")

            # 4. Eliminar Notas de Ingreso y sus detalles
            cant_ingresos = NotaIngreso.objects.count()
            NotaIngresoDetalle.objects.all().delete()
            NotaIngreso.objects.all().delete()
            self.stdout.write(f"✔ Notas de ingreso eliminadas: {cant_ingresos}")

            # 5. Eliminar Movimientos de Kardex (Lotes PEPS)
            cant_movs = MovimientoInventario.objects.count()
            MovimientoInventario.objects.all().delete()
            self.stdout.write(f"✔ Movimientos históricos de inventario eliminados: {cant_movs}")

            # 6. Eliminar Asignaciones previas y reportes auditados de prueba
            AsignacionMaterialUnidad.objects.all().delete()
            ReporteCierreAuditado.objects.all().delete()
            self.stdout.write("✔ Asignaciones previas y reportes de prueba limpiados.")

            # 7. IMPORTANTE: Eliminar DetalleProgramacionPOA (libera los materiales protegidos)
            cant_det_poa = DetalleProgramacionPOA.objects.count()
            DetalleProgramacionPOA.objects.all().delete()
            ModificacionPresupuestaria.objects.all().delete()
            POA.objects.all().delete()
            self.stdout.write(f"✔ Filas de DetalleProgramacionPOA y POAs eliminados: {cant_det_poa}")

            # 8. Eliminar Materiales duplicados o residuales con prefijo 'MAT-'
            materiales_mat = Material.objects.filter(codigo__startswith='MAT-')
            cant_mat = materiales_mat.count()
            materiales_mat.delete()
            self.stdout.write(f"✔ Materiales duplicados (prefijo MAT-) eliminados: {cant_mat}")

            # 9. Resetear existencias físicas a 0 en los materiales legítimos del catálogo oficial
            InventarioAlmacen.objects.update(stock_fisico=0, stock_reservado=0)
            Material.objects.update(stock_actual=0)
            self.stdout.write("✔ Stocks físicos de estantería reseteados a 0.")

            # 10. Limpiar proveedores de prueba
            Proveedor.objects.filter(nit__icontains="SIM").delete()
            self.stdout.write("✔ Proveedores ficticios eliminados.")

        self.stdout.write(self.style.SUCCESS(
            "\n¡Limpieza completada al 100%!\n"
            "Tu base de datos mantiene tus usuarios, almacenes, unidades y el catálogo oficial limpio."
        ))