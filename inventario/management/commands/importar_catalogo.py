import csv
import os
from django.core.management.base import BaseCommand
from django.conf import settings
from inventario.models import Material, PartidaPresupuestaria, UnidadMedida, Almacen, InventarioAlmacen

class Command(BaseCommand):
    help = 'Vincula los materiales a sus partidas y al Almacén Central'

    def handle(self, *args, **kwargs):
        archivo_path = os.path.join(settings.BASE_DIR, 'catalogo_materiales.csv')

        if not os.path.exists(archivo_path):
            self.stdout.write(self.style.ERROR(f'No se encontró el archivo: {archivo_path}'))
            return

        # Almacén Central (ID 1)
        almacen_central = Almacen.objects.filter(tipo='CENTRAL').first() or Almacen.objects.filter(id=1).first()

        # Usamos utf-8-sig para ignorar automáticamente el BOM de Windows
        with open(archivo_path, mode='r', encoding='utf-8-sig') as f:
            # Detecta si el CSV usa comas o punto y coma
            primera_linea = f.readline()
            delimitador = ';' if ';' in primera_linea else ','
            f.seek(0)

            reader = csv.DictReader(f, delimiter=delimitador)
            procesados = 0

            for fila in reader:
                # Limpiar nombres de columnas y valores por si hay espacios
                fila_limpia = {k.strip(): (v.strip() if v else '') for k, v in fila.items() if k}

                cod_partida = fila_limpia.get('partida_codigo', '')
                nom_partida = fila_limpia.get('partida_nombre', '')
                cod_sabs = fila_limpia.get('codigo_sabs', '')
                desc = fila_limpia.get('descripcion', '')
                u_medida = fila_limpia.get('unidad_medida', '')

                if not cod_sabs or not cod_partida:
                    continue

                # 1. Partida
                partida_obj, _ = PartidaPresupuestaria.objects.get_or_create(
                    codigo=cod_partida,
                    defaults={'nombre': nom_partida}
                )

                # 2. Unidad de medida
                unidad_obj = None
                if u_medida:
                    unidad_obj, _ = UnidadMedida.objects.get_or_create(
                        codigo=u_medida,
                        defaults={'nombre': u_medida}
                    )

                # 3. Asignar la Partida al Material
                mat, creado = Material.objects.get_or_create(
                    codigo=cod_sabs,
                    defaults={'nombre': desc, 'descripcion': desc}
                )
                mat.partida = partida_obj
                mat.nombre = desc
                mat.descripcion = desc
                mat.unidad_medida = u_medida
                mat.unidad_medida_fk = unidad_obj
                mat.is_active = True
                mat.save()

                # 4. Vincular al Almacén Central
                if almacen_central:
                    InventarioAlmacen.objects.get_or_create(
                        almacen=almacen_central,
                        material=mat
                    )

                procesados += 1

        sin_partida = Material.objects.filter(partida__isnull=True).count()
        self.stdout.write(self.style.SUCCESS(f'¡Éxito! Se procesaron {procesados} materiales.'))
        self.stdout.write(self.style.SUCCESS(f'Materiales sin partida restantes: {sin_partida}'))