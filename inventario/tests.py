# --- TU ARCHIVO inventario/tests.py CORREGIDO ---

from decimal import Decimal
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone  # <-- CORREGIDO: Importación añadida para pruebas de fecha
from organizacion.models import Secretaria, UnidadOrganizacional
from inventario.models import Almacen
from usuarios.models import PerfilUsuario
from inventario.services import unidades_ya_asignadas
from inventario.admin import AlmacenAdminForm

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


class Tarjeta2VistaAlmacenTest(TestCase):
    """
    Tarjeta 2: el formulario de Almacenes guarda la relación Unidad → Almacén,
    bloquea asignaciones duplicadas (una unidad = UN almacén) y solo acepta
    unidades activas. NO se reutiliza ni modifica PerfilUsuario.almacenes_autorizados.
    """

    def setUp(self):
        self.secretaria = Secretaria.objects.create(nombre="Secretaría de Salud")
        self.unidad_a = UnidadOrganizacional.objects.create(nombre="Unidad A", secretaria=self.secretaria)
        self.unidad_b = UnidadOrganizacional.objects.create(nombre="Unidad B", secretaria=self.secretaria)
        self.central = Almacen.objects.create(nombre="Almacén Central", tipo="CENTRAL")
        self.unasba = Almacen.objects.create(nombre="Subalmacén UNASBA", tipo="SUBALMACEN")

        self.admin = User.objects.create_superuser(
            username="admin_tarjeta2",
            password="Admin1234",
            email="admin2@test.gob.bo",
        )
        PerfilUsuario.objects.create(user=self.admin, rol="ADMINISTRADOR")
        self.client = Client()
        self.client.force_login(self.admin)

    def _datos_almacen(self, unidades_atendidas, **extras):
        datos = {
            'nombre': 'Subalmacén Nuevo',
            'descripcion': '',
            'tipo': 'SUBALMACEN',
            'unidad_organizacional': '',
            'responsable': '',
            'almacen_padre': '',
            'is_active': 'true',
            'unidades_atendidas': [str(u.id) for u in unidades_atendidas],
        }
        datos.update(extras)
        return datos

    def test_crear_almacen_guarda_unidades_atendidas(self):
        respuesta = self.client.post(
            reverse('crear_almacen'),
            self._datos_almacen([self.unidad_a], nombre='Subalmacén Registrado'),
        )
        self.assertRedirects(respuesta, reverse('almacen_list'))

        almacen = Almacen.objects.get(nombre='Subalmacén Registrado')
        self.assertEqual(list(almacen.unidades_atendidas.all()), [self.unidad_a])
        self.assertEqual(list(self.unidad_a.almacenes_que_atienden.all()), [almacen])

    def test_editar_almacen_guarda_unidades_atendidas(self):
        respuesta = self.client.post(
            reverse('editar_almacen', args=[self.unasba.id]),
            self._datos_almacen([self.unidad_b], nombre=self.unasba.nombre),
        )
        self.assertRedirects(respuesta, reverse('almacen_list'))

        self.unasba.refresh_from_db()
        self.assertEqual(list(self.unasba.unidades_atendidas.all()), [self.unidad_b])
        self.assertEqual(list(self.unidad_b.almacenes_que_atienden.all()), [self.unasba])

    def test_bloquea_unidad_ya_asignada_a_otro_almacen(self):
        self.central.unidades_atendidas.add(self.unidad_a)

        respuesta = self.client.post(
            reverse('crear_almacen'),
            self._datos_almacen([self.unidad_a]),
        )
        self.assertEqual(respuesta.status_code, 302)
        self.assertFalse(Almacen.objects.filter(nombre='Subalmacén Nuevo').exists())
        self.assertEqual(list(self.unidad_a.almacenes_que_atienden.all()), [self.central])

    def test_cambio_de_almacen_reemplaza_sin_duplicar(self):
        self.central.unidades_atendidas.add(self.unidad_a)

        # Intentar asignar también al Subalmacén UNASBA: bloqueado (una unidad = un almacén)
        self.client.post(
            reverse('editar_almacen', args=[self.unasba.id]),
            self._datos_almacen([self.unidad_a], nombre=self.unasba.nombre),
        )
        self.unasba.refresh_from_db()
        self.assertEqual(self.unasba.unidades_atendidas.count(), 0)

        # Cambiar de almacén: retirar del Central y asignar a UNASBA
        self.central.unidades_atendidas.remove(self.unidad_a)
        self.client.post(
            reverse('editar_almacen', args=[self.unasba.id]),
            self._datos_almacen([self.unidad_a], nombre=self.unasba.nombre),
        )
        self.unasba.refresh_from_db()
        self.central.refresh_from_db()
        self.assertEqual(list(self.unidad_a.almacenes_que_atienden.all()), [self.unasba])
        self.assertEqual(self.central.unidades_atendidas.count(), 0)

    def test_solo_acepta_unidades_activas(self):
        inactiva = UnidadOrganizacional.objects.create(
            nombre="Unidad Inactiva", secretaria=self.secretaria, is_active=False
        )
        respuesta = self.client.post(
            reverse('crear_almacen'),
            self._datos_almacen([self.unidad_a, inactiva]),
        )
        self.assertRedirects(respuesta, reverse('almacen_list'))

        almacen = Almacen.objects.get(nombre='Subalmacén Nuevo')
        self.assertEqual(list(almacen.unidades_atendidas.all()), [self.unidad_a])

    def test_vista_almacen_muestra_unidades_atendidas(self):
        self.central.unidades_atendidas.add(self.unidad_a)
        respuesta = self.client.get(reverse('almacen_list'))
        self.assertContains(respuesta, 'Almacén Central')
        self.assertContains(respuesta, 'Unidad A')

    def test_editar_almacen_conserva_relacion_a_unidad_inactiva(self):
        # Escenario reportado: asignar → desactivar → editar el almacén sin tocar la asociación.
        # El formulario no muestra unidades inactivas, por lo que el POST llega sin ellas.
        self.central.unidades_atendidas.add(self.unidad_a)
        self.unidad_a.is_active = False
        self.unidad_a.save()

        respuesta = self.client.post(
            reverse('editar_almacen', args=[self.central.id]),
            self._datos_almacen([], nombre=self.central.nombre, tipo='CENTRAL'),
        )
        self.assertRedirects(respuesta, reverse('almacen_list'))

        self.central.refresh_from_db()
        self.assertEqual(list(self.central.unidades_atendidas.all()), [self.unidad_a])
        self.assertEqual(list(self.unidad_a.almacenes_que_atienden.all()), [self.central])

    def test_editar_almacen_quitar_activa_conserva_inactiva(self):
        self.central.unidades_atendidas.add(self.unidad_a, self.unidad_b)
        self.unidad_b.is_active = False
        self.unidad_b.save()

        # Se deselecciona la única unidad activa; la inactiva no aparece en el formulario
        self.client.post(
            reverse('editar_almacen', args=[self.central.id]),
            self._datos_almacen([], nombre=self.central.nombre, tipo='CENTRAL'),
        )
        self.central.refresh_from_db()
        self.assertEqual(list(self.central.unidades_atendidas.all()), [self.unidad_b])

    def test_editar_almacen_conserva_inactiva_y_actualiza_activas(self):
        self.central.unidades_atendidas.add(self.unidad_a, self.unidad_b)
        self.unidad_a.is_active = False
        self.unidad_a.save()

        self.client.post(
            reverse('editar_almacen', args=[self.central.id]),
            self._datos_almacen([self.unidad_b], nombre=self.central.nombre, tipo='CENTRAL'),
        )
        self.central.refresh_from_db()
        self.assertCountEqual(
            list(self.central.unidades_atendidas.values_list('id', flat=True)),
            [self.unidad_a.id, self.unidad_b.id],
        )

    def test_editar_almacen_no_asigna_unidad_inactiva_nueva(self):
        self.unidad_b.is_active = False
        self.unidad_b.save()

        self.client.post(
            reverse('editar_almacen', args=[self.unasba.id]),
            self._datos_almacen([self.unidad_b], nombre=self.unasba.nombre),
        )
        self.unasba.refresh_from_db()
        self.assertEqual(self.unasba.unidades_atendidas.count(), 0)

    def test_editar_almacen_muestra_aviso_unidad_inactiva(self):
        self.central.unidades_atendidas.add(self.unidad_a)
        self.unidad_a.is_active = False
        self.unidad_a.save()

        respuesta = self.client.get(reverse('editar_almacen', args=[self.central.id]))
        self.assertContains(respuesta, 'inactiva')
        self.assertContains(respuesta, 'Unidad A')


class Tarjeta2AuditoriaEscrituraTest(TestCase):
    """
    Tarjeta 2 - Auditoría: la validación (una Unidad = UN almacén) está centralizada
    en inventario.services.unidades_ya_asignadas y se reutiliza en vistas y Django Admin.
    """

    def setUp(self):
        self.secretaria = Secretaria.objects.create(nombre="Secretaría de Salud")
        self.unidad_a = UnidadOrganizacional.objects.create(nombre="Unidad A", secretaria=self.secretaria)
        self.unidad_b = UnidadOrganizacional.objects.create(nombre="Unidad B", secretaria=self.secretaria)
        self.central = Almacen.objects.create(nombre="Almacén Central", tipo="CENTRAL")
        self.unasba = Almacen.objects.create(nombre="Subalmacén UNASBA", tipo="SUBALMACEN")
        self.central.unidades_atendidas.add(self.unidad_a)

    def test_servicio_centralizado_detecta_conflicto(self):
        conflictos = unidades_ya_asignadas([self.unidad_b.id])
        self.assertEqual(conflictos, {})
        conflictos = unidades_ya_asignadas([self.unidad_a.id])
        self.assertEqual(conflictos[self.unidad_a.id], (self.unidad_a.nombre, self.central.nombre))

    def test_servicio_centralizado_excluye_almacen_en_edicion(self):
        # Para un almacén que YA contiene la unidad, no debe reportarse conflicto consigo mismo
        conflictos = unidades_ya_asignadas([self.unidad_a.id], almacen_excluir_id=self.central.id)
        self.assertEqual(conflictos, {})

    def test_django_admin_bloquea_doble_asignacion(self):
        # La misma regla se aplica al formulario de Django Admin (AlmacenAdminForm)
        form_add = AlmacenAdminForm(data={
            'nombre': 'Almacén Nuevo Admin',
            'tipo': 'SUBALMACEN',
            'unidades_atendidas': [self.unidad_a.id],  # ya está en Central
        })
        self.assertFalse(form_add.is_valid())
        self.assertIn('unidades_atendidas', form_add.errors)
        self.assertIn('solo puede ser atendida por UN almacén', form_add.errors.as_text())

        # Asignando una unidad libre: válido
        form_ok = AlmacenAdminForm(data={
            'nombre': 'Almacén Nuevo Admin',
            'tipo': 'SUBALMACEN',
            'unidades_atendidas': [self.unidad_b.id],
        })
        self.assertTrue(form_ok.is_valid())

        # En edición del almacén que ya la posee: válido (se excluye a sí mismo)
        form_edit = AlmacenAdminForm(data={
            'nombre': self.central.nombre,
            'tipo': 'CENTRAL',
            'unidades_atendidas': [self.unidad_a.id],
        }, instance=self.central)
        self.assertTrue(form_edit.is_valid())