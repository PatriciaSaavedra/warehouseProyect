# --- TU ARCHIVO solicitudes/tests.py ---
# Tarjeta 5 — SUBALMACENES: DESPACHO LOCAL.

import threading
import unittest
import uuid
from decimal import Decimal

from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.db import connection
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from inventario.models import (
    Almacen,
    InventarioAlmacen,
    Material,
    MovimientoInventario,
    NotaSalida,
    PartidaPresupuestaria,
    UnidadMedida,
)
from organizacion.models import UnidadOrganizacional
from presupuestos.models import POA
from solicitudes.models import DetalleSolicitud, Solicitud
from usuarios.models import PerfilUsuario

GESTION_ACTUAL = timezone.now().year


class Tarjeta5DespachoSubalmacenTestCase(TestCase):
    """
    Tarjeta 5 — SUBALMACENES: DESPACHO LOCAL.

    Escenarios funcionales (POST/GET vía Client) sobre el congelamiento del
    almacén operativo en revisar_solicitud y la operación EXCLUSIVA de toda la
    cadena posterior (preparar/entregar/rechazar/cerrar) desde ese almacén.
    """

    def setUp(self):
        self.gestion = timezone.now().year
        self.central = Almacen.objects.create(nombre="Almacén Central GAD", tipo="CENTRAL")
        self.unasba = Almacen.objects.create(nombre="Subalmacén UNASBA", tipo="SUBALMACEN")
        self.farmacia = Almacen.objects.create(nombre="Subalmacén Farmacia", tipo="SUBALMACEN")

        self.unidad_c = UnidadOrganizacional.objects.create(nombre="Unidad Clínica C")
        self.unasba.unidades_atendidas.add(self.unidad_c)

        self.users = self._crear_usuarios()

        self.material = self._crear_material("MAT-T5-001", "Papel Bond A4")
        self._abastecer(self.unasba, self.material, 100, Decimal("7.50"))

        self.nro_solicitud = 0

    # -------------------------------------------------------------
    # Fábricas / Helpers
    # -------------------------------------------------------------
    def _crear_usuarios(self):
        admin = User.objects.create_user(username="admin_t5", password="password123")
        PerfilUsuario.objects.create(user=admin, rol="ADMINISTRADOR")

        jefe = User.objects.create_user(username="jefe_t5", password="password123")
        perfil_jefe = PerfilUsuario.objects.create(user=jefe, rol="JEFE_INMEDIATO", unidad=self.unidad_c)

        pedro = User.objects.create_user(username="pedro_t5", password="password123")
        perfil_pedro = PerfilUsuario.objects.create(user=pedro, rol="ALMACENERO")
        perfil_pedro.almacenes_autorizados.add(self.unasba)

        farmaco = User.objects.create_user(username="farmaco_t5", password="password123")
        perfil_farmaco = PerfilUsuario.objects.create(user=farmaco, rol="ALMACENERO")
        perfil_farmaco.almacenes_autorizados.add(self.farmacia)

        admin_alm = User.objects.create_user(username="admin_alm_t5", password="password123")
        perfil_admin_alm = PerfilUsuario.objects.create(user=admin_alm, rol="ADMIN_ALMACENES")
        perfil_admin_alm.almacenes_autorizados.add(self.unasba)

        admin_alm_sin = User.objects.create_user(username="admin_alm_sin_t5", password="password123")
        PerfilUsuario.objects.create(user=admin_alm_sin, rol="ADMIN_ALMACENES")

        kardista = User.objects.create_user(username="kardista_t5", password="password123")
        PerfilUsuario.objects.create(user=kardista, rol="KARDISTA")

        return {
            'admin': admin, 'jefe': jefe, 'pedro': pedro, 'farmaco': farmaco,
            'admin_alm': admin_alm, 'admin_alm_sin': admin_alm_sin,
            'kardista': kardista,
        }

    def _crear_material(self, codigo, nombre):
        self.nro_unidad = getattr(self, 'nro_unidad', 0) + 1
        partida = PartidaPresupuestaria.objects.create(codigo=f"PID-{codigo}", nombre=f"Partida {nombre}")
        unidad = UnidadMedida.objects.create(
            codigo=f"UM-{self.nro_unidad:03d}",
            nombre="Unidad",
        )
        return Material.objects.create(
            partida=partida,
            codigo=codigo,
            nombre=nombre,
            unidad_medida="Unidad",
            unidad_medida_fk=unidad,
            stock_actual=0,
        )

    def _abastecer(self, almacen, material, cantidad, costo_unitario):
        InventarioAlmacen.objects.create(
            material=material, almacen=almacen, stock_fisico=cantidad, stock_reservado=0
        )
        MovimientoInventario.objects.create(
            material=material,
            almacen=almacen,
            tipo='ENTRADA',
            cantidad=cantidad,
            costo_unitario=costo_unitario,
            costo_total=costo_unitario * cantidad,
            stock_anterior=0,
            stock_resultante=cantidad,
            referencia="LOTE INICIAL DESPACHO LOCAL",
            usuario=self.users['admin'],
            saldo_disponible_lote=cantidad,
        )
        POA.objects.create(
            unidad=self.unidad_c,
            partida=material.partida,
            gestion=self.gestion,
            monto_inicial=Decimal('1000.00'),
            monto_disponible=Decimal('1000.00'),
            monto_comprometido=Decimal('0.00'),
            monto_ejecutado=Decimal('0.00'),
        )

    def _crear_solicitud(self, cantidad=10, estado='REGISTRADA', flujo='SALIDA_ALMACEN'):
        self.nro_solicitud += 1
        solicitud = Solicitud.objects.create(
            codigo=f"SOL-T5-{self.nro_solicitud:04d}-{uuid.uuid4().hex[:4]}",
            unidad_solicitante=self.unidad_c,
            solicitante=self.users['jefe'],
            fecha=timezone.now().date(),
            justificacion="Despacho local Tarjeta 5",
            estado=estado,
            flujo_atencion=flujo,
        )
        DetalleSolicitud.objects.create(
            solicitud=solicitud,
            material=self.material,
            cantidad_solicitada=cantidad,
            precio_unitario_referencial=Decimal('7.50'),
        )
        return solicitud

    def _client(self, usuario):
        cliente = Client()
        cliente.force_login(usuario)
        return cliente

    def _revisar(self, solicitud, aprobada=10):
        cliente = self._client(self.users['jefe'])
        detalle = solicitud.detalles.first()
        return cliente.post(
            reverse('revisar_solicitud', args=[solicitud.id]),
            {f'aprobado_{detalle.id}': str(aprobada)},
        )

    def _preparar(self, solicitud, usuario=None):
        usuario = usuario or self.users['pedro']
        cliente = self._client(usuario)
        return cliente.post(reverse('preparar_solicitud', args=[solicitud.id]))

    def _entregar(self, solicitud, usuario=None):
        usuario = usuario or self.users['pedro']
        cliente = self._client(usuario)
        return cliente.post(reverse('entregar_solicitud', args=[solicitud.id]))

    def _rechazar(self, solicitud, usuario=None, motivo='Material agotado en almacén'):
        usuario = usuario or self.users['jefe']
        cliente = self._client(usuario)
        return cliente.post(
            reverse('rechazar_solicitud', args=[solicitud.id]),
            {'motivo_predefinido': motivo},
        )

    def _cambiar_asignacion(self):
        """La Unidad deja de ser atendida por UNASBA y pasa a ser de Farmacia."""
        self.unasba.unidades_atendidas.remove(self.unidad_c)
        self.farmacia.unidades_atendidas.add(self.unidad_c)

    def _mensajes(self, resp):
        return [str(m) for m in get_messages(resp.wsgi_request)]

    # -------------------------------------------------------------
    # A + F + G + H: despacho EXCLUSIVO desde el almacén congelado
    # -------------------------------------------------------------
    def test_a_f_g_h_despacho_exclusivo_desde_almacen_operativo_congelado(self):
        solicitud = self._crear_solicitud(cantidad=10)

        # Revisión = congelamiento: almacen_operativo queda fijo en UNASBA.
        resp = self._revisar(solicitud, aprobada=10)
        self.assertEqual(resp.status_code, 302)
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, 'REVISADA')
        self.assertEqual(solicitud.almacen_operativo, self.unasba)

        # El responsable cambia la asignación de la Unidad DESPUÉS de la revisión.
        self._cambiar_asignacion()

        # (F) PREPARAR sigue usando UNASBA (el congelado no se re-resuelve).
        resp = self._preparar(solicitud)
        self.assertEqual(resp.status_code, 302)
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, 'PREPARADA')
        self.assertEqual(solicitud.almacen_operativo, self.unasba)

        # (A) ENTREGAR despacha únicamente desde UNASBA.
        resp = self._entregar(solicitud)
        self.assertEqual(resp.status_code, 302)
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, 'ENTREGADA')

        # (G) La Nota de Salida registra almacen_origen = almacén operativo congelado.
        nota = NotaSalida.objects.get(solicitud_origen=solicitud)
        self.assertEqual(nota.almacen_origen, self.unasba)

        # Inventario aislado: UNASBA 100 -> 90; Farmacia jamás fue tocada.
        inv_unasba = InventarioAlmacen.objects.get(material=self.material, almacen=self.unasba)
        self.assertEqual(inv_unasba.stock_fisico, 90)
        self.assertEqual(inv_unasba.stock_reservado, 0)
        self.assertFalse(
            InventarioAlmacen.objects.filter(material=self.material, almacen=self.farmacia).exists()
        )

        # (H) Traza: el ÚNICO movimiento SALIDA del despacho está en UNASBA.
        salidas = MovimientoInventario.objects.filter(tipo='SALIDA')
        self.assertEqual(salidas.count(), 1)
        self.assertEqual(salidas.first().almacen, self.unasba)
        self.assertFalse(MovimientoInventario.objects.filter(almacen=self.farmacia).exists())

        # POA ejecutado una sola vez (independiente del almacén físico).
        poa = POA.objects.get(unidad=self.unidad_c, partida=self.material.partida, gestion=self.gestion)
        self.assertEqual(poa.monto_ejecutado, Decimal('75.00'))

    # -------------------------------------------------------------
    # B / C: rechazo (liberación) desde el almacén congelado
    # -------------------------------------------------------------
    def test_c_rechazo_libera_reserva_del_almacen_operativo(self):
        solicitud = self._crear_solicitud(cantidad=10)
        self._revisar(solicitud, aprobada=10)
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.almacen_operativo, self.unasba)
        inv = InventarioAlmacen.objects.get(material=self.material, almacen=self.unasba)
        self.assertEqual(inv.stock_reservado, 10)

        # Cambio de asignación tras la revisión no afecta la liberación.
        self._cambiar_asignacion()

        resp = self._rechazar(solicitud)
        self.assertEqual(resp.status_code, 302)
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, 'RECHAZADA')

        inv.refresh_from_db()
        self.assertEqual(inv.stock_reservado, 0)
        self.assertEqual(inv.stock_fisico, 100)
        # Farmacia sin fila, sin saldos negativos, sin movimientos.
        self.assertFalse(
            InventarioAlmacen.objects.filter(material=self.material, almacen=self.farmacia).exists()
        )
        self.assertFalse(MovimientoInventario.objects.filter(tipo='SALIDA').exists())

    # -------------------------------------------------------------
    # D: unidad sin almacén configurado -> error controlado (sin fallback)
    # -------------------------------------------------------------
    def test_d_sin_almacen_activo_bloquea_la_revision(self):
        solicitud = self._crear_solicitud(cantidad=10)
        self.unasba.unidades_atendidas.remove(self.unidad_c)

        resp = self._revisar(solicitud, aprobada=10)
        self.assertEqual(resp.status_code, 302)
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, 'REGISTRADA', 'No debe avanzar sin almacén de despacho')
        self.assertIsNone(solicitud.almacen_operativo)

        inv = InventarioAlmacen.objects.get(material=self.material, almacen=self.unasba)
        self.assertEqual(inv.stock_reservado, 0, 'No debe generarse reserva fantasma')
        self.assertTrue(
            any('almacén' in m.lower() for m in self._mensajes(resp)),
            f'Debe informarse el bloqueo por almacén, mensajes: {self._mensajes(resp)}',
        )

    # -------------------------------------------------------------
    # E: unidad con 2 almacenes activos -> error de configuración
    # -------------------------------------------------------------
    def test_e_unidad_con_dos_almacenes_bloquea_la_revision(self):
        solicitud = self._crear_solicitud(cantidad=10)
        self.farmacia.unidades_atendidas.add(self.unidad_c)

        resp = self._revisar(solicitud, aprobada=10)
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, 'REGISTRADA')
        self.assertIsNone(solicitud.almacen_operativo)
        inv = InventarioAlmacen.objects.get(material=self.material, almacen=self.unasba)
        self.assertEqual(inv.stock_reservado, 0)

    # -------------------------------------------------------------
    # I: solicitudes históricas en estado intermedio con NULL -> bloqueadas
    # -------------------------------------------------------------
    def test_i_historica_intermedia_almacen_null_bloqueada(self):
        solicitud = self._crear_solicitud(cantidad=10, estado='PREPARADA')
        inv = InventarioAlmacen.objects.get(material=self.material, almacen=self.unasba)
        inv.stock_reservado = 10  # reserva "histórica" dejada a mano
        inv.save()

        # Preparar: bloqueada.
        resp = self._preparar(solicitud)
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, 'PREPARADA')

        # Entregar: bloqueada, sin salidas, sin Nota de Salida.
        resp = self._entregar(solicitud)
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, 'PREPARADA')
        self.assertEqual(NotaSalida.objects.filter(solicitud_origen=solicitud).count(), 0)
        self.assertEqual(MovimientoInventario.objects.filter(tipo='SALIDA').count(), 0)
        inv.refresh_from_db()
        self.assertEqual(inv.stock_fisico, 100)
        self.assertEqual(inv.stock_reservado, 10)

        # Rechazo: bloqueado (la reserva original no puede reconstruirse).
        resp = self._rechazar(solicitud, motivo='Regularización')
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, 'PREPARADA')
        inv.refresh_from_db()
        self.assertEqual(inv.stock_reservado, 10)

        # Cerrar una ENTREGADA histórica sin Nota de Salida: bloqueado.
        solicitud.estado = 'ENTREGADA'
        solicitud.save()
        cliente = self._client(self.users['pedro'])
        resp = cliente.post(reverse('cerrar_solicitud', args=[solicitud.id]))
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, 'ENTREGADA')

    # -------------------------------------------------------------
    # Permisos (condición 11): ADMIN_ALMACENES opera solo donde está asignado
    # -------------------------------------------------------------
    def test_admin_almacenes_asignado_prepara_y_entrega(self):
        solicitud = self._crear_solicitud(cantidad=10)
        self._revisar(solicitud, aprobada=10)
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.almacen_operativo, self.unasba)

        resp = self._preparar(solicitud, usuario=self.users['admin_alm'])
        self.assertEqual(resp.status_code, 302)
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, 'PREPARADA')

        resp = self._entregar(solicitud, usuario=self.users['admin_alm'])
        self.assertEqual(resp.status_code, 302)
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, 'ENTREGADA')

    def test_admin_almacenes_sin_asignacion_no_opera(self):
        solicitud = self._crear_solicitud(cantidad=10)
        self._revisar(solicitud, aprobada=10)
        solicitud.refresh_from_db()

        resp = self._preparar(solicitud, usuario=self.users['admin_alm_sin'])
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, 'REVISADA', 'No debe preparar sin cobertura')
        self.assertTrue(
            any('autorizaci' in m.lower() for m in self._mensajes(resp)),
            f'Debe informarse la falta de autorización: {self._mensajes(resp)}',
        )

        # Entregar (sobre una PREPARADA por otro) también bloqueado.
        self._preparar(solicitud, usuario=self.users['pedro'])
        resp = self._entregar(solicitud, usuario=self.users['admin_alm_sin'])
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, 'PREPARADA')
        self.assertEqual(NotaSalida.objects.filter(solicitud_origen=solicitud).count(), 0)

    def test_kardista_no_puede_preparar(self):
        solicitud = self._crear_solicitud(cantidad=10)
        self._revisar(solicitud, aprobada=10)
        resp = self._preparar(solicitud, usuario=self.users['kardista'])
        self.assertEqual(resp.status_code, 302)
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, 'REVISADA')

    # -------------------------------------------------------------
    # Bandeja del ALMACENERO (solo su ámbito de despacho)
    # -------------------------------------------------------------
    def test_bandeja_almacenero_solo_su_ambito(self):
        sol_unasba = self._crear_solicitud(cantidad=5)

        unidad_f = UnidadOrganizacional.objects.create(nombre="Unidad F")
        self.farmacia.unidades_atendidas.add(unidad_f)
        sol_farmacia = self._crear_solicitud(cantidad=5)
        sol_farmacia.unidad_solicitante = unidad_f
        sol_farmacia.save()

        cliente = self._client(self.users['pedro'])
        resp = cliente.get(reverse('solicitudes_general'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, sol_unasba.codigo)
        self.assertNotContains(resp, sol_farmacia.codigo)
        # KPIs acotados a la bandeja del almacenero.
        self.assertEqual(resp.context['kpi_total_folios'], 1)

    def test_bandeja_almacenero_considera_almacen_congelado(self):
        # Congelada a FARMACIA -> invisible y detalle 403.
        sol_farmacia = self._crear_solicitud(cantidad=5)
        sol_farmacia.almacen_operativo = self.farmacia
        sol_farmacia.save()

        # Congelada a UNASBA -> visible y detalle permitido.
        sol_unasba = self._crear_solicitud(cantidad=5)
        sol_unasba.almacen_operativo = self.unasba
        sol_unasba.save()

        cliente = self._client(self.users['pedro'])
        resp = cliente.get(reverse('solicitudes_general'))
        self.assertNotContains(resp, sol_farmacia.codigo)
        self.assertContains(resp, sol_unasba.codigo)

        det_farmacia = cliente.get(reverse('detalle_solicitud', args=[sol_farmacia.id]))
        self.assertEqual(det_farmacia.status_code, 403)
        det_unasba = cliente.get(reverse('detalle_solicitud', args=[sol_unasba.id]))
        self.assertEqual(det_unasba.status_code, 200)

    def test_bandeja_almacenero_excluye_unidades_con_dos_almacenes(self):
        unidad_multi = UnidadOrganizacional.objects.create(nombre="Unidad MultiAlmacén")
        self.unasba.unidades_atendidas.add(unidad_multi)
        self.farmacia.unidades_atendidas.add(unidad_multi)

        solicitud = self._crear_solicitud(cantidad=5)
        solicitud.unidad_solicitante = unidad_multi
        solicitud.save()

        cliente = self._client(self.users['pedro'])
        resp = cliente.get(reverse('solicitudes_general'))
        self.assertNotContains(resp, solicitud.codigo)

    # -------------------------------------------------------------
    # Histórico ENTREGADA: NotaSalida.almacen_origen como fuente (corrección)
    # -------------------------------------------------------------
    def test_bandeja_historica_entregada_usa_almacen_origen_de_nota_salida(self):
        # Unidad C fue atendida por UNASBA (despacho entregado contra UNASBA) y
        # AHORA pasó a depender de FARMACIA: la asignación actual NO debe ganar.
        self.farmacia.unidades_atendidas.add(self.unidad_c)

        solicitud = self._crear_solicitud(cantidad=10, estado='ENTREGADA')
        NotaSalida.objects.create(
            nro_nota="NS-T5-HIST-001",
            solicitud_origen=solicitud,
            almacen_origen=self.unasba,
            unidad_destino=self.unidad_c,
            fecha=timezone.now().date(),
            usuario=self.users['admin'],
        )

        # ALMACENERO de UNASBA: la VE (la salida histórica fue de UNASBA).
        resp_un = self._client(self.users['pedro']).get(reverse('solicitudes_general'))
        self.assertContains(resp_un, solicitud.codigo)

        # ALMACENERO de FARMACIA: NO la ve por la asignación actual de la Unidad.
        resp_fa = self._client(self.users['farmaco']).get(reverse('solicitudes_general'))
        self.assertNotContains(resp_fa, solicitud.codigo)

    def test_detalle_historica_entregada_usa_almacen_origen_de_nota_salida(self):
        self.farmacia.unidades_atendidas.add(self.unidad_c)

        solicitud = self._crear_solicitud(cantidad=10, estado='ENTREGADA')
        NotaSalida.objects.create(
            nro_nota="NS-T5-HIST-002",
            solicitud_origen=solicitud,
            almacen_origen=self.unasba,
            unidad_destino=self.unidad_c,
            fecha=timezone.now().date(),
            usuario=self.users['admin'],
        )

        # ALMACENERO de UNASBA: acceso permitido.
        resp_un = self._client(self.users['pedro']).get(reverse('detalle_solicitud', args=[solicitud.id]))
        self.assertEqual(resp_un.status_code, 200)

        # ALMACENERO de FARMACIA: acceso denegado (403).
        resp_fa = self._client(self.users['farmaco']).get(reverse('detalle_solicitud', args=[solicitud.id]))
        self.assertEqual(resp_fa.status_code, 403)

    def test_historica_congelada_prioriza_almacen_operativo(self):
        # Prioridad 1: el almacén congelado impera sobre la NotaSalida (incluso
        # inconsistente hacia UNASBA) y sobre la asignación actual de la Unidad.
        self.farmacia.unidades_atendidas.add(self.unidad_c)

        solicitud = self._crear_solicitud(cantidad=10, estado='ENTREGADA')
        solicitud.almacen_operativo = self.farmacia
        solicitud.save()

        NotaSalida.objects.create(
            nro_nota="NS-T5-HIST-003",
            solicitud_origen=solicitud,
            almacen_origen=self.unasba,
            unidad_destino=self.unidad_c,
            fecha=timezone.now().date(),
            usuario=self.users['admin'],
        )

        # ALMACENERO de FARMACIA: la ve y el detalle es 200.
        cliente_fa = self._client(self.users['farmaco'])
        resp_fa = cliente_fa.get(reverse('solicitudes_general'))
        self.assertContains(resp_fa, solicitud.codigo)
        det_fa = cliente_fa.get(reverse('detalle_solicitud', args=[solicitud.id]))
        self.assertEqual(det_fa.status_code, 200)

        # ALMACENERO de UNASBA: NI por NotaSalida NI por asignación actual.
        cliente_un = self._client(self.users['pedro'])
        resp_un = cliente_un.get(reverse('solicitudes_general'))
        self.assertNotContains(resp_un, solicitud.codigo)
        det_un = cliente_un.get(reverse('detalle_solicitud', args=[solicitud.id]))
        self.assertEqual(det_un.status_code, 403)

    def test_registrada_sin_congelar_resuelve_por_unidad_actual(self):
        # Prioridad 3: REGISTRADA sin congelar ni NotaSalida -> resolver por Unidad.
        # Unidad C es atendida por UNASBA (setUp); FARMACIA no la atiende.
        solicitud = self._crear_solicitud(cantidad=5)

        cliente_un = self._client(self.users['pedro'])
        resp_un = cliente_un.get(reverse('solicitudes_general'))
        self.assertContains(resp_un, solicitud.codigo)
        det_un = cliente_un.get(reverse('detalle_solicitud', args=[solicitud.id]))
        self.assertEqual(det_un.status_code, 200)

        cliente_fa = self._client(self.users['farmaco'])
        resp_fa = cliente_fa.get(reverse('solicitudes_general'))
        self.assertNotContains(resp_fa, solicitud.codigo)
        det_fa = cliente_fa.get(reverse('detalle_solicitud', args=[solicitud.id]))
        self.assertEqual(det_fa.status_code, 403)

    def test_registrada_sin_congelar_con_cero_o_dos_almacenes_queda_excluida(self):
        # 0 almacenes atienden la unidad: sin almacen_operativo y REGISTRADA no
        # integra la bandeja de nadie (bloqueada).
        unidad_sin = UnidadOrganizacional.objects.create(nombre="Unidad Sin Almacén")
        sol_sin = self._crear_solicitud(cantidad=5)
        sol_sin.unidad_solicitante = unidad_sin
        sol_sin.save()

        # >1 almacenes atienden la unidad: la Unidad pasa a depender de
        # UNASBA + FARMACIA -> REGISTRADA sin congelar excluida.
        self.farmacia.unidades_atendidas.add(self.unidad_c)
        sol_multi = self._crear_solicitud(cantidad=5)

        cliente = self._client(self.users['pedro'])
        resp = cliente.get(reverse('solicitudes_general'))
        self.assertNotContains(resp, sol_sin.codigo)
        self.assertNotContains(resp, sol_multi.codigo)

        det_sin = cliente.get(reverse('detalle_solicitud', args=[sol_sin.id]))
        self.assertEqual(det_sin.status_code, 403)
        det_multi = cliente.get(reverse('detalle_solicitud', args=[sol_multi.id]))
        self.assertEqual(det_multi.status_code, 403)

    def test_bandeja_y_kpis_no_duplican_por_join_nota_salida(self):
        # Dos NotaSalidas de la misma solicitud (caso extremo de JOIN): el cruce
        # hacia notas_salida generaría 2 filas sin .distinct(). Debe contar como 1.
        solicitud = self._crear_solicitud(cantidad=5)
        for i in range(2):
            NotaSalida.objects.create(
                nro_nota=f"NS-T5-HIST-00{i}",
                solicitud_origen=solicitud,
                almacen_origen=self.unasba,
                unidad_destino=self.unidad_c,
                fecha=timezone.now().date(),
                usuario=self.users['admin'],
            )

        cliente = self._client(self.users['pedro'])
        resp = cliente.get(reverse('solicitudes_general'))
        self.assertEqual(resp.status_code, 200)
        # Bandeja: una sola fila por folio.
        self.assertEqual(resp.context['solicitudes'].paginator.count, 1)
        # KPI: cuenta Solicitudes, no filas del JOIN.
        self.assertEqual(resp.context['kpi_total_folios'], 1)

    # -------------------------------------------------------------
    # Idempotencia: doble POST de entrega no duplica el despacho
    # -------------------------------------------------------------
    def test_entrega_doble_post_idempotente(self):
        solicitud = self._crear_solicitud(cantidad=10)
        self._revisar(solicitud, aprobada=10)
        self._preparar(solicitud)

        cliente = self._client(self.users['pedro'])
        resp1 = cliente.post(reverse('entregar_solicitud', args=[solicitud.id]))
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, 'ENTREGADA')
        self.assertEqual(NotaSalida.objects.filter(solicitud_origen=solicitud).count(), 1)

        # Segundo intento (reenvío/doble click): sin nuevo despacho.
        resp2 = cliente.post(reverse('entregar_solicitud', args=[solicitud.id]))
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, 'ENTREGADA')
        self.assertEqual(NotaSalida.objects.filter(solicitud_origen=solicitud).count(), 1)
        self.assertEqual(MovimientoInventario.objects.filter(tipo='SALIDA').count(), 1)

        inv = InventarioAlmacen.objects.get(material=self.material, almacen=self.unasba)
        self.assertEqual(inv.stock_fisico, 90)
        self.assertEqual(inv.stock_reservado, 0)


class Tarjeta5ConcurrenciaDobleEntregaTest(TransactionTestCase):
    """
    Concurrencia REAL sobre PostgreSQL: dos entregas SIMULTÁNEAS de la misma
    solicitud (dos threads / dos conexiones). La revalidación autoritativa del
    estado DENTRO del select_for_update debe permitir UN solo despacho.

    TransactionTestCase (no TestCase): autocommit + sin transacción envolvente,
    requisito para probar bloqueos de fila de PostgreSQL.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if connection.vendor != 'postgresql':
            raise unittest.SkipTest(
                'Test de concurrencia requiere PostgreSQL (vendor actual: %s)' % connection.vendor
            )

    def setUp(self):
        self.gestion = timezone.now().year
        self.unasba = Almacen.objects.create(nombre="Subalmacén UNASBA-CONC", tipo="SUBALMACEN")

        self.unidad_c = UnidadOrganizacional.objects.create(nombre="Unidad Clínica C-CONC")
        self.unasba.unidades_atendidas.add(self.unidad_c)
        self.unasba.unidad_organizacional = self.unidad_c
        self.unasba.save()

        self.admin = User.objects.create_user(username="admin_conc_t5", password="password123")
        PerfilUsuario.objects.create(user=self.admin, rol="ADMINISTRADOR")

        self.pedro = User.objects.create_user(username="pedro_conc_t5", password="password123")
        perfil_pedro = PerfilUsuario.objects.create(user=self.pedro, rol="ALMACENERO")
        perfil_pedro.almacenes_autorizados.add(self.unasba)

        self.jefe = User.objects.create_user(username="jefe_conc_t5", password="password123")
        PerfilUsuario.objects.create(user=self.jefe, rol="JEFE_INMEDIATO", unidad=self.unidad_c)

        partida = PartidaPresupuestaria.objects.create(codigo="PID-CONC-T5", nombre="Partida Concurrencia")
        unidad = UnidadMedida.objects.create(codigo="UM-CONC-T5", nombre="Unidad")
        self.material = Material.objects.create(
            partida=partida,
            codigo="MAT-CONC-T5",
            nombre="Material Concurrencia T5",
            unidad_medida="Unidad",
            unidad_medida_fk=unidad,
            stock_actual=0,
        )
        self._abastecer(self.unasba, self.material, 100, Decimal("7.50"))

        self.poa = POA.objects.create(
            unidad=self.unidad_c,
            partida=partida,
            gestion=self.gestion,
            monto_inicial=Decimal('1000.00'),
            monto_disponible=Decimal('1000.00'),
            monto_comprometido=Decimal('0.00'),
            monto_ejecutado=Decimal('0.00'),
        )

    def _abastecer(self, almacen, material, cantidad, costo_unitario):
        InventarioAlmacen.objects.create(
            material=material, almacen=almacen, stock_fisico=cantidad, stock_reservado=0
        )
        MovimientoInventario.objects.create(
            material=material,
            almacen=almacen,
            tipo='ENTRADA',
            cantidad=cantidad,
            costo_unitario=costo_unitario,
            costo_total=costo_unitario * cantidad,
            stock_anterior=0,
            stock_resultante=cantidad,
            referencia="LOTE INICIAL CONC T5",
            usuario=self.admin,
            saldo_disponible_lote=cantidad,
        )

    def _crear_solicitud_preparada(self):
        solicitud = Solicitud.objects.create(
            codigo=f"SOL-CONC-T5-{uuid.uuid4().hex[:6]}",
            unidad_solicitante=self.unidad_c,
            solicitante=self.jefe,
            fecha=timezone.now().date(),
            justificacion="Concurrencia doble entrega",
            estado='REVISADA',
            flujo_atencion='SALIDA_ALMACEN',
            almacen_operativo=self.unasba,
        )
        detalle = DetalleSolicitud.objects.create(
            solicitud=solicitud,
            material=self.material,
            cantidad_solicitada=10,
            cantidad_aprobada=10,
            precio_unitario_referencial=Decimal('7.50'),
        )
        inv = InventarioAlmacen.objects.get(material=self.material, almacen=self.unasba)
        inv.stock_reservado = 10
        inv.save()
        solicitud.estado = 'PREPARADA'
        solicitud.preparado_por = self.pedro
        solicitud.fecha_preparado = timezone.now()
        solicitud.save()
        return solicitud

    def _hilo_entregar(self, solicitud, usuario, resultados, indice, barrera):
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.contrib.sessions.backends.db import SessionStore
        from django.db import connection as thread_connection
        from django.test import RequestFactory

        from solicitudes.views import entregar_solicitud

        try:
            request = RequestFactory().post(reverse('entregar_solicitud', args=[solicitud.id]))
            request.user = usuario
            request.session = SessionStore()
            request._messages = FallbackStorage(request)
            request.META['HTTP_REFERER'] = reverse('solicitudes_general')
            barrera.wait(timeout=60)
            entregar_solicitud(request, solicitud.id)
            resultados.append(('ok', indice, None))
        except Exception as exc:  # noqa: BLE001
            resultados.append(('error', indice, f'{type(exc).__name__}: {exc}'))
        finally:
            thread_connection.close()

    def test_doble_entrega_concurrente_no_duplica_despacho(self):
        solicitud = self._crear_solicitud_preparada()

        resultados = []
        barrera = threading.Barrier(2)
        hilo_a = threading.Thread(target=self._hilo_entregar, args=(solicitud, self.pedro, resultados, 'A', barrera))
        hilo_b = threading.Thread(target=self._hilo_entregar, args=(solicitud, self.pedro, resultados, 'B', barrera))
        hilo_a.start()
        hilo_b.start()
        hilo_a.join(timeout=90)
        hilo_b.join(timeout=90)

        from django.db import connections
        connections.close_all()
        self.assertFalse(hilo_a.is_alive() or hilo_b.is_alive(), 'Hilos colgados (posible deadlock)')

        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, 'ENTREGADA')
        self.assertEqual(NotaSalida.objects.filter(solicitud_origen=solicitud).count(), 1)

        inv = InventarioAlmacen.objects.get(material=self.material, almacen=self.unasba)
        self.assertEqual(inv.stock_fisico, 90)
        self.assertEqual(inv.stock_reservado, 0)

        self.assertEqual(MovimientoInventario.objects.filter(tipo='SALIDA').count(), 1)

        self.poa.refresh_from_db()
        self.assertEqual(self.poa.monto_ejecutado, Decimal('75.00'))
        self.assertEqual(self.poa.monto_disponible, Decimal('925.00'))