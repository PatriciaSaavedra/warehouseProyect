from django.test import TestCase
from django.contrib.auth.models import User
from decimal import Decimal
from django.db.models import Sum

from organizacion.models import Secretaria, UnidadOrganizacional
from inventario.models import (
    PartidaPresupuestaria, UnidadMedida, Material, Proveedor,
    Almacen, NotaIngreso, NotaIngresoDetalle, AsignacionEntradaUnidad,
    InventarioAlmacen, MovimientoInventario
)
from inventario.services import registrar_salida_valorada_peps

class TestAuditoriaInventarioSABS(TestCase):
    
    def setUp(self):
        """
        PREPARACIÓN DE DATOS (El catálogo institucional)
        """
        self.user = User.objects.create_user(username='almacenero', password='123')
        
        # 1. Crear Organización
        self.secretaria_saf = Secretaria.objects.create(nombre="SECRETARIA DEPARTAMENTAL ADMINISTRATIVA FINANCIERA", is_active=True)
        self.unidad_contrataciones = UnidadOrganizacional.objects.create(nombre="Contrataciones", secretaria=self.secretaria_saf, is_active=True)
        self.unidad_archivo = UnidadOrganizacional.objects.create(nombre="Archivo", secretaria=self.secretaria_saf, is_active=True)
        
        # 2. Crear Partida y Material
        self.partida = PartidaPresupuestaria.objects.create(codigo="32100", nombre="Papel")
        self.unidad_medida = UnidadMedida.objects.create(codigo="PAQ", nombre="Paquete")
        self.material = Material.objects.create(
            codigo="32100-001", 
            nombre="Papel Bond Tamaño Carta", 
            partida=self.partida, 
            unidad_medida_fk=self.unidad_medida,
            stock_actual=0
        )
        
        # 3. Proveedor y Almacén
        self.proveedor = Proveedor.objects.create(nit="123456701", razon_social="Librería Potosí")
        self.almacen = Almacen.objects.create(nombre="Almacén Central", tipo="CENTRAL", is_active=True)

    def test_flujo_contable_perfecto(self):
        """
        EJECUCIÓN DEL FLUJO DE 4 PASOS
        """
        
        # ==========================================================
        # PASO 1: Ingreso de 100 u. a 32.50 Bs. y Asignación (80/20)
        # ==========================================================
        cantidad_ingreso = 100
        precio_unitario = Decimal('32.50')
        precio_total = cantidad_ingreso * precio_unitario # 3250.00
        
        nota = NotaIngreso.objects.create(
            nro_nota="NI-001", proveedor=self.proveedor, almacen_destino=self.almacen,
            fecha="2026-05-10", usuario=self.user
        )
        
        detalle = NotaIngresoDetalle.objects.create(
            nota_ingreso=nota, material=self.material, cantidad=cantidad_ingreso,
            precio_unitario=precio_unitario, precio_total=precio_total
        )
        
        # Asignar a oficinas
        AsignacionEntradaUnidad.objects.create(nota_ingreso_detalle=detalle, unidad_organizacional=self.unidad_contrataciones, cantidad_asignada=80)
        AsignacionEntradaUnidad.objects.create(nota_ingreso_detalle=detalle, unidad_organizacional=self.unidad_archivo, cantidad_asignada=20)
        
        # Registrar físico y Kárdex (Entrada)
        InventarioAlmacen.objects.create(material=self.material, almacen=self.almacen, stock_fisico=100)
        MovimientoInventario.objects.create(
            material=self.material, almacen=self.almacen, tipo='ENTRADA',
            cantidad=100, costo_unitario=precio_unitario, costo_total=precio_total,
            stock_anterior=0, stock_resultante=100, referencia="INGRESO INICIAL",
            usuario=self.user, saldo_disponible_lote=100
        )

        # ==========================================================
        # PASO 2: Despachos (Consumo de las unidades)
        # ==========================================================
        # Sale 5 para Contrataciones
        registrar_salida_valorada_peps(
            material=self.material, almacen=self.almacen, cantidad_salida=5,
            tipo_movimiento='SALIDA', referencia="DESPACHO 1", usuario=self.user, unidad_destino=self.unidad_contrataciones
        )
        
        # Sale 10 para Archivo
        registrar_salida_valorada_peps(
            material=self.material, almacen=self.almacen, cantidad_salida=10,
            tipo_movimiento='SALIDA', referencia="DESPACHO 2", usuario=self.user, unidad_destino=self.unidad_archivo
        )
        
        # ==========================================================
        # PASO 3: AUDITORÍA CRUZADA (Asserts matemáticos)
        # ==========================================================
        
        # 1. Test: Consumo por Dependencias
        salidas_contrataciones = MovimientoInventario.objects.filter(unidad_destino=self.unidad_contrataciones)
        cant_ret_contrataciones = salidas_contrataciones.aggregate(s=Sum('cantidad'))['s']
        val_ret_contrataciones = salidas_contrataciones.aggregate(s=Sum('costo_total'))['s']
        
        self.assertEqual(cant_ret_contrataciones, 5, "Fallo: Contrataciones debió retirar 5 unidades.")
        self.assertEqual(val_ret_contrataciones, Decimal('162.50'), "Fallo: Valor de Contrataciones debe ser 162.50 Bs.")
        
        salidas_archivo = MovimientoInventario.objects.filter(unidad_destino=self.unidad_archivo)
        val_ret_archivo = salidas_archivo.aggregate(s=Sum('costo_total'))['s']
        self.assertEqual(val_ret_archivo, Decimal('325.00'), "Fallo: Valor de Archivo debe ser 325.00 Bs.")

        # 2. Test: Matriz DBC
        asigs_saf = AsignacionEntradaUnidad.objects.filter(unidad_organizacional__secretaria=self.secretaria_saf)
        cant_total_saf = asigs_saf.aggregate(s=Sum('cantidad_asignada'))['s']
        self.assertEqual(cant_total_saf, 100, "Fallo: La Secretaría SAF debió planificar/recibir 100 unidades en total.")

        # 3. Test: Kárdex Valorado (PEPS)
        stock_actual_bd = InventarioAlmacen.objects.get(material=self.material, almacen=self.almacen).stock_fisico
        self.assertEqual(stock_actual_bd, 85, "Fallo: El stock físico final debe ser 85 (100 - 5 - 10).")

        # 4. Test: Formulario DGCF R1.05 / R1.06
        tot_entradas_bs = MovimientoInventario.objects.filter(tipo='ENTRADA').aggregate(s=Sum('costo_total'))['s']
        tot_salidas_bs = MovimientoInventario.objects.filter(tipo='SALIDA').aggregate(s=Sum('costo_total'))['s']
        saldo_final_bs = tot_entradas_bs - tot_salidas_bs
        
        self.assertEqual(saldo_final_bs, Decimal('2762.50'), "Fallo Crítico: El Saldo Valorado Final (DGCF) debe ser exactamente 2762.50 Bs.")
        
        print("\n✅ [ÉXITO] TEST END-TO-END SUPERADO:")
        print(f"   -> Entradas: 100 u. (Bs. {tot_entradas_bs})")
        print(f"   -> Salidas: 15 u. (Bs. {tot_salidas_bs})")
        print(f"   -> Saldo Final Auditado: {stock_actual_bd} u. (Bs. {saldo_final_bs})")
        print("   -> Consumos y Matrices DBC validadas al centavo.\n")
        from django.test import TestCase
from django.contrib.auth.models import User
from decimal import Decimal
from django.db.models import Sum

from organizacion.models import Secretaria, UnidadOrganizacional
from inventario.models import PartidaPresupuestaria, UnidadMedida, Material, Almacen, InventarioAlmacen, MovimientoInventario
from inventario.services import registrar_salida_valorada_peps


class TestValoracionPEPS(TestCase):
    
    def setUp(self):
        """ PREPARACIÓN DEL ENTORNO """
        self.user = User.objects.create_user(username='almacenero_peps', password='123')
        
        self.secretaria = Secretaria.objects.create(nombre="SEC. EDUCACION Y CULTURA", is_active=True)
        self.unidad_colegio = UnidadOrganizacional.objects.create(nombre="Colegio Nacional", secretaria=self.secretaria, is_active=True)
        
        self.partida = PartidaPresupuestaria.objects.create(codigo="32200", nombre="Artes Gráficas")
        self.unidad_medida = UnidadMedida.objects.create(codigo="PZA", nombre="Piezas")
        self.material = Material.objects.create(
            codigo="32200-002", 
            nombre="Cartulina Color Pliego", 
            partida=self.partida, 
            unidad_medida_fk=self.unidad_medida,
            stock_actual=0
        )
        self.almacen = Almacen.objects.create(nombre="Almacén Central", tipo="CENTRAL", is_active=True)
        self.inv = InventarioAlmacen.objects.create(material=self.material, almacen=self.almacen, stock_fisico=0)

    def test_consumo_peps_lotes_diferentes(self):
        """
        PRUEBA DE ESCRITORIO:
        - Entran 5 a 25 Bs.
        - Entran 2 a 75 Bs.
        - Salen 6. (Debería costar 200 Bs y sobrar 1 a 75 Bs).
        """
        print("\n" + "="*60)
        print("🧪 INICIANDO TEST DE VALORACIÓN P.E.P.S. (Cartulinas)")
        print("="*60)

        # ---------------------------------------------------------
        # 1. INGRESO DEL LOTE 1 (El más barato y antiguo)
        # ---------------------------------------------------------
        self.inv.stock_fisico += 5
        self.inv.save()
        lote1 = MovimientoInventario.objects.create(
            material=self.material, almacen=self.almacen, tipo='ENTRADA',
            cantidad=5, costo_unitario=Decimal('25.00'), costo_total=Decimal('125.00'),
            stock_anterior=0, stock_resultante=5, referencia="COMPRA LOTE 1 (Antiguo)",
            usuario=self.user, saldo_disponible_lote=5
        )
        print("📦 [INGRESO] Lote 1: 5 u. a 25.00 Bs  -> (Costo Lote: 125.00 Bs)")

        # ---------------------------------------------------------
        # 2. INGRESO DEL LOTE 2 (El más caro y nuevo)
        # ---------------------------------------------------------
        self.inv.stock_fisico += 2
        self.inv.save()
        lote2 = MovimientoInventario.objects.create(
            material=self.material, almacen=self.almacen, tipo='ENTRADA',
            cantidad=2, costo_unitario=Decimal('75.00'), costo_total=Decimal('150.00'),
            stock_anterior=5, stock_resultante=7, referencia="COMPRA LOTE 2 (Nuevo)",
            usuario=self.user, saldo_disponible_lote=2
        )
        print("📦 [INGRESO] Lote 2: 2 u. a 75.00 Bs  -> (Costo Lote: 150.00 Bs)")
        print("📊 STOCK FÍSICO DISPONIBLE: 7 unidades.")

        # ---------------------------------------------------------
        # 3. EL DESPACHO O NOTA DE SALIDA
        # ---------------------------------------------------------
        print("\n🚚 [PLANILLA DE PEDIDO] Entregando 6 cartulinas a 'Colegio Nacional'...")
        
        # Esta es la función clave de tu sistema que procesa el algoritmo PEPS
        salida_mov = registrar_salida_valorada_peps(
            material=self.material, 
            almacen=self.almacen, 
            cantidad_salida=6,
            tipo_movimiento='SALIDA', 
            referencia="PEDIDO COLEGIO", 
            usuario=self.user, 
            unidad_destino=self.unidad_colegio
        )
        
        # ---------------------------------------------------------
        # 4. LA AUDITORÍA DE CONTRALORÍA (Asserts)
        # ---------------------------------------------------------
        
        # Actualizamos la memoria para ver qué pasó en la BD
        lote1.refresh_from_db()
        lote2.refresh_from_db()
        self.inv.refresh_from_db()

        # A) ¿Se vació el Lote 1? (5 - 5 = 0)
        self.assertEqual(lote1.saldo_disponible_lote, 0, "Fallo: El Lote 1 debió quedar en 0.")
        
        # B) ¿Se descontó correctamente el Lote 2? (2 - 1 = 1)
        self.assertEqual(lote2.saldo_disponible_lote, 1, "Fallo: El Lote 2 debió quedar con 1 unidad de saldo.")
        
        # C) ¿El sistema cobró 200 Bs en la planilla? (5*25 + 1*75 = 200)
        costo_salida = salida_mov.costo_total
        self.assertEqual(costo_salida, Decimal('200.00'), f"Fallo: El costo calculado fue {costo_salida}.")

        # D) ¿El inventario físico bajó a 1?
        self.assertEqual(self.inv.stock_fisico, 1, "Fallo: El stock físico final debe ser 1 unidad.")

        print("✅ [PEPS APLICADO] Se vació el Lote 1 (5 u. x 25 Bs = 125 Bs).")
        print("✅ [PEPS APLICADO] Se extrajo 1 unidad del Lote 2 (1 u. x 75 Bs = 75 Bs).")
        print(f"✅ [VALORACIÓN EXACTA] Costo total de la Planilla: {costo_salida} Bs (¡Matemática perfecta!).")
        print(f"✅ [SALDO FINAL] Sobra {self.inv.stock_fisico} cartulina en estantería valuada a 75.00 Bs.\n")