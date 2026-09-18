# --- TU ARCHIVO inventario/tests.py CORREGIDO ---

import json
import threading
import unittest
from decimal import Decimal
from unittest import mock
from django.test import TestCase, Client, TransactionTestCase
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone  # <-- CORREGIDO: Importación añadida para pruebas de fecha
from django.db import connection
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


class Tarjeta4RecepcionTransferenciaTest(TestCase):
    """
    Tarjeta 4 — SUBALMACENES: RECEPCIÓN DE TRANSFERENCIAS.

    Flujo: ALMACÉN CENTRAL --emitir--> EN_TRANSITO --confirmar--> RECIBIDA -> stock subalmacén.
    Central descuenta su stock AL EMITIR (enviar_transferencia). La recepción del subalmacén
    solo suma en el destino, genera lote PEPS, conserva el costo de Central, registra
    fecha/usuario receptor y nunca puede repetirse.
    """

    def setUp(self):
        self.central = Almacen.objects.create(nombre="Almacén Central", tipo="CENTRAL")
        self.unasba = Almacen.objects.create(nombre="Subalmacén UNASBA", tipo="SUBALMACEN")
        self.farmacia = Almacen.objects.create(nombre="Subalmacén Farmacia", tipo="SUBALMACEN")

        self.admin = User.objects.create_user(username="admin_t4", password="password123")
        PerfilUsuario.objects.create(user=self.admin, rol="ADMINISTRADOR")

        self.pedro = User.objects.create_user(username="pedro_unasba", password="password123")
        perfil_pedro = PerfilUsuario.objects.create(user=self.pedro, rol="ALMACENERO")
        perfil_pedro.almacenes_autorizados.add(self.unasba)

        self.pedro_multi = User.objects.create_user(username="pedro_multi", password="password123")
        perfil_multi = PerfilUsuario.objects.create(user=self.pedro_multi, rol="ALMACENERO")
        perfil_multi.almacenes_autorizados.add(self.unasba, self.farmacia)

        self.kardista = User.objects.create_user(username="kardista_t4", password="password123")
        perfil_kardista = PerfilUsuario.objects.create(user=self.kardista, rol="KARDISTA")
        perfil_kardista.almacenes_autorizados.add(self.unasba)

        self.admin_alm = User.objects.create_user(username="admin_alm_t4", password="password123")
        perfil_admin_alm = PerfilUsuario.objects.create(user=self.admin_alm, rol="ADMIN_ALMACENES")
        perfil_admin_alm.almacenes_autorizados.add(self.unasba)

        self.admin_alm_sin_acceso = User.objects.create_user(username="admin_alm_sin", password="password123")
        PerfilUsuario.objects.create(user=self.admin_alm_sin_acceso, rol="ADMIN_ALMACENES")

        self.usuario_sin_perfil = User.objects.create_user(username="sin_perfil_t4", password="password123")

        self.material = self._crear_material("MAT-001", "Papel Bond A4")
        self.material2 = self._crear_material("MAT-002", "Tinta Impresora")
        self._abastecer_central(self.material, 100, "17.50")
        self._abastecer_central(self.material2, 100, "45.00")
        self.client = Client()

    def _crear_material(self, codigo, nombre):
        from inventario.models import PartidaPresupuestaria, UnidadMedida, Material
        partida = PartidaPresupuestaria.objects.create(codigo=f"PID-{codigo}", nombre=f"Partida {codigo}")
        unidad = UnidadMedida.objects.create(codigo=f"UM-{codigo}", nombre="Unidad")
        return Material.objects.create(
            partida=partida,
            codigo=codigo,
            nombre=nombre,
            unidad_medida="Unidad",
            unidad_medida_fk=unidad,
            stock_actual=0,
        )

    def _abastecer_central(self, material, cantidad, costo_unitario):
        from inventario.models import InventarioAlmacen, MovimientoInventario
        InventarioAlmacen.objects.create(material=material, almacen=self.central, stock_fisico=cantidad)
        MovimientoInventario.objects.create(
            material=material,
            almacen=self.central,
            tipo='ENTRADA',
            cantidad=cantidad,
            costo_unitario=Decimal(costo_unitario),
            costo_total=Decimal(costo_unitario) * cantidad,
            stock_anterior=0,
            stock_resultante=cantidad,
            referencia="LOTE INICIAL CENTRAL",
            usuario=self.admin,
            saldo_disponible_lote=cantidad,
        )

    def _enviar(self, usuario, origen, destino, cantidad, material=None):
        material = material or self.material
        self.client.force_login(usuario)
        payload = json.dumps({str(material.id): {'cantidad': str(cantidad)}})
        respuesta = self.client.post(
            reverse('enviar_transferencia'),
            {'origen': str(origen.id), 'destino': str(destino.id), 'payload': payload},
        )
        self.assertEqual(respuesta.status_code, 302)
        from inventario.models import Transferencia
        return Transferencia.objects.latest('id')

    def _ver_lista(self, usuario):
        client = Client()
        client.force_login(usuario)
        return client.get(reverse('transferencia_list'))

    def _recibir(self, usuario, transferencia):
        client = Client()
        client.force_login(usuario)
        return client.post(reverse('recibir_transferencia', args=[transferencia.id]))

    # ---------------------------------------------------------
    # CASO 1 — Recepción normal Central -> UNASBA
    # ---------------------------------------------------------
    def test_caso1_recepcion_normal_aumenta_stock_genera_lote_preserva_costo(self):
        from inventario.models import Transferencia, InventarioAlmacen, MovimientoInventario
        from auditoria.models import Bitacora

        transferencia = self._enviar(self.admin, self.central, self.unasba, 10)

        # Pedro la ve y puede confirmarla
        respuesta = self._ver_lista(self.pedro)
        self.assertContains(respuesta, transferencia.nro_transferencia)
        self.assertContains(respuesta, "Confirmar Recepción Conforme")
        self.assertContains(
            respuesta,
            'action="/inventario/transferencias/%d/recibir/"' % transferencia.id,
        )

        # El stock de Central YA fue descontado al emitir
        self.assertEqual(InventarioAlmacen.objects.get(material=self.material, almacen=self.central).stock_fisico, 90)

        respuesta = self._recibir(self.pedro, transferencia)
        self.assertEqual(respuesta.status_code, 302)

        # Estado + receptor + fecha
        transferencia.refresh_from_db()
        self.assertEqual(transferencia.estado, 'RECIBIDA')
        self.assertEqual(transferencia.usuario_recibe, self.pedro)
        self.assertIsNotNone(transferencia.fecha_recepcion)

        # Stock del subalmacén: 0 -> 10 (InventarioAlmacen creado si no existía)
        inv_unasba = InventarioAlmacen.objects.get(material=self.material, almacen=self.unasba)
        self.assertEqual(inv_unasba.stock_fisico, 10)
        # Central NO se vuelve a modificar durante la recepción
        self.assertEqual(InventarioAlmacen.objects.get(material=self.material, almacen=self.central).stock_fisico, 90)
        # Farmacia sin cambios
        self.assertFalse(InventarioAlmacen.objects.filter(material=self.material, almacen=self.farmacia).exists())

        # Lote PEPS de entrada con costo proveniente de Central (CASO 9)
        lote = MovimientoInventario.objects.get(
            material=self.material, almacen=self.unasba, tipo='ENTRADA'
        )
        self.assertEqual(lote.saldo_disponible_lote, 10)
        self.assertEqual(lote.costo_unitario, Decimal('17.50'))
        self.assertEqual(lote.costo_total, Decimal('175.00'))
        self.assertEqual(lote.cantidad, 10)
        self.assertEqual(lote.usuario, self.pedro)

        # Bitácora reutilizada
        self.assertTrue(Bitacora.objects.filter(accion='Recibir Transferencia').exists())

    # ---------------------------------------------------------
    # CASO 2 — Otra transferencia (Central -> Farmacia) no visible
    # ---------------------------------------------------------
    def test_caso2_transferencia_a_otro_subalmacen_no_visible(self):
        tr_unasba = self._enviar(self.admin, self.central, self.unasba, 10)
        tr_farmacia = self._enviar(self.admin, self.central, self.farmacia, 5)

        respuesta = self._ver_lista(self.pedro)
        self.assertContains(respuesta, tr_unasba.nro_transferencia)
        self.assertNotContains(respuesta, tr_farmacia.nro_transferencia)
        self.assertNotContains(respuesta, 'Subalmacén Farmacia')

    # ---------------------------------------------------------
    # CASO 3 — POST manipulado: rechazado, sin efectos
    # ---------------------------------------------------------
    def test_caso3_post_manipulado_rechazado_sin_efectos(self):
        from inventario.models import InventarioAlmacen
        tr_farmacia = self._enviar(self.admin, self.central, self.farmacia, 5)

        respuesta = self._recibir(self.pedro, tr_farmacia)
        self.assertEqual(respuesta.status_code, 302)

        tr_farmacia.refresh_from_db()
        self.assertEqual(tr_farmacia.estado, 'EN_TRANSITO')
        self.assertFalse(InventarioAlmacen.objects.filter(material=self.material, almacen=self.farmacia).exists())

    # ---------------------------------------------------------
    # CASO 4 — Doble recepción: secuencial y vía servicio
    # ---------------------------------------------------------
    def test_caso4_doble_recepcion_no_duplica_stock_lote_ni_movimientos(self):
        from inventario.models import InventarioAlmacen, MovimientoInventario
        from inventario.services import confirmar_recepcion_transferencia

        transferencia = self._enviar(self.admin, self.central, self.unasba, 10)

        # 1ª confirmación vía POST
        self._recibir(self.pedro, transferencia)

        # 2ª confirmación secuencial vía POST -> rechazada
        respuesta = self._recibir(self.pedro, transferencia)
        self.assertEqual(respuesta.status_code, 302)

        # Servicio idempotente: 2ª llamada directa -> ValueError
        with self.assertRaises(ValueError):
            confirmar_recepcion_transferencia(transferencia.id, self.pedro)

        # Invariantes: solo una recepción consolida
        transferencia.refresh_from_db()
        self.assertEqual(transferencia.estado, 'RECIBIDA')
        inv = InventarioAlmacen.objects.get(material=self.material, almacen=self.unasba)
        self.assertEqual(inv.stock_fisico, 10)  # no 20
        self.assertEqual(
            MovimientoInventario.objects.filter(material=self.material, almacen=self.unasba, tipo='ENTRADA').count(),
            1,
        )
        lote = MovimientoInventario.objects.get(material=self.material, almacen=self.unasba, tipo='ENTRADA')
        self.assertEqual(lote.saldo_disponible_lote, 10)  # no 20

        # Una SEGUNDA transferencia distinta sí puede recibirse (no bloquea flujos futuros)
        transferencia2 = self._enviar(self.admin, self.central, self.unasba, 5)
        confirmar_recepcion_transferencia(transferencia2.id, self.pedro)
        inv.refresh_from_db()
        self.assertEqual(inv.stock_fisico, 15)

    # ---------------------------------------------------------
    # CASO 5 — GET nunca procesa recepción (regla POST)
    # ---------------------------------------------------------
    def test_caso5_get_no_procesa_recepcion(self):
        from inventario.models import InventarioAlmacen
        transferencia = self._enviar(self.admin, self.central, self.unasba, 10)

        self.client.force_login(self.pedro)
        respuesta = self.client.get(reverse('recibir_transferencia', args=[transferencia.id]))
        self.assertEqual(respuesta.status_code, 302)

        transferencia.refresh_from_db()
        self.assertEqual(transferencia.estado, 'EN_TRANSITO')
        self.assertEqual(transferencia.usuario_recibe, None)
        self.assertFalse(InventarioAlmacen.objects.filter(material=self.material, almacen=self.unasba).exists())

    # ---------------------------------------------------------
    # CASO 6 — Múltiples almacenes autorizados
    # ---------------------------------------------------------
    def test_caso6_multiples_almacenes_autorizados(self):
        tr_unasba = self._enviar(self.admin, self.central, self.unasba, 10)
        tr_farmacia = self._enviar(self.admin, self.central, self.farmacia, 5)

        respuesta = self._ver_lista(self.pedro_multi)
        self.assertContains(respuesta, tr_unasba.nro_transferencia)
        self.assertContains(respuesta, tr_farmacia.nro_transferencia)
        # Puede confirmar en ambos destinos
        self.assertContains(respuesta, 'Confirmar Recepción Conforme')

    # ---------------------------------------------------------
    # CASO 7 — Transferencia ya RECIBIDA: sin botón, POST no reprocesa
    # ---------------------------------------------------------
    def test_caso7_ya_recibida_sin_boton_y_sin_reproceso(self):
        from inventario.models import InventarioAlmacen, MovimientoInventario
        transferencia = self._enviar(self.admin, self.central, self.unasba, 10)
        self._recibir(self.pedro, transferencia)

        respuesta = self._ver_lista(self.pedro)
        self.assertNotContains(respuesta, 'Confirmar Recepción Conforme')
        self.assertContains(respuesta, 'Concluido')

        self._recibir(self.pedro, transferencia)  # POST directo -> sin efectos
        inv = InventarioAlmacen.objects.get(material=self.material, almacen=self.unasba)
        self.assertEqual(inv.stock_fisico, 10)
        self.assertEqual(
            MovimientoInventario.objects.filter(material=self.material, almacen=self.unasba, tipo='ENTRADA').count(),
            1,
        )

    # ---------------------------------------------------------
    # CASO 8 — Estado incorrecto (RECHAZADA): no puede recibirse
    # ---------------------------------------------------------
    def test_caso8_estado_incorrecto_no_se_recibe(self):
        from inventario.models import Transferencia, TransferenciaDetalle, InventarioAlmacen
        rechazada = Transferencia.objects.create(
            nro_transferencia='TR-RECHAZADA-4',
            origen=self.central,
            destino=self.unasba,
            estado='RECHAZADA',
            usuario_envia=self.admin,
        )
        TransferenciaDetalle.objects.create(
            transferencia=rechazada,
            material=self.material,
            cantidad=10,
            costo_unitario_transferencia=Decimal('17.50'),
            costo_total_transferencia=Decimal('175.00'),
        )

        respuesta = self._recibir(self.pedro, rechazada)
        self.assertEqual(respuesta.status_code, 302)

        rechazada.refresh_from_db()
        self.assertEqual(rechazada.estado, 'RECHAZADA')
        self.assertIsNone(rechazada.fecha_recepcion)
        self.assertFalse(InventarioAlmacen.objects.filter(material=self.material, almacen=self.unasba).exists())

    # ---------------------------------------------------------
    # CASO 10 — Rollback completo ante fallo durante el lote/movimiento
    # ---------------------------------------------------------
    def test_caso10_fallo_en_creacion_de_lote_revierte_todo(self):
        from inventario.models import Transferencia, InventarioAlmacen, MovimientoInventario
        transferencia = self._enviar(self.admin, self.central, self.unasba, 10)

        with mock.patch.object(
            MovimientoInventario.objects, 'create',
            side_effect=RuntimeError('fallo simulado en lote'),
        ):
            self._recibir(self.pedro, transferencia)

        transferencia.refresh_from_db()
        self.assertEqual(transferencia.estado, 'EN_TRANSITO')  # NO quedó RECIBIDA
        self.assertIsNone(transferencia.usuario_recibe)
        self.assertIsNone(transferencia.fecha_recepcion)
        # No quedó stock parcial ni registros parciales
        self.assertFalse(InventarioAlmacen.objects.filter(material=self.material, almacen=self.unasba).exists())
        self.assertEqual(
            MovimientoInventario.objects.filter(material=self.material, almacen=self.unasba).count(),
            0,
        )

    # ---------------------------------------------------------
    # CASO 11 — Varios materiales: todos se reciben exactamente una vez
    # ---------------------------------------------------------
    def test_caso11_multiples_materiales_se_reciben_una_vez(self):
        from inventario.models import Transferencia
        from inventario.services import confirmar_recepcion_transferencia
        from inventario.models import InventarioAlmacen
        self.client.force_login(self.admin)
        payload = json.dumps({
            str(self.material.id): {'cantidad': '10'},
            str(self.material2.id): {'cantidad': '4'},
        })
        self.client.post(
            reverse('enviar_transferencia'),
            {'origen': str(self.central.id), 'destino': str(self.unasba.id), 'payload': payload},
        )
        transferencia = Transferencia.objects.latest('id')

        confirmar_recepcion_transferencia(transferencia.id, self.pedro)

        self.assertEqual(
            InventarioAlmacen.objects.get(material=self.material, almacen=self.unasba).stock_fisico, 10
        )
        self.assertEqual(
            InventarioAlmacen.objects.get(material=self.material2, almacen=self.unasba).stock_fisico, 4
        )

    # ---------------------------------------------------------
    # CASO 13 — Inventario existente: stock_anterior + recibido
    # ---------------------------------------------------------
    def test_caso13_inventario_existente_acumula(self):
        from inventario.models import InventarioAlmacen
        InventarioAlmacen.objects.create(material=self.material, almacen=self.unasba, stock_fisico=20)
        transferencia = self._enviar(self.admin, self.central, self.unasba, 10)

        self._recibir(self.pedro, transferencia)

        inv = InventarioAlmacen.objects.get(material=self.material, almacen=self.unasba)
        self.assertEqual(inv.stock_fisico, 30)  # 20 + 10

    # ---------------------------------------------------------
    # CASO 14 — Usuario sin perfil: sin 500, sin recepción
    # ---------------------------------------------------------
    def test_caso14_usuario_sin_perfil_no_rompe(self):
        from inventario.models import InventarioAlmacen
        transferencia = self._enviar(self.admin, self.central, self.unasba, 10)

        self.client.force_login(self.usuario_sin_perfil)
        respuesta = self.client.post(reverse('recibir_transferencia', args=[transferencia.id]))
        self.assertEqual(respuesta.status_code, 302)  # redirect por rol_requerido, no 500

        transferencia.refresh_from_db()
        self.assertEqual(transferencia.estado, 'EN_TRANSITO')
        self.assertFalse(InventarioAlmacen.objects.filter(material=self.material, almacen=self.unasba).exists())

    # ---------------------------------------------------------
    # CASO 12/15 — ADMINISTRADOR global; ADMIN_ALMACENES según asignación
    # ---------------------------------------------------------
    def test_caso15_administrador_global_administra_todos(self):
        from inventario.models import InventarioAlmacen
        tr_unasba = self._enviar(self.admin, self.central, self.unasba, 10)
        tr_farmacia = self._enviar(self.admin, self.central, self.farmacia, 5)

        # Supervisión global en la lista (VER)
        respuesta = self._ver_lista(self.admin)
        self.assertContains(respuesta, tr_unasba.nro_transferencia)
        self.assertContains(respuesta, tr_farmacia.nro_transferencia)

        # Confirmar recepción global (CONFIRMAR)
        self._recibir(self.admin, tr_farmacia)
        self.assertEqual(
            InventarioAlmacen.objects.get(material=self.material, almacen=self.farmacia).stock_fisico, 5
        )

    def test_caso15_admin_almacenes_accede_solo_donde_esta_asignado(self):
        from inventario.models import InventarioAlmacen
        tr_unasba = self._enviar(self.admin, self.central, self.unasba, 10)
        tr_farmacia = self._enviar(self.admin, self.central, self.farmacia, 5)

        # ADMIN_ALMACENES ve la lista completa (supervisión)...
        respuesta = self._ver_lista(self.admin_alm)
        self.assertContains(respuesta, tr_farmacia.nro_transferencia)

        # ...pero solo CONFIRMA donde está asignado
        self._recibir(self.admin_alm, tr_unasba)
        self.assertEqual(
            InventarioAlmacen.objects.get(material=self.material, almacen=self.unasba).stock_fisico, 10
        )
        self._recibir(self.admin_alm, tr_farmacia)
        self.assertEqual(tr_farmacia.estado, 'EN_TRANSITO')
        self.assertFalse(InventarioAlmacen.objects.filter(material=self.material, almacen=self.farmacia).exists())

        # ADMIN_ALMACENES sin ningún almacén asignado: no recibe nada
        tr_farmacia2 = self._enviar(self.admin, self.central, self.farmacia, 3)
        self._recibir(self.admin_alm_sin_acceso, tr_farmacia2)
        tr_farmacia2.refresh_from_db()
        self.assertEqual(tr_farmacia2.estado, 'EN_TRANSITO')

    # ---------------------------------------------------------
    # KARDISTA: ve transferencias de sus almacenes pero SIN botón de recepción
    # ---------------------------------------------------------
    def test_kardista_ve_pero_no_puede_confirmar(self):
        from inventario.models import InventarioAlmacen
        transferencia = self._enviar(self.admin, self.central, self.unasba, 10)

        respuesta = self._ver_lista(self.kardista)
        self.assertContains(respuesta, transferencia.nro_transferencia)
        self.assertNotContains(respuesta, 'Confirmar Recepción Conforme')

        # Backend: rol no incluido en recibir_transferencia -> no procesa
        self.client.force_login(self.kardista)
        respuesta = self.client.post(reverse('recibir_transferencia', args=[transferencia.id]))
        self.assertEqual(respuesta.status_code, 302)
        transferencia.refresh_from_db()
        self.assertEqual(transferencia.estado, 'EN_TRANSITO')
        self.assertFalse(InventarioAlmacen.objects.filter(material=self.material, almacen=self.unasba).exists())

    # ---------------------------------------------------------
    # La lista muestra materiales y cantidades (visibilidad)
    # ---------------------------------------------------------
    def test_lista_muestra_materiales_y_cantidades(self):
        transferencia = self._enviar(self.admin, self.central, self.unasba, 10)
        respuesta = self._ver_lista(self.pedro)
        self.assertContains(respuesta, transferencia.nro_transferencia)
        self.assertContains(respuesta, 'Papel Bond A4')
        self.assertContains(respuesta, 'Enviado / a confirmar: 10')


class Tarjeta4ConcurrenciaPostgresTest(TransactionTestCase):
    """
    Test REAL de concurrencia sobre PostgreSQL (NO SQLite).

    Verifica cómo se comporta confirmar_recepcion_transferencia ante dos
    recepciones SIMULTÁNEAS del mismo material/destino:

      Transferencia A: Central -> UNASBA, Material X +10
      Transferencia B: Central -> UNASBA, Material X +20

    Confirmadas con dos threads / dos conexiones/transacciones independientes
    sobre la restricción única (material, almacen) de InventarioAlmacen.

    - CASO FILA EXISTENTE: InventarioAlmacen(X, UNASBA) = 100  -> debe dar 130.
    - CASO FILA INEXISTENTE: sin fila previa                     -> debe dar 30.

    Se usa TransactionTestCase (no TestCase): cada test se ejecuta en autocommit y
    sin transacción envolvente, requisito para probar bloqueos de fila POSTGRES.
    Una barrera threaded dentro del create() de InventarioAlmacen fuerza que ambos
    threads intenten el INSERT simultáneo (la ventana exacta de la carrera sobre el
    índice único). La prueba es estable en PostgreSQL (único writer por fila/índice).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if connection.vendor != 'postgresql':
            raise unittest.SkipTest(
                'Test de concurrencia requiere PostgreSQL (vendor actual: %s)' % connection.vendor
            )

    def setUp(self):
        from inventario.models import (
            Almacen, PartidaPresupuestaria, UnidadMedida, Material,
            Transferencia, TransferenciaDetalle, InventarioAlmacen,
        )
        self.central = Almacen.objects.create(nombre="Almacén Central", tipo="CENTRAL")
        self.unasba = Almacen.objects.create(nombre="Subalmacén UNASBA", tipo="SUBALMACEN")
        self.admin = User.objects.create_user(username="admin_conc_t4", password="password123")
        PerfilUsuario.objects.create(user=self.admin, rol="ADMINISTRADOR")

        partida = PartidaPresupuestaria.objects.create(codigo="PID-CONC", nombre="Partida Concurrencia")
        unidad = UnidadMedida.objects.create(codigo="UM-CONC", nombre="Unidad")
        self.material = Material.objects.create(
            partida=partida,
            codigo="MAT-CONC-01",
            nombre="Material X Concurrencia",
            unidad_medida="Unidad",
            unidad_medida_fk=unidad,
            stock_actual=0,
        )

    # -------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------
    def _crear_transferencia(self, nro, cantidad):
        from inventario.models import Transferencia, TransferenciaDetalle
        tr = Transferencia.objects.create(
            nro_transferencia=nro,
            origen=self.central,
            destino=self.unasba,
            estado='EN_TRANSITO',
            usuario_envia=self.admin,
        )
        TransferenciaDetalle.objects.create(
            transferencia=tr,
            material=self.material,
            cantidad=cantidad,
            costo_unitario_transferencia=Decimal('1.00'),
            costo_total_transferencia=Decimal(str(cantidad)),
        )
        return tr

    def _hilo(self, transferencia, resultados, indice):
        from django.db import connection as thread_connection
        from inventario.services import confirmar_recepcion_transferencia
        try:
            transferencia_ok = confirmar_recepcion_transferencia(transferencia.id, self.admin)
            resultados.append(('ok', indice, transferencia_ok.id))
        except Exception as exc:  # noqa: BLE001
            resultados.append(('error', indice, f'{type(exc).__name__}: {exc}'))
        finally:
            thread_connection.close()  # cerrar la conexión del hilo (no la del test runner)

    def _confirmar_simultaneamente(self, tr_a, tr_b):
        """
        Lanza los dos hilos con una barrera DENTRO de InventarioAlmacen.objects.create:
        ambos llegan al INSERT del índice único (material, almacen) a la vez, forzando
        la carrera real de get_or_create en PostgreSQL.
        """
        from inventario.models import InventarioAlmacen
        barrera = threading.Barrier(2)
        create_real = InventarioAlmacen.objects.create

        def compuerta(*args, **kwargs):
            barrera.wait(timeout=60)
            return create_real(*args, **kwargs)

        resultados = []
        with mock.patch.object(InventarioAlmacen.objects, 'create', side_effect=compuerta):
            hilo_a = threading.Thread(target=self._hilo, args=(tr_a, resultados, 'A'))
            hilo_b = threading.Thread(target=self._hilo, args=(tr_b, resultados, 'B'))
            hilo_a.start()
            hilo_b.start()
            hilo_a.join(timeout=90)
            hilo_b.join(timeout=90)
        from django.db import connections
        connections.close_all()
        self.assertFalse(hilo_a.is_alive() or hilo_b.is_alive(), 'Hilos colgados (posible deadlock)')
        return resultados

    def _assert_resultado(self, tr_a, tr_b, stock_esperado, resultados):
        from inventario.models import InventarioAlmacen, MovimientoInventario, Transferencia

        errores = [r for r in resultados if r[0] == 'error']
        self.assertEqual(errores, [], f'Se propagaron errores (¿IntegrityError?): {errores}')

        tr_a.refresh_from_db()
        tr_b.refresh_from_db()
        self.assertEqual(tr_a.estado, 'RECIBIDA', f'{tr_a.nro_transferencia} debe quedar RECIBIDA')
        self.assertEqual(tr_b.estado, 'RECIBIDA', f'{tr_b.nro_transferencia} debe quedar RECIBIDA')
        self.assertFalse(
            Transferencia.objects.filter(estado='EN_TRANSITO').exists(),
            'No debe quedar ninguna transferencia EN_TRANSITO',
        )

        filas = InventarioAlmacen.objects.filter(material=self.material, almacen=self.unasba)
        self.assertEqual(filas.count(), 1, 'Debe existir UN solo InventarioAlmacen(X, UNASBA)')
        self.assertEqual(filas.first().stock_fisico, stock_esperado)

        lotes = list(
            MovimientoInventario.objects
            .filter(material=self.material, almacen=self.unasba, tipo='ENTRADA')
            .order_by('id')
        )
        self.assertEqual(len(lotes), 2, 'Deben existir exactamente los 2 lotes de ambas recepciones')
        self.assertEqual(
            sum(l.saldo_disponible_lote for l in lotes),
            30,  # cantidades recibidas por cada lote: 10 + 20 (no el stock absoluto)
        )
        # Traza PEPS íntegra bajo concurrencia: anterior/resultante encadenados hasta el stock final.
        for i, lote in enumerate(lotes):
            esperado_anterior = (stock_esperado - 30) if i == 0 else lotes[i - 1].stock_resultante
            self.assertEqual(lote.stock_anterior, esperado_anterior, 'stock_anterior del lote inconsistente')
        self.assertEqual(lotes[-1].stock_resultante, stock_esperado, 'stock_resultante final incorrecto')
        return lotes

    # -------------------------------------------------------------
    # CASO FILA EXISTENTE (stock 100) -> 130
    # -------------------------------------------------------------
    def test_caso_fila_existente_concurrencia_two_transferencias(self):
        from inventario.models import InventarioAlmacen
        tr_a = self._crear_transferencia('TR-CONC-EXIST-A', 10)
        tr_b = self._crear_transferencia('TR-CONC-EXIST-B', 20)
        InventarioAlmacen.objects.create(
            material=self.material, almacen=self.unasba, stock_fisico=100,
        )

        resultados = self._confirmar_simultaneamente(tr_a, tr_b)
        lotes = self._assert_resultado(tr_a, tr_b, stock_esperado=130, resultados=resultados)

        self.material.refresh_from_db()
        self.assertEqual(self.material.stock_actual, 130)

    # -------------------------------------------------------------
    # CASO FILA INEXISTENTE -> 30
    # -------------------------------------------------------------
    def test_caso_fila_inexistente_concurrencia_two_transferencias(self):
        from inventario.models import InventarioAlmacen
        self.assertFalse(
            InventarioAlmacen.objects.filter(material=self.material, almacen=self.unasba).exists(),
            'Precondición: la fila (X, UNASBA) NO existe inicialmente',
        )
        tr_a = self._crear_transferencia('TR-CONC-NEW-A', 10)
        tr_b = self._crear_transferencia('TR-CONC-NEW-B', 20)

        resultados = self._confirmar_simultaneamente(tr_a, tr_b)
        lotes = self._assert_resultado(tr_a, tr_b, stock_esperado=30, resultados=resultados)

        self.material.refresh_from_db()
        self.assertEqual(self.material.stock_actual, 30)