import csv
from django.core.management.base import BaseCommand
from django.db import transaction
from organizacion.models import Secretaria, UnidadOrganizacional

class Command(BaseCommand):
    help = 'Importa la estructura de Secretarías y Unidades de Potosí desde un CSV'

    def add_arguments(self, parser):
        parser.add_argument('csv_file', type=str, help='Ruta al archivo CSV con los datos')

    def handle(self, *args, **options):
        file_path = options['csv_file']
        
        try:
            with open(file_path, newline='', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                secretarias_creadas = 0
                unidades_creadas = 0

                with transaction.atomic():
                    for row in reader:
                        sec_nombre = row['secretaria'].strip()
                        sec_codigo = row.get('secretaria_codigo', '').strip()
                        uni_nombre = row['unidad'].strip()
                        uni_sigep = row.get('codigo_sigep', '').strip()

                        # 1. Crear o buscar la secretaría correspondiente
                        secretaria, created = Secretaria.objects.get_or_create(
                            nombre=sec_nombre,
                            defaults={'codigo': sec_codigo if sec_codigo else None}
                        )
                        if created:
                            secretarias_creadas += 1

                        # 2. Crear o buscar la unidad asignándola a su secretaría
                        unidad, uni_created = UnidadOrganizacional.objects.get_or_create(
                            nombre=uni_nombre,
                            secretaria=secretaria,
                            defaults={'codigo_sigep': uni_sigep if uni_sigep else None}
                        )
                        if uni_created:
                            unidades_creadas += 1

                self.stdout.write(self.style.SUCCESS(
                    f"¡Importación exitosa! Secretarías creadas: {secretarias_creadas}, Unidades creadas: {unidades_creadas}."
                ))
                
        except FileNotFoundError:
            self.stdout.write(self.style.ERROR(f"El archivo en la ruta '{file_path}' no existe."))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Ocurrió un error al importar: {str(e)}"))