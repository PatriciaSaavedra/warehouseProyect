from decimal import Decimal
from datetime import date
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User

from organizacion.models import Secretaria, UnidadOrganizacional
from inventario.models import (
    PartidaPresupuestaria, UnidadMedida, Material, Proveedor,
    Almacen, NotaIngreso, NotaIngresoDetalle, InventarioAlmacen, MovimientoInventario
)
from solicitudes.models import Solicitud
from inventario.services import calcular_desglose_peps


class Command(BaseCommand):
    help = 'Genera 2 lotes con precios distintos y una solicitud para auditar el método PEPS'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("=" * 65))
        self.stdout.write(self.style.SUCCESS("🚀 GENERANDO ESCENARIO DE PRUEBA PEPS (Lavandina 4L)"))
        self.stdout.write(self.style.SUCCESS("=" * 65))

        # 1. Usuario y Organización
        usuario = User.objects.filter(is_superuser=True).first() or User.objects.first()
        if not usuario:
            usuario = User.objects.create_user(username='emilia', first_name='Emilia', last_name='Saavedra')

        secretaria, _ = Secretaria.objects.get_or_create(
            nombre="SECRETARIA DEPARTAMENTAL ADMINISTRATIVA FINANCIERA",
            defaults={'is_active': True}
        )
        unidad_contrataciones, _ = UnidadOrganizacional.objects.get_or_create(
            nombre="Contrataciones",
            defaults={'secretaria': secretaria, 'is_active': True}
        )

        almacen = Almacen.objects.filter(tipo='CENTRAL').first()
        if not almacen:
            almacen, _ = Almacen.objects.get_or_create(
                nombre="Almacen Central",
                defaults={'tipo': 'CENTRAL', 'is_active': True}
            )

        # 2. Catálogos: Partida, Unidad y Material
        partida, _ = PartidaPresupuestaria.objects.get_or_create(
            codigo="39100",
            defaults={'nombre': 'MATERIAL DE LIMPIEZA E HIGIENE'}
        )
        unidad_medida, _ = UnidadMedida.objects.get_or_create(
            codigo="BOTE",
            defaults={'nombre': 'Bote'}
        )
        material, _ = Material.objects.get_or_create(
            codigo="39100-030",
            defaults={
                'nombre': 'LAVANDINA DE 4 LITROS',
                'partida': partida,
                'unidad_medida_fk': unidad_medida,
                'unidad_medida': 'BOTE',
                'stock_actual': 0
            }
        )

        proveedor, _ = Proveedor.objects.get_or_create(
            nit="5896231018",
            defaults={'razon_social': 'COMERCIAL ANDINA DE LIMPIEZA', 'is_active': True}
        )

        # -------------------------------------------------------------
        # 3. LOTE 1: 2 unidades a Bs. 30,00 (Total 60,00 Bs.)
        # -------------------------------------------------------------
        nro_lote_1 = "NI-PEPS-1"
        nota1, _ = NotaIngreso.objects.get_or_create(
            nro_nota=nro_lote_1,
            defaults={
                'proveedor': proveedor,
                'almacen_destino': almacen,
                'fecha': date.today(),
                'usuario': usuario,
                'observaciones': 'Lote 1 de prueba PEPS'
            }
        )
        NotaIngresoDetalle.objects.get_or_create(
            nota_ingreso=nota1,
            material=material,
            defaults={'cantidad': 2, 'precio_unitario': Decimal('30.00'), 'precio_total': Decimal('60.00')}
        )
        MovimientoInventario.objects.get_or_create(
            material=material,
            almacen=almacen,
            referencia=f"INGRESO NOTA {nro_lote_1}",
            defaults={
                'tipo': 'ENTRADA',
                'cantidad': 2,
                'costo_unitario': Decimal('30.00'),
                'costo_total': Decimal('60.00'),
                'stock_anterior': 0,
                'stock_resultante': 2,
                'usuario': usuario,
                'saldo_disponible_lote': 2
            }
        )
        self.stdout.write(f"📦 [CREADO] Lote 1 ({nro_lote_1}): 2 unidades × Bs. 30,00 = Bs. 60,00")

        # -------------------------------------------------------------
        # 4. LOTE 2: 1 unidad a Bs. 45,00 (Total 45,00 Bs.)
        # -------------------------------------------------------------
        nro_lote_2 = "NI-PEPS-2"
        nota2, _ = NotaIngreso.objects.get_or_create(
            nro_nota=nro_lote_2,
            defaults={
                'proveedor': proveedor,
                'almacen_destino': almacen,
                'fecha': date.today(),
                'usuario': usuario,
                'observaciones': 'Lote 2 de prueba PEPS'
            }
        )
        NotaIngresoDetalle.objects.get_or_create(
            nota_ingreso=nota2,
            material=material,
            defaults={'cantidad': 1, 'precio_unitario': Decimal('45.00'), 'precio_total': Decimal('45.00')}
        )
        MovimientoInventario.objects.get_or_create(
            material=material,
            almacen=almacen,
            referencia=f"INGRESO NOTA {nro_lote_2}",
            defaults={
                'tipo': 'ENTRADA',
                'cantidad': 1,
                'costo_unitario': Decimal('45.00'),
                'costo_total': Decimal('45.00'),
                'stock_anterior': 2,
                'stock_resultante': 3,
                'usuario': usuario,
                'saldo_disponible_lote': 1
            }
        )
        self.stdout.write(f"📦 [CREADO] Lote 2 ({nro_lote_2}): 1 unidad × Bs. 45,00 = Bs. 45,00")

        # Actualizar stock físico en estantería
        inv_almacen, _ = InventarioAlmacen.objects.get_or_create(material=material, almacen=almacen)
        inv_almacen.stock_fisico = 3
        inv_almacen.save()

        # -------------------------------------------------------------
        # 5. CREAR LA SOLICITUD (Incluyendo el campo 'fecha' obligatorio)
        # -------------------------------------------------------------
        ultimo_ped = Solicitud.objects.order_by('-id').first()
        correlativo = (ultimo_ped.id + 1) if ultimo_ped else 1
        codigo_ped = f"PED-PEPS-{correlativo:03d}"

        campos_solicitud = {f.name for f in Solicitud._meta.fields}
        
        datos_solicitud = {
            'codigo': codigo_ped,
            'fecha': date.today(),  # <-- Campo obligatorio que faltaba
            'solicitante': usuario,
            'unidad_solicitante': unidad_contrataciones,
            'flujo_atencion': 'SALIDA_ALMACEN',
            'tipo_requerimiento': 'BIEN',
            'prioridad': 'ALTA',
            'justificacion': 'Pedido de prueba para verificar consumo multicapa PEPS',
            'estado': 'REGISTRADA'
        }
        if 'almacen' in campos_solicitud:
            datos_solicitud['almacen'] = almacen
        if 'gestion' in campos_solicitud:
            datos_solicitud['gestion'] = date.today().year

        kwargs_solicitud = {k: v for k, v in datos_solicitud.items() if k in campos_solicitud}
        solicitud = Solicitud.objects.create(**kwargs_solicitud)

        # Crear detalle de la solicitud
        modelo_detalle = solicitud.detalles.model
        campos_detalle = {f.name for f in modelo_detalle._meta.fields}
        
        datos_detalle = {
            'solicitud': solicitud,
            'material': material,
            'cantidad_solicitada': 3,
            'precio_unitario_referencial': Decimal('30.00'),
            'subtotal_referencial': Decimal('90.00')
        }
        if 'estado' in campos_detalle:
            datos_detalle['estado'] = 'CREADA'

        kwargs_detalle = {k: v for k, v in datos_detalle.items() if k in campos_detalle}
        modelo_detalle.objects.create(**kwargs_detalle)

        self.stdout.write(self.style.WARNING(f"\n📋 [CREADA] Solicitud Folio: {solicitud.codigo}"))
        self.stdout.write(f"   -> Solicitante: {usuario.get_full_name() or usuario.username} ({unidad_contrataciones.nombre})")
        self.stdout.write(f"   -> Artículo: {material.nombre} (Cantidad: 3 unidades)")

        # -------------------------------------------------------------
        # 6. VERIFICACIÓN Y AUDITORÍA PEPS
        # -------------------------------------------------------------
        desglose = calcular_desglose_peps(material, almacen, 3)

        self.stdout.write("\n" + "-" * 65)
        self.stdout.write("🔍 RESULTADO DEL CÁLCULO PEPS EN TIEMPO REAL:")
        self.stdout.write("-" * 65)
        for l in desglose['desglose_lotes']:
            self.stdout.write(f"   • {l['referencia']}: {l['cantidad']} u. × Bs. {l['costo_unitario']} = Bs. {l['subtotal']}")

        self.stdout.write(f"\n💰 SUBTOTAL REAL CALCULADO: Bs. {desglose['subtotal_real']}")
        if desglose['subtotal_real'] == Decimal('105.00'):
            self.stdout.write(self.style.SUCCESS("✅ [CORRECTO] El subtotal es exactamente 105,00 Bs. (2@30 + 1@45)."))
        else:
            self.stdout.write(self.style.ERROR(f"❌ [ERROR] Esperado 105.00 Bs, pero dio: {desglose['subtotal_real']}"))

        self.stdout.write(self.style.SUCCESS("\n🔗 ENLACE PARA VER EN EL NAVEGADOR:"))
        self.stdout.write(f"   http://127.0.0.1:8000/solicitudes/{solicitud.id}/")
        self.stdout.write(self.style.SUCCESS("=" * 65 + "\n"))