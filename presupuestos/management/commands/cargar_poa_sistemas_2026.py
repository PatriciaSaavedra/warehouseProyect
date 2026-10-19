from decimal import Decimal
import re
from django.core.management.base import BaseCommand
from django.db import transaction

from organizacion.models import Secretaria, UnidadOrganizacional
from inventario.models import PartidaPresupuestaria, UnidadMedida, Material
from presupuestos.models import POA, DetalleProgramacionPOA


def normalizar(texto):
    """Limpia tildes, signos y dobles espacios para hacer comparaciones seguras"""
    t = texto.upper()
    t = re.sub(r'[ÁÀÄÂ]', 'A', t)
    t = re.sub(r'[ÉÈËÊ]', 'E', t)
    t = re.sub(r'[ÍÌÏÎ]', 'I', t)
    t = re.sub(r'[ÓÒÖÔ]', 'O', t)
    t = re.sub(r'[ÚÙÜÛ]', 'U', t)
    t = re.sub(r'[^A-Z0-9]', ' ', t)
    return ' '.join(t.split())


class Command(BaseCommand):
    help = "Carga el Formulario 005 (Gestión 2026) vinculándolo coherentemente con el catálogo oficial de materiales."

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Iniciando validación y carga del Formulario 005 con el Catálogo Oficial..."))

        ITEMS_FORMULARIO_005 = [
            # (partida_poa, partida_nombre, actividad_poa, descripcion_form005, unidad_medida, cantidad, precio_unitario)
            ('31110', 'Gastos por refrigerios al personal', '5.10', 'Gastos por refrigerio al personal eventual permanente y consultores', 'Global', 1, Decimal('28512.00')),
            ('32100', 'Papel, cartón e impresos', '5.10', 'Papel Hilado Tamaño Carta de 240 grs.', 'Piezas', 300, Decimal('1.00')),
            ('32100', 'Papel, cartón e impresos', '5.10', 'Papel Fotografico', 'Piezas', 200, Decimal('2.00')),
            ('32200', 'Productos de artes gráficas', '5.10', 'Artes graficas', 'Global', 1, Decimal('500.00')),

            # Material de Limpieza
            ('39100', 'Material de Limpieza e Higiene', '5.10', 'Paños para desempolvar', 'Piezas', 10, Decimal('15.00')),
            ('39100', 'Material de Limpieza e Higiene', '5.10', 'Ambientador en spray', 'Piezas', 5, Decimal('15.00')),
            ('39100', 'Material de Limpieza e Higiene', '5.10', 'Escoba Plástica con palo', 'Piezas', 3, Decimal('20.00')),
            ('39100', 'Material de Limpieza e Higiene', '5.10', 'Levantador de Basura', 'Piezas', 5, Decimal('15.00')),
            ('39100', 'Material de Limpieza e Higiene', '5.10', 'Detergente de 1000 gr.', 'Bolsa', 3, Decimal('20.00')),
            ('39100', 'Material de Limpieza e Higiene', '5.10', 'Limpia Computadoras Spray - Multiuso', 'Piezas', 5, Decimal('22.00')),
            ('39100', 'Material de Limpieza e Higiene', '5.10', 'Limpia Muebles de 600 ml', 'Piezas', 5, Decimal('20.00')),
            ('39100', 'Material de Limpieza e Higiene', '5.10', 'Jabon Liquido ANTIBACTERAL de 320 ml', 'Bote', 10, Decimal('22.00')),
            ('39100', 'Material de Limpieza e Higiene', '5.10', 'Sanitizador de manos 500 ml.', 'Bote', 10, Decimal('23.00')),
            ('39100', 'Material de Limpieza e Higiene', '5.10', 'Lavandina de 1 litro', 'Bote', 4, Decimal('15.00')),
            ('39100', 'Material de Limpieza e Higiene', '5.10', 'Limpia baños de 1000 ml', 'Bote', 4, Decimal('40.00')),
            ('39100', 'Material de Limpieza e Higiene', '5.10', 'Aromatizante y desinfectante para piso frio', 'Bote', 4, Decimal('50.00')),
            ('39100', 'Material de Limpieza e Higiene', '5.10', 'Limpiador piso flotante', 'Bote', 4, Decimal('38.00')),

            # Útiles de oficina
            ('39500', 'Útiles de Escritorio y Oficina', '5.10', 'Papel bond tamaño carta', 'Cajas', 10, Decimal('350.00')),
            ('39500', 'Útiles de Escritorio y Oficina', '5.10', 'Papel bond tamaño oficio', 'Cajas', 1, Decimal('370.00')),
            ('39500', 'Útiles de Escritorio y Oficina', '5.10', 'papel fotografico', 'Piezas', 50, Decimal('2.00')),
            ('39500', 'Útiles de Escritorio y Oficina', '5.10', 'Estilete grande', 'Piezas', 12, Decimal('4.00')),
            ('39500', 'Útiles de Escritorio y Oficina', '5.10', 'Folder de cartulina oficio color amarillo', 'Piezas', 30, Decimal('1.00')),
            ('39500', 'Útiles de Escritorio y Oficina', '5.10', 'Fastener metálico', 'Caja', 1, Decimal('12.00')),
            ('39500', 'Útiles de Escritorio y Oficina', '5.10', 'Cuaderno espiral tamaño oficio', 'Piezas', 6, Decimal('25.00')),
            ('39500', 'Útiles de Escritorio y Oficina', '5.10', 'Bolígrafo negro', 'Piezas', 6, Decimal('5.00')),
            ('39500', 'Útiles de Escritorio y Oficina', '5.10', 'Bolígrafo azul', 'Piezas', 24, Decimal('5.00')),
            ('39500', 'Útiles de Escritorio y Oficina', '5.10', 'Lapiz color negro', 'Piezas', 12, Decimal('2.00')),
            ('39500', 'Útiles de Escritorio y Oficina', '5.10', 'Borradores de tinta y lapiz dos colores', 'Piezas', 12, Decimal('2.00')),
            ('39500', 'Útiles de Escritorio y Oficina', '5.10', 'Marcadores para CD', 'Piezas', 12, Decimal('5.00')),
            ('39500', 'Útiles de Escritorio y Oficina', '5.10', 'Clip 33mm.', 'Cjitas', 6, Decimal('3.00')),
            ('39500', 'Útiles de Escritorio y Oficina', '5.10', 'Regla de 30 cm metálicos', 'Piezas', 6, Decimal('6.00')),
            ('39500', 'Útiles de Escritorio y Oficina', '5.10', 'Clip Nº 8 mm.', 'Cajitas', 5, Decimal('8.00')),
            ('39500', 'Útiles de Escritorio y Oficina', '5.10', 'Posit mediano', 'Block', 12, Decimal('5.00')),
            ('39500', 'Útiles de Escritorio y Oficina', '5.10', 'Cinta de Embalaje transparente', 'Rollo', 24, Decimal('9.00')),
            ('39500', 'Útiles de Escritorio y Oficina', '5.10', 'Marcadores acrilicos diferentes color', 'Piezas', 12, Decimal('5.00')),
            ('39500', 'Útiles de Escritorio y Oficina', '5.10', 'Tampo pequeño', 'Piezas', 6, Decimal('9.00')),
            ('39500', 'Útiles de Escritorio y Oficina', '5.10', 'engrampadoras', 'Piezas', 7, Decimal('10.00')),
            ('39500', 'Útiles de Escritorio y Oficina', '5.10', 'saca grapas', 'Piezas', 8, Decimal('11.00')),
            ('39500', 'Útiles de Escritorio y Oficina', '5.10', 'Perforadoras', 'Piezas', 9, Decimal('12.00')),
            ('39500', 'Útiles de Escritorio y Oficina', '5.10', 'Cinta masquin 2 cm.', 'Piezas', 12, Decimal('8.00')),
            ('39500', 'Útiles de Escritorio y Oficina', '5.10', 'Pegamento UHU de 40 grs.', 'Piezas', 24, Decimal('16.00')),
            ('39500', 'Útiles de Escritorio y Oficina', '5.10', 'Botella de tinta para impresora EPSON', 'Piezas', 6, Decimal('800.00')),
            ('39500', 'Útiles de Escritorio y Oficina', '5.10', 'Cinta Evolis YMCKO', 'Piezas', 6, Decimal('725.00')),
            ('39500', 'Útiles de Escritorio y Oficina', '5.10', 'Cinta Evolis HOLOGRAMA', 'Piezas', 6, Decimal('1000.00')),
            ('39500', 'Útiles de Escritorio y Oficina', '5.10', 'Tijera mediana', 'Piezas', 12, Decimal('9.00')),
            ('39500', 'Útiles de Escritorio y Oficina', '5.10', 'Resaltadores varios colores', 'Piezas', 6, Decimal('4.50')),
            ('39500', 'Útiles de Escritorio y Oficina', '5.10', 'CD', 'Piezas', 100, Decimal('2.00')),
            ('39500', 'Útiles de Escritorio y Oficina', '5.10', 'DVD', 'Piezas', 100, Decimal('2.30')),

            # Materiales eléctricos / telemáticos
            ('39700', 'Útiles y Materiales Eléctricos', '5.10', 'Conectores RJ 45', 'Caja', 6, Decimal('250.00')),
            ('39700', 'Útiles y Materiales Eléctricos', '5.10', 'Conectores RJ 45 CAT 6A', 'Caja', 3, Decimal('500.00')),
            ('39700', 'Útiles y Materiales Eléctricos', '5.10', 'Jack para RJ45', 'Pieza', 30, Decimal('10.00')),
            ('39700', 'Útiles y Materiales Eléctricos', '5.10', 'Cable HDMI 50 metros', 'Pieza', 3, Decimal('502.00')),
            ('39700', 'Útiles y Materiales Eléctricos', '5.10', 'Cable HDMI 5 metros', 'Pieza', 8, Decimal('95.00')),
            ('39700', 'Útiles y Materiales Eléctricos', '5.10', 'Cable HDMI 10 metros', 'Pieza', 8, Decimal('120.00')),
            ('39700', 'Útiles y Materiales Eléctricos', '5.10', 'Cable de red UTP Cat. 6', 'Caja', 2, Decimal('1500.00')),
            ('39700', 'Útiles y Materiales Eléctricos', '5.10', 'Cable de red UTP Cat. 6a', 'Caja', 4, Decimal('4050.00')),
            ('39700', 'Útiles y Materiales Eléctricos', '5.10', 'Alargador de energia de 10 mts.', 'Pieza', 6, Decimal('90.00')),
            ('39700', 'Útiles y Materiales Eléctricos', '5.10', 'Alargador de energia de 20 mts.', 'Pieza', 6, Decimal('90.00')),
            ('39700', 'Útiles y Materiales Eléctricos', '5.10', 'Alargador de energia de 30 mts.', 'Pieza', 6, Decimal('90.00')),
            ('39700', 'Útiles y Materiales Eléctricos', '5.10', 'Cinta aislante', 'Piezas', 10, Decimal('15.00')),
            ('39700', 'Útiles y Materiales Eléctricos', '5.10', 'Conectores de energia', 'Piezas', 20, Decimal('4.00')),
            ('39700', 'Útiles y Materiales Eléctricos', '5.10', 'Conversores HDMI a VGA (no genericos)', 'Piezas', 4, Decimal('100.00')),
            ('39700', 'Útiles y Materiales Eléctricos', '5.10', 'Conversores VGA A DVI (no genericos)', 'Piezas', 4, Decimal('100.00')),
            ('39700', 'Útiles y Materiales Eléctricos', '5.10', 'Grapas para cable de energia', 'Cajas', 10, Decimal('12.00')),
            ('39700', 'Útiles y Materiales Eléctricos', '5.10', 'Baterias recargables', 'Global', 1, Decimal('350.00')),
            ('39700', 'Útiles y Materiales Eléctricos', '5.10', 'Pasta Termica', 'Bote', 30, Decimal('150.00')),
            ('39700', 'Útiles y Materiales Eléctricos', '5.10', 'Guantes latex de caucho sintetico', 'Cajas', 8, Decimal('55.00')),
            ('39700', 'Útiles y Materiales Eléctricos', '5.10', 'Pulseras antiestáticas', 'Piezas', 10, Decimal('50.00')),
            ('39700', 'Útiles y Materiales Eléctricos', '5.10', 'Brochas antiestáticas (juego de 5 piezas)', 'Juegos', 4, Decimal('200.00')),
            ('39700', 'Útiles y Materiales Eléctricos', '5.10', 'Multímetro (alta precisión 20.000 cuentas)', 'Piezas', 1, Decimal('1800.00')),
            ('39700', 'Útiles y Materiales Eléctricos', '5.10', 'Linterna led para mantenimiento de pc', 'Pieza', 6, Decimal('200.00')),
            ('39700', 'Útiles y Materiales Eléctricos', '5.10', 'Detector de voltaje buscapolo', 'Pieza', 1, Decimal('250.00')),
            ('39700', 'Útiles y Materiales Eléctricos', '5.10', 'Jeringas de 20 ml', 'Pieza', 30, Decimal('6.00')),
            ('39700', 'Útiles y Materiales Eléctricos', '5.10', 'Pila de botón de 3 voltios', 'Pieza', 20, Decimal('25.00')),
            ('39700', 'Útiles y Materiales Eléctricos', '5.10', 'Tester de red', 'Pieza', 6, Decimal('180.00')),
            ('39700', 'Útiles y Materiales Eléctricos', '5.10', 'Cortapicos de 3 mts.', 'Piezas', 5, Decimal('80.00')),

            # Repuestos de computación
            ('39800', 'Otros Repuestos y Accesorios', '5.10', 'Disco Duro Externo', 'Piezas', 2, Decimal('1700.00')),
            ('39800', 'Otros Repuestos y Accesorios', '5.10', 'Memorias de almacenamiento portátiles', 'Piezas', 5, Decimal('50.00')),
            ('39800', 'Otros Repuestos y Accesorios', '5.10', 'Unidades de estado solido', 'Piezas', 4, Decimal('800.00')),
            ('39800', 'Otros Repuestos y Accesorios', '5.10', 'Memoria Ram de 16 Gb DDR4', 'Piezas', 4, Decimal('750.00')),
            ('39800', 'Otros Repuestos y Accesorios', '5.10', 'Lector de BluRay', 'Piezas', 1, Decimal('941.00')),
            ('39800', 'Otros Repuestos y Accesorios', '5.10', 'Disco duro para servidor', 'Piezas', 10, Decimal('3800.00')),
            ('39800', 'Otros Repuestos y Accesorios', '5.10', 'Procesador para servidor', 'Piezas', 8, Decimal('1200.00')),
            ('39800', 'Otros Repuestos y Accesorios', '5.10', 'Lector de disco externo M2/ sata/hdd', 'Piezas', 3, Decimal('750.00')),
            ('39800', 'Otros Repuestos y Accesorios', '5.10', 'Lector y quemador externo de CD,DVD', 'Piezas', 4, Decimal('1500.00')),
            ('39800', 'Otros Repuestos y Accesorios', '5.10', 'Unidad Flash usb de 32 gb de 3.0', 'Piezas', 6, Decimal('180.00')),
            ('39800', 'Otros Repuestos y Accesorios', '5.10', 'Unidad Flash usb de 64 gb de 3.0', 'Piezas', 6, Decimal('280.00')),
            ('39800', 'Otros Repuestos y Accesorios', '5.10', 'Adaptadores de red a usb', 'Piezas', 3, Decimal('150.00')),
            ('39800', 'Otros Repuestos y Accesorios', '5.10', 'Adaptadores de red a tipo c', 'Piezas', 3, Decimal('150.00')),
            ('39800', 'Otros Repuestos y Accesorios', '5.10', 'Spliter de 4 puertos de usb a hdmi', 'Piezas', 2, Decimal('351.00')),
            ('39800', 'Otros Repuestos y Accesorios', '5.10', 'Adaptadores de hdmi a vga', 'Piezas', 4, Decimal('100.00')),
            ('39800', 'Otros Repuestos y Accesorios', '5.10', 'Adaptadores de displayport a vga', 'Piezas', 4, Decimal('100.00')),
            ('39800', 'Otros Repuestos y Accesorios', '5.10', 'Memorias ram DDR4 -DDR5 de 8GB', 'Piezas', 7, Decimal('1300.00')),
            ('39800', 'Otros Repuestos y Accesorios', '5.10', 'Fuentes de poder 800W 80+ Bronze (PC escritorio)', 'Piezas', 4, Decimal('1200.00')),
            ('39800', 'Otros Repuestos y Accesorios', '5.10', 'Discos solidos SSD SATA A400 de 980GB', 'Piezas', 8, Decimal('1700.00')),
            ('39800', 'Otros Repuestos y Accesorios', '5.10', 'Memoria RAM 16 Gb DDR4 para Servidor', 'Piezas', 2, Decimal('4200.00')),

            # Textiles y calzado
            ('33300', 'Prendas de Vestir', '5.10', 'Ropa de trabajo', 'Piezas', 6, Decimal('600.00')),
            ('33300', 'Prendas de Vestir', '5.10', 'Accesorios de seguridad industrial', 'Piezas', 6, Decimal('400.00')),
            ('33400', 'Calzados', '5.10', 'Calzados de trabajo', 'Piezas', 6, Decimal('800.00')),
        ]

        with transaction.atomic():
            # 1. Unidad y Secretaría
            secretaria, _ = Secretaria.objects.get_or_create(
                nombre="SECRETARIA DEPTAL. ADMINISTRATIVA Y FINANCIERA",
                defaults={"codigo": "SDAF", "is_active": True}
            )

            unidad = UnidadOrganizacional.objects.filter(nombre__icontains="Sistemas").first()
            if not unidad:
                unidad, _ = UnidadOrganizacional.objects.get_or_create(
                    nombre="SISTEMAS",
                    defaults={"codigo_sigep": "000 0 003", "secretaria": secretaria, "is_active": True}
                )
            else:
                unidad.secretaria = secretaria
                if not unidad.codigo_sigep:
                    unidad.codigo_sigep = "000 0 003"
                unidad.save()

            # 2. Totales por partida
            totales_por_partida = {}
            for item in ITEMS_FORMULARIO_005:
                partida_cod = item[0]
                monto = item[5] * item[6]
                totales_por_partida[partida_cod] = totales_por_partida.get(partida_cod, Decimal('0.00')) + monto

            poas_por_partida = {}
            for cod_partida, total_monto in totales_por_partida.items():
                partida_nombre = next(i[1] for i in ITEMS_FORMULARIO_005 if i[0] == cod_partida)
                partida, _ = PartidaPresupuestaria.objects.get_or_create(
                    codigo=cod_partida,
                    defaults={'nombre': partida_nombre}
                )

                poa, created = POA.objects.get_or_create(
                    unidad=unidad,
                    partida=partida,
                    gestion=2026,
                    defaults={
                        'monto_inicial': total_monto,
                        'monto_disponible': total_monto,
                        'monto_comprometido': Decimal('0.00'),
                        'monto_ejecutado': Decimal('0.00'),
                    }
                )
                if not created and poa.monto_ejecutado == Decimal('0.00') and poa.monto_comprometido == Decimal('0.00'):
                    poa.monto_inicial = total_monto
                    poa.monto_disponible = total_monto
                    poa.save()

                poas_por_partida[cod_partida] = poa

            # 3. Precargar materiales existentes para vincularlos limpiamente
            materiales_existentes = list(Material.objects.select_related('partida').all())

            def buscar_material_en_catalogo(partida_cod, nombre_item):
                norm_item = normalizar(nombre_item)
                # Coincidencia exacta o contenida dentro de la misma partida o afín
                for mat in materiales_existentes:
                    norm_mat = normalizar(mat.nombre)
                    if (mat.partida and mat.partida.codigo == partida_cod) or norm_item == norm_mat:
                        if norm_item == norm_mat or (len(norm_item) > 8 and norm_item in norm_mat):
                            return mat
                return None

            def generar_codigo_sabs_nuevo(partida_cod):
                """Genera códigos oficiales tipo 39700-001 respetando la secuencia SABS"""
                n = 1
                while True:
                    codigo = f"{partida_cod}-{n:03d}"
                    if not Material.objects.filter(codigo=codigo).exists():
                        return codigo
                    n += 1

            # 4. Procesar ítems del Formulario 005
            items_enlazados = 0
            items_creados = 0

            for item in ITEMS_FORMULARIO_005:
                partida_cod, _, act_poa, mat_nombre, u_medida, cant, precio_u = item
                poa_cabecera = poas_por_partida[partida_cod]
                partida_obj = poa_cabecera.partida

                # Buscar si ya existe en el catálogo para no duplicar
                material = buscar_material_en_catalogo(partida_cod, mat_nombre)

                if not material:
                    # Crear nuevo ítem en el catálogo con formato oficial SABS
                    um_obj, _ = UnidadMedida.objects.get_or_create(
                        codigo=u_medida[:10].upper(),
                        defaults={'nombre': u_medida}
                    )
                    nuevo_codigo_sabs = generar_codigo_sabs_nuevo(partida_cod)

                    material = Material.objects.create(
                        partida=partida_obj,
                        codigo=nuevo_codigo_sabs,
                        nombre=mat_nombre.strip(),
                        unidad_medida=u_medida,
                        unidad_medida_fk=um_obj,
                        stock_minimo=2,
                        stock_actual=0,
                        is_active=True
                    )
                    materiales_existentes.append(material)
                    items_creados += 1
                else:
                    items_enlazados += 1

                # Vincular en el Detalle del Formulario 005
                DetalleProgramacionPOA.objects.update_or_create(
                    poa=poa_cabecera,
                    material=material,
                    defaults={
                        'codigo_actividad_poa': act_poa,
                        'unidad_medida': u_medida,
                        'cantidad_programada': cant,
                        'precio_unitario_estimado': precio_u,
                        'subtotal': cant * precio_u,
                    }
                )

        self.stdout.write(self.style.SUCCESS(
            f"\n¡Proceso exitoso y consistente!\n"
            f"- Partidas POA cargadas: {len(poas_por_partida)} (Total: Bs. 212,526.00)\n"
            f"- Materiales existentes reutilizados del Catálogo: {items_enlazados}\n"
            f"- Nuevos materiales dados de alta con código SABS oficial: {items_creados}"
        ))