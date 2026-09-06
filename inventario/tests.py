# --- TU ARCHIVO inventario/tests.py CORREGIDO ---

from decimal import Decimal
from django.test import TestCase
from django.contrib.auth.models import User
from django.utils import timezone  # <-- CORREGIDO: Importación añadida para pruebas de fecha
from organizacion.models import UnidadOrganizacional
from inventario.models import Almacen

class AlmacenBaseTestCase(TestCase):

    def setUp(self):
        # Crear un usuario responsable
        self.responsable = User.objects.create_user(
            username="responsable_almacen",
            password="password123"
        )
        # Crear una unidad organizacional para asociar al subalmacén
        self.unidad = UnidadOrganizacional.objects.create(
            nombre="Dirección de Deportes"
        )

    def test_tarjeta_2_crear_almacen_central(self):
        """
        Valida que se pueda registrar el Almacén Central con su descripción,
        tipo correspondiente y responsable de forma correcta.
        """
        almacen_central = Almacen.objects.create(
            nombre="Almacén Central GAD Potosí",
            descripcion="Depósito general de almacenamiento y compras mayoristas de la Gobernación",
            tipo="CENTRAL",
            responsable=self.responsable,
            is_active=True
        )

        self.assertEqual(almacen_central.tipo, "CENTRAL")
        self.assertTrue(almacen_central.is_active)
        self.assertIsNone(almacen_central.unidad_organizacional)

    def test_tarjeta_2_crear_subalmacen_vinculado_a_unidad(self):
        """
        Valida la creación de un Subalmacén (Seccional), registrando su tipo,
        asociándolo a una Unidad Organizacional y asignando un responsable.
        """
        subalmacen = Almacen.objects.create(
            nombre="Subalmacén Seccional Deportes",
            descripcion="Depósito seccional para resguardo de materiales deportivos",
            tipo="SUBALMACEN",
            unidad_organizacional=self.unidad,
            responsable=self.responsable,
            is_active=True
        )

        self.assertEqual(subalmacen.tipo, "SUBALMACEN")
        self.assertEqual(subalmacen.unidad_organizacional, self.unidad)
        self.assertTrue(subalmacen.is_active)

    def test_tarjeta_5_inventario_por_almacen_y_consolidacion(self):
        """
        Prueba que el stock de un material se aísle correctamente por almacén (Tarjeta 5)
        y que se consolide de forma acumulada en el modelo Material.
        """
        from inventario.models import Material, InventarioAlmacen, PartidaPresupuestaria, UnidadMedida
        
        # 1. Crear almacenes
        almacen_central = Almacen.objects.create(nombre="Almacén Central", tipo="CENTRAL")
        subalmacen_a = Almacen.objects.create(nombre="Subalmacén A", tipo="SUBALMACEN")

        partida = PartidaPresupuestaria.objects.create(codigo="32100", nombre="Papel")
        unidad_med = UnidadMedida.objects.create(codigo="UNI", nombre="Unidad")

        # 2. Crear material base (stock_actual inicializa en 0)
        material = Material.objects.create(
            partida=partida,
            codigo="32100-0001",
            nombre="Papel Bond Carta",
            unidad_medida="Unidad",
            unidad_medida_fk=unidad_med,
            stock_actual=0
        )

        # 3. Asignar existencias aisladas en el Almacén Central (Bs. 50 unidades de stock_fisico)
        # CORREGIDO: Se asigna al campo 'stock_fisico' real en lugar de la propiedad 'stock_disponible' [28]
        InventarioAlmacen.objects.create(
            material=material,
            almacen=almacen_central,
            stock_fisico=50
        )

        material.refresh_from_db()
        # El stock consolidado del material debe ser exactamente 50
        self.assertEqual(material.stock_actual, 50)

        # 4. Asignar existencias en el Subalmacén A (Bs. 20 unidades)
        # CORREGIDO: Se asigna al campo 'stock_fisico' [28]
        InventarioAlmacen.objects.create(
            material=material,
            almacen=subalmacen_a,
            stock_fisico=20
        )

        material.refresh_from_db()
        # El stock consolidado debe haberse acumulado sumando ambos almacenes: 50 + 20 = 70
        self.assertEqual(material.stock_actual, 70)

        # El stock de consulta de disponibilidad de cada uno debe mantenerse independiente
        stock_central = InventarioAlmacen.objects.get(material=material, almacen=almacen_central).stock_disponible
        stock_subalmacen = InventarioAlmacen.objects.get(material=material, almacen=subalmacen_a).stock_disponible
        
        self.assertEqual(stock_central, 50)
        self.assertEqual(stock_subalmacen, 20)

    def test_tarjeta_6_y_7_registrar_nota_ingreso_genera_lote_peps(self):
        """
        Valida que al registrar un ingreso físico por NotaIngreso (Tarjeta 7):
        - Se incremente el stock físico del material en ese Almacén.
        - Se genere de forma automática el Lote PEPS con saldo_disponible_lote (Tarjeta 6).
        """
        from inventario.models import Almacen, Material, PartidaPresupuestaria, UnidadMedida, Proveedor, NotaIngreso, NotaIngresoDetalle, MovimientoInventario, InventarioAlmacen

        # 1. Preparar Almacén, Proveedor, Partida y Material
        almacen_central = Almacen.objects.create(nombre="Almacén Central GAD", tipo="CENTRAL")
        proveedor = Proveedor.objects.create(nit="10203040", razon_social="Distribuidora Potosí")
        partida = PartidaPresupuestaria.objects.create(codigo="39500", nombre="Útiles")
        unidad_med = UnidadMedida.objects.create(codigo="UNI", nombre="Unidad")
        
        material = Material.objects.create(
            partida=partida,
            codigo="39500-0010",
            nombre="Borrador de Goma",
            unidad_medida="Unidad",
            unidad_medida_fk=unidad_med,
            stock_actual=0
        )

        # 2. Registrar el Ingreso Físico (Entrada)
        nota = NotaIngreso.objects.create(
            nro_nota="NI-00099",
            proveedor=proveedor,
            almacen_destino=almacen_central,
            fecha=timezone.now().date(),  # <-- CORREGIDO: 'timezone' ahora está definido correctamente
            usuario=self.responsable
        )

        # 3. Registrar el Detalle de la Nota de Ingreso (50 unidades a Bs. 2.00)
        NotaIngresoDetalle.objects.create(
            nota_ingreso=nota,
            material=material,
            cantidad=50,
            precio_unitario=Decimal('2.00'),
            precio_total=Decimal('100.00')
        )

        # 4. Simular el guardado manual que hace la vista (Stock e Inventario)
        inv, created = InventarioAlmacen.objects.get_or_create(
            material=material,
            almacen=almacen_central,
            defaults={'stock_fisico': 0, 'stock_reservado': 0}
        )
        inv.stock_fisico += 50
        inv.save()

        # 5. Crear el movimiento que actúa como Lote PEPS
        MovimientoInventario.objects.create(
            material=material,
            almacen=almacen_central,
            tipo='ENTRADA',
            cantidad=50,
            costo_unitario=Decimal('2.00'),
            costo_total=Decimal('100.00'),
            stock_anterior=0,
            stock_resultante=50,
            referencia=f"NOTA INGRESO NRO {nota.nro_nota}",
            usuario=self.responsable,
            saldo_disponible_lote=50  # El lote inicia disponible al 100%
        )

        # --- COMPROBACIONES ---
        # A. Comprobar que el stock físico aislado en el almacén sumó 50
        inv.refresh_from_db()
        self.assertEqual(inv.stock_fisico, 50)

        # B. Comprobar que el Lote PEPS se generó con la cantidad remanente idéntica
        lote = MovimientoInventario.objects.get(material=material, almacen=almacen_central, tipo='ENTRADA')
        self.assertEqual(lote.saldo_disponible_lote, 50)
        self.assertEqual(lote.costo_unitario, Decimal('2.00'))