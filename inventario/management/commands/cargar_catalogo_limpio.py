import csv
import os
from django.core.management.base import BaseCommand
from django.conf import settings
from django.db import transaction

from inventario.models import (
    Material, PartidaPresupuestaria, UnidadMedida, 
    InventarioAlmacen, MovimientoInventario,
    NotaIngreso, NotaIngresoDetalle, NotaSalida, NotaSalidaDetalle,
    Transferencia, TransferenciaDetalle, AsignacionEntradaUnidad,
    AsignacionMaterialUnidad, ReporteCierreAuditado
)
from solicitudes.models import Solicitud, DetalleSolicitud


class Command(BaseCommand):
    help = "Carga o actualiza el catálogo oficial de materiales desde un CSV."

    def add_arguments(self, parser):
        parser.add_argument(
            '--archivo',
            type=str,
            default='catalogo_materiales.csv',
            help='Nombre del archivo CSV en la raíz del proyecto'
        )
        parser.add_argument(
            '--actualizar',
            action='store_true',
            help='Modo seguro/incremental: No borra datos, solo crea los materiales nuevos y actualiza nombres/unidades existentes.'
        )

    def handle(self, *args, **options):
        archivo_nombre = options['archivo']
        modo_actualizar = options['actualizar']
        ruta_csv = os.path.join(settings.BASE_DIR, archivo_nombre)

        if not os.path.exists(ruta_csv):
            self.stdout.write(self.style.ERROR(f"No se encontró el archivo: {ruta_csv}"))
            return

        with transaction.atomic():
            # ========================================================
            # MODO 1: LIMPIEZA COMPLETA (Solo si NO se usa --actualizar)
            # ========================================================
            if not modo_actualizar:
                self.stdout.write(self.style.WARNING("=== MODO RESET: LIMPIANDO DEPENDENCIAS Y CATÁLOGO ==="))
                
                AsignacionEntradaUnidad.objects.all().delete()
                if AsignacionMaterialUnidad:
                    AsignacionMaterialUnidad.objects.all().delete()

                NotaSalidaDetalle.objects.all().delete()
                NotaSalida.objects.all().delete()
                DetalleSolicitud.objects.all().delete()
                Solicitud.objects.all().delete()

                TransferenciaDetalle.objects.all().delete()
                Transferencia.objects.all().delete()
                MovimientoInventario.objects.all().delete()

                NotaIngresoDetalle.objects.all().delete()
                NotaIngreso.objects.all().delete()
                ReporteCierreAuditado.objects.all().delete()

                InventarioAlmacen.objects.all().delete()

                cant_borrados = Material.objects.count()
                Material.objects.all().delete()
                self.stdout.write(self.style.SUCCESS(f"✔ Se eliminaron {cant_borrados} materiales anteriores."))
            else:
                self.stdout.write(self.style.NOTICE("=== MODO ACTUALIZACIÓN INCREMENTAL: SIN BORRAR MOVIMIENTOS ==="))

            # ========================================================
            # PROCESAMIENTO DEL ARCHIVO CSV
            # ========================================================
            self.stdout.write(self.style.NOTICE(f"Leyendo archivo: {archivo_nombre}"))
            
            encoding = 'utf-8-sig'
            try:
                with open(ruta_csv, mode='r', encoding=encoding) as f:
                    muestra = f.read(2048)
            except UnicodeDecodeError:
                encoding = 'latin-1'

            with open(ruta_csv, mode='r', encoding=encoding) as f:
                delimitador = ';' if ';' in muestra else ','
                lector = csv.DictReader(f, delimiter=delimitador)
                lector.fieldnames = [c.strip().lower() for c in lector.fieldnames]

                creados = 0
                actualizados = 0
                omitidos = 0

                for fila in lector:
                    codigo = fila.get('codigo_sabs') or fila.get('codigo') or fila.get('cod_sabs') or ''
                    codigo = codigo.strip()

                    nombre = fila.get('descripcion') or fila.get('nombre') or fila.get('material') or ''
                    nombre = nombre.strip().upper()

                    partida_cod = fila.get('partida_codigo') or fila.get('partida') or fila.get('partida_cod') or ''
                    partida_cod = partida_cod.strip()
                    partida_nom = fila.get('partida_nombre') or f"Partida {partida_cod}"

                    u_med = fila.get('unidad_medida') or fila.get('unidad') or 'PIEZA'
                    u_med = u_med.strip().upper()

                    if not codigo or not nombre:
                        omitidos += 1
                        continue

                    # Partida
                    partida_obj = None
                    if partida_cod:
                        partida_obj, _ = PartidaPresupuestaria.objects.get_or_create(
                            codigo=partida_cod,
                            defaults={'nombre': partida_nom.strip().upper()}
                        )

                    # Unidad de Medida
                    sigla_um = u_med[:10]
                    um_obj, _ = UnidadMedida.objects.get_or_create(
                        codigo=sigla_um,
                        defaults={'nombre': u_med, 'is_active': True}
                    )

                    # Búsqueda o creación inteligente (Upsert)
                    material, created = Material.objects.get_or_create(
                        codigo=codigo,
                        defaults={
                            'nombre': nombre,
                            'partida': partida_obj,
                            'unidad_medida': u_med,
                            'unidad_medida_fk': um_obj,
                            'stock_actual': 0,
                            'stock_minimo': 5,
                            'is_active': True
                        }
                    )

                    if created:
                        creados += 1
                    else:
                        # Si ya existía, solo actualizamos metadatos si cambiaron, sin tocar stock
                        material.nombre = nombre
                        material.partida = partida_obj
                        material.unidad_medida = u_med
                        material.unidad_medida_fk = um_obj
                        material.save()
                        actualizados += 1

            self.stdout.write(self.style.SUCCESS(
                f"\n✔ ¡Operación completada con éxito!\n"
                f"- Materiales nuevos creados: {creados}\n"
                f"- Materiales existentes actualizados: {actualizados}\n"
                f"- Filas omitidas: {omitidos}"
            ))