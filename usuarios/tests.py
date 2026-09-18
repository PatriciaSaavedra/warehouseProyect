# --- EN TU ARCHIVO usuarios/tests.py ---

from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse
from inventario.models import Almacen
from organizacion.models import Secretaria, UnidadOrganizacional
from usuarios.models import PerfilUsuario
from usuarios.views import validar_datos_usuario

class PermisosMultiAlmacenTestCase(TestCase):

    def setUp(self):
        # 1. Crear Almacenes (Central y Subalmacén A)
        self.almacen_central = Almacen.objects.create(
            nombre="Almacén Central",
            tipo="CENTRAL"
        )
        self.subalmacen_a = Almacen.objects.create(
            nombre="Subalmacén Seccional Deportes",
            tipo="SUBALMACEN"
        )

        # 2. Crear Usuarios (Juan y Administradora Emilia)
        self.juan = User.objects.create_user(username="juan_almacenero", password="password123")
        self.emilia = User.objects.create_user(username="emilia_admin", password="password123")

        # 3. Crear Perfiles de Usuario
        self.perfil_juan = PerfilUsuario.objects.create(
            user=self.juan,
            rol='ALMACENERO'
        )
        self.perfil_emilia = PerfilUsuario.objects.create(
            user=self.emilia,
            rol='ADMINISTRADOR'
        )

        # 4. Autorizar a Juan ÚNICAMENTE en el Subalmacén A (Tarjeta 3)
        self.perfil_juan.almacenes_autorizados.add(self.subalmacen_a)

    def test_tarjeta_3_control_acceso_almacen_exitoso(self):
        """
        Valida que Juan tenga acceso a su almacén asignado y el sistema
        le bloquee explícitamente el acceso al Almacén Central (Tarjeta 3).
        """
        # Juan debe poder acceder al Subalmacén A
        self.assertTrue(self.perfil_juan.tiene_acceso_almacen(self.subalmacen_a))

        # Juan NO debe poder acceder al Almacén Central (Tarjeta 3 - Control de Acceso)
        self.assertFalse(self.perfil_juan.tiene_acceso_almacen(self.almacen_central))

    def test_tarjeta_3_administrador_acceso_global(self):
        """
        Valida que un administrador tenga acceso absoluto global sin necesidad 
        de tener los almacenes explícitamente enlazados.
        """
        # Emilia es administradora y debe tener acceso total a ambos almacenes
        self.assertTrue(self.perfil_emilia.tiene_acceso_almacen(self.almacen_central))
        self.assertTrue(self.perfil_emilia.tiene_acceso_almacen(self.subalmacen_a))


class Tarjeta1CrearEditarUsuarioTest(TestCase):
    """
    Tarjeta 1: Creación y edición de usuarios con Secretaría, Unidad, Rol y
    almacenes autorizados.
    """

    def setUp(self):
        self.secretaria = Secretaria.objects.create(nombre="Secretaría de Desarrollo")
        self.unidad = UnidadOrganizacional.objects.create(
            nombre="Unidad de Sistemas",
            secretaria=self.secretaria,
        )
        self.central = Almacen.objects.create(nombre="Almacén Central", tipo="CENTRAL")
        self.unasba = Almacen.objects.create(nombre="Subalmacén UNASBA", tipo="SUBALMACEN")

        self.admin = User.objects.create_superuser(
            username="admin_tarea1",
            password="Admin1234",
            email="admin@test.gob.bo",
        )
        PerfilUsuario.objects.create(
            user=self.admin,
            rol="ADMINISTRADOR",
            secretaria=self.secretaria,
            unidad_id=self.unidad.id,
        )

        self.client = Client()
        self.client.force_login(self.admin)

    def _datos_usuario(self, **extras):
        datos = {
            'username': 'pedro_s',
            'first_name': 'Pedro',
            'last_name': 'Sanchez',
            'email': 'pedro@test.gob.bo',
            'password': 'Clave123',
            'password_confirm': 'Clave123',
            'rol': 'ALMACENERO',
            'secretaria': str(self.secretaria.id),
            'unidad': str(self.unidad.id),
        }
        datos.update(extras)
        return datos

    def test_almacenero_sin_almacen_rechazado_por_validacion(self):
        _, errores = validar_datos_usuario(
            self._datos_usuario(rol='ALMACENERO'),
            es_creacion=True,
            almacenes=[],
        )
        self.assertTrue(any('Almacenero' in e for e in errores))

    def test_almacenero_sin_almacen_rechazado_por_vista(self):
        respuesta = self.client.post(
            reverse('crear_usuario'),
            self._datos_usuario(rol='ALMACENERO'),
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertTemplateUsed(respuesta, 'usuarios/crear.html')
        self.assertFalse(User.objects.filter(username='pedro_s').exists())

    def test_crear_almacenero_con_almacen_guardado(self):
        respuesta = self.client.post(
            reverse('crear_usuario'),
            self._datos_usuario(almacenes=[str(self.unasba.id)]),
        )
        self.assertRedirects(respuesta, reverse('usuarios'))

        pedro = User.objects.get(username='pedro_s')
        perfil = pedro.perfilusuario
        self.assertEqual(perfil.rol, 'ALMACENERO')
        self.assertEqual(perfil.secretaria_id, self.secretaria.id)
        self.assertEqual(perfil.unidad_id, self.unidad.id)
        self.assertEqual(
            list(perfil.almacenes_autorizados.values_list('id', flat=True)),
            [self.unasba.id],
        )

    def test_editar_conserva_y_agrega_almacenes(self):
        pedro = User.objects.create_user(
            username='pedro_s',
            password='Clave123',
            first_name='Pedro',
            last_name='Sanchez',
            email='pedro@test.gob.bo',
        )
        perfil = PerfilUsuario.objects.create(
            user=pedro,
            rol='ALMACENERO',
            secretaria=self.secretaria,
            unidad_id=self.unidad.id,
        )
        perfil.almacenes_autorizados.add(self.unasba)

        respuesta = self.client.post(
            reverse('editar_usuario', args=[pedro.id]),
            self._datos_usuario(almacenes=[str(self.unasba.id), str(self.central.id)]),
        )
        self.assertRedirects(respuesta, reverse('usuarios'))

        perfil.refresh_from_db()
        self.assertEqual(perfil.rol, 'ALMACENERO')
        self.assertEqual(perfil.secretaria_id, self.secretaria.id)
        self.assertEqual(perfil.unidad_id, self.unidad.id)
        self.assertEqual(
            sorted(perfil.almacenes_autorizados.values_list('id', flat=True)),
            sorted([self.unasba.id, self.central.id]),
        )

    def test_crear_admin_almacenes_funciona(self):
        respuesta = self.client.post(
            reverse('crear_usuario'),
            self._datos_usuario(rol='ADMIN_ALMACENES', almacenes=[str(self.central.id)]),
        )
        self.assertRedirects(respuesta, reverse('usuarios'))
        self.assertEqual(
            User.objects.get(username='pedro_s').perfilusuario.rol,
            'ADMIN_ALMACENES',
        )

    def test_crear_kardista_conserva_organizacion(self):
        respuesta = self.client.post(
            reverse('crear_usuario'),
            self._datos_usuario(rol='KARDISTA', almacenes=[str(self.unasba.id)]),
        )
        self.assertRedirects(respuesta, reverse('usuarios'))

        perfil = User.objects.get(username='pedro_s').perfilusuario
        self.assertEqual(perfil.rol, 'KARDISTA')
        self.assertEqual(perfil.secretaria_id, self.secretaria.id)
        self.assertEqual(perfil.unidad_id, self.unidad.id)

    def test_editar_administrador_no_borra_almacenes_existentes(self):
        admin2 = User.objects.create_user(
            username='admin2',
            password='Clave123',
            email='admin2@test.gob.bo',
        )
        perfil2 = PerfilUsuario.objects.create(
            user=admin2,
            rol='ADMINISTRADOR',
            secretaria=self.secretaria,
            unidad_id=self.unidad.id,
        )
        perfil2.almacenes_autorizados.add(self.central)

        self.client.post(
            reverse('editar_usuario', args=[admin2.id]),
            self._datos_usuario(
                username='admin2',
                email='admin2@test.gob.bo',
                rol='ADMINISTRADOR',
            ),
        )

        perfil2.refresh_from_db()
        self.assertEqual(
            list(perfil2.almacenes_autorizados.values_list('id', flat=True)),
            [self.central.id],
        )

    def test_perfil_muestra_secretaria_rol_y_almacenes(self):
        pedro = User.objects.create_user(
            username='pedro_s',
            password='Clave123',
            first_name='Pedro',
            last_name='Sanchez',
            email='pedro@test.gob.bo',
        )
        perfil = PerfilUsuario.objects.create(
            user=pedro,
            rol='KARDISTA',
            secretaria=self.secretaria,
            unidad_id=self.unidad.id,
        )
        perfil.almacenes_autorizados.add(self.unasba, self.central)

        self.client.force_login(pedro)
        respuesta = self.client.get(reverse('perfil'))
        self.assertContains(respuesta, 'Subalmacén UNASBA')
        self.assertContains(respuesta, 'Almacén Central')
        self.assertContains(respuesta, 'Kardista')
        self.assertContains(respuesta, 'Secretaría de Desarrollo')


class Tarjeta2ListaUnidadesTest(TestCase):
    """
    Tarjeta 2: la vista de Unidades Organizacionales muestra el almacén que atiende
    a cada unidad (solo lectura, usando la relación existente Almacen.unidades_atendidas).
    """
    def setUp(self):
        self.secretaria = Secretaria.objects.create(nombre="Secretaría de Salud")
        self.unidad_con_almacen = UnidadOrganizacional.objects.create(
            nombre="UNASBA", secretaria=self.secretaria
        )
        self.unidad_sin_almacen = UnidadOrganizacional.objects.create(
            nombre="Dirección de Deportes", secretaria=self.secretaria
        )
        self.unasba = Almacen.objects.create(nombre="Subalmacén UNASBA", tipo="SUBALMACEN")
        self.unasba.unidades_atendidas.add(self.unidad_con_almacen)

        self.admin = User.objects.create_superuser(
            username="admin_tarjeta2_lista",
            password="Admin1234",
            email="admin3@test.gob.bo",
        )
        PerfilUsuario.objects.create(user=self.admin, rol="ADMINISTRADOR")
        self.client = Client()
        self.client.force_login(self.admin)

    def test_lista_unidades_muestra_almacen_que_atiende(self):
        respuesta = self.client.get(reverse('unidades_list'))
        self.assertContains(respuesta, 'UNASBA')
        self.assertContains(respuesta, 'Subalmacén UNASBA')

    def test_lista_unidades_muestra_no_asignado(self):
        respuesta = self.client.get(reverse('unidades_list'))
        self.assertContains(respuesta, 'Dirección de Deportes')
        self.assertContains(respuesta, 'No asignado')

    def test_creacion_y_edicion_unidad_siguen_funcionando(self):
        # Creación (no se rompe)
        respuesta = self.client.post(reverse('crear_unidad'), {
            'nombre': 'Unidad C',
            'codigo_sigep': 'DIR-03',
            'secretaria': str(self.secretaria.id),
        })
        self.assertRedirects(respuesta, reverse('unidades_list'))
        self.assertTrue(UnidadOrganizacional.objects.filter(nombre='Unidad C').exists())

        # Edición (no se rompe)
        unidad_c = UnidadOrganizacional.objects.get(nombre='Unidad C')
        respuesta = self.client.post(reverse('editar_unidad', args=[unidad_c.id]), {
            'nombre': 'Unidad C Actualizada',
            'codigo_sigep': 'DIR-03',
            'secretaria': str(self.secretaria.id),
        })
        self.assertRedirects(respuesta, reverse('unidades_list'))
        unidad_c.refresh_from_db()
        self.assertEqual(unidad_c.nombre, 'Unidad C Actualizada')


class SidebarVisibilidadTestCase(TestCase):
    """
    Tarjeta 3: El Sidebar muestra solo las opciones de navegación apropiadas
    según el rol real del usuario. Solo controla visibilidad, NO seguridad de URLs.
    Todas las verificaciones se hacen sobre href reales generados en el render
    para evitar falsos positivos por texto repetido en la página.

    Criterio de aceptación (acceso a Kardex): se considera cumplido si
    ALMACENERO y KARDISTA pueden acceder a Existencias/Materiales y desde allí
    existe el flujo real "Ver Kardex" por material (URL `kardex/<id>/`).

    "Movimientos" (/inventario/movimientos/) es el historial GLOBAL de
    movimientos y se mantiene como opción funcional independiente; NO es
    sinónimo de Kardex, que es por material y requiere <id>.
    """

    HREFS = {
        'panel': '/dashboard/',
        'solicitudes': '/solicitudes/',
        'secretarias': '/usuarios/secretarias/lista-singular/',
        'unidades': '/usuarios/unidades/lista-singular/',
        'almacenes': '/inventario/almacenes/',
        'usuarios': '/usuarios/',
        'existencias': '/inventario/existencias/',
        'stock_unidad': '/inventario/stock-unidad/',
        'entradas': '/inventario/entradas/',
        'salidas': '/inventario/salidas/',
        'transferencias': '/inventario/transferencias/',
        'lotes': '/inventario/lotes/',
        'movimientos': '/inventario/movimientos/',
        'poa': '/presupuestos/',
        'ejecucion_poa': '/presupuestos/reporte-ejecucion/',
        'compras': '/compras/',
        'auditoria': '/auditoria/',
        'reporte_consumo': '/inventario/reporte/consumo/',
        'reporte_inventario': '/inventario/reporte/pdf/',
        'reporte_inventario_pdf': '/inventario/reporte/inventario/pdf/',
    }

    # Hrefs de la sección Reportes (matriz Tarjeta 3: solo ADMINISTRADOR y ADMIN_ALMACENES)
    REPORTES = ['reporte_consumo', 'reporte_inventario', 'reporte_inventario_pdf']

    # Hrefs que jamás debe ver un rol sin administración global
    RAICES_NO_ADMIN = [
        'secretarias', 'unidades', 'usuarios', 'poa', 'compras', 'auditoria',
    ]

    def setUp(self):
        self.central = Almacen.objects.create(nombre="Almacén Central", tipo="CENTRAL")
        self.unasba = Almacen.objects.create(nombre="Subalmacén UNASBA", tipo="SUBALMACEN")
        self.farmacia = Almacen.objects.create(nombre="Subalmacén Farmacia", tipo="SUBALMACEN")
        self.client = Client()

    def _crear_usuario(self, username, rol, almacenes=()):
        user = User.objects.create_user(username=username, password="password123")
        perfil = PerfilUsuario.objects.create(user=user, rol=rol)
        for almacen in almacenes:
            perfil.almacenes_autorizados.add(almacen)
        return user

    def _get_dashboard(self, user):
        self.client.force_login(user)
        return self.client.get(reverse('dashboard'))

    def _verificar(self, user, esperados=(), no_esperados=()):
        respuesta = self._get_dashboard(user)
        self.assertEqual(respuesta.status_code, 200)
        for nombre in esperados:
            self.assertContains(
                respuesta,
                f'href="{self.HREFS[nombre]}"',
                msg_prefix=f"{user.username} debe ver '{nombre}'",
            )
        for nombre in no_esperados:
            self.assertNotContains(
                respuesta,
                f'href="{self.HREFS[nombre]}"',
                msg_prefix=f"{user.username} NO debe ver '{nombre}'",
            )
        return respuesta

    # ---------------------------------------------------------
    # CASO 9 — Todas las URLs reales del menú resuelven
    # ---------------------------------------------------------
    def test_urls_reales_del_sidebar_resuelven(self):
        nombres = [
            'dashboard', 'solicitudes', 'secretaria_list', 'unidad_list',
            'almacen_list', 'inventario_por_almacen', 'inventario_por_unidad',
            'nota_ingreso_list', 'nota_salida_list', 'transferencia_list',
            'lotes_list', 'movimientos', 'reporte_presupuestos', 'logout',
            'reporte_consumo_unidades', 'reporte_inventario',
            'reporte_inventario_oficial_pdf',
        ]
        for nombre in nombres:
            with self.subTest(nombre=nombre):
                ruta = reverse(nombre)
                self.assertTrue(ruta.startswith('/'))

    # ---------------------------------------------------------
    # CASO 1 — Unidad Solicitante: solo Panel + Solicitudes
    # ---------------------------------------------------------
    def test_caso1_unidad_solicitante_solo_panel_y_solicitudes(self):
        user = self._crear_usuario('unidad_solicitante', 'UNIDAD_SOLICITANTE')
        no_ver = self.RAICES_NO_ADMIN + [
            'almacenes', 'existencias', 'stock_unidad', 'entradas',
            'salidas', 'transferencias', 'lotes', 'movimientos',
        ]
        self._verificar(
            user,
            esperados=['panel', 'solicitudes'],
            no_esperados=no_ver,
        )

    # ---------------------------------------------------------
    # CASO 2 — Almacenero de Subalmacén: menú operativo local
    # ---------------------------------------------------------
    def test_caso2_almacenero_subalmacen_menú_operativo(self):
        user = self._crear_usuario('pedro_almacenero', 'ALMACENERO', almacenes=[self.unasba])
        respuesta = self._verificar(
            user,
            esperados=[
                'panel', 'solicitudes', 'existencias', 'stock_unidad',
                'entradas', 'salidas', 'transferencias', 'lotes', 'movimientos',
            ],
            no_esperados=self.RAICES_NO_ADMIN + ['almacenes'],
        )
        # Menú Inventario sigue siendo colapsable (<details>) para almacenero
        self.assertContains(respuesta, '<details')
        self.assertContains(respuesta, 'expand_more')

    # ---------------------------------------------------------
    # CASO 3 — KARDISTA: menú técnico (Existencias, Lotes, Movimientos).
    # El Kardex es por material y se abre desde Existencias ("Ver Kardex").
    # ---------------------------------------------------------
    def test_caso3_kardista_menú_técnico(self):
        user = self._crear_usuario('kardista', 'KARDISTA', almacenes=[self.unasba])
        respuesta = self._verificar(
            user,
            esperados=['panel', 'solicitudes', 'existencias', 'lotes', 'movimientos'],
            no_esperados=self.RAICES_NO_ADMIN + [
                'almacenes', 'stock_unidad', 'entradas', 'salidas', 'transferencias',
            ],
        )
        self.assertContains(respuesta, '<details')

    # ---------------------------------------------------------
    # CASO 4 — ADMIN_ALMACENES: administración operativa multi-almacén
    # ---------------------------------------------------------
    def test_caso4_admin_almacenes_menú_operativo_multi_almacen(self):
        user = self._crear_usuario('admin_almacenes', 'ADMIN_ALMACENES', almacenes=[self.central])
        respuesta = self._verificar(
            user,
            esperados=[
                'panel', 'solicitudes', 'almacenes', 'existencias', 'stock_unidad',
                'entradas', 'salidas', 'transferencias', 'lotes', 'movimientos',
                'reporte_consumo', 'reporte_inventario', 'reporte_inventario_pdf',
            ],
            no_esperados=['secretarias', 'unidades', 'usuarios', 'poa', 'compras', 'auditoria'],
        )
        self.assertContains(respuesta, '<details')

    # ---------------------------------------------------------
    # CASO 5 — ADMINISTRADOR: conserva menú administrativo global
    # ---------------------------------------------------------
    def test_caso5_administrador_conserva_menú_global(self):
        user = self._crear_usuario('administrador', 'ADMINISTRADOR')
        self._verificar(
            user,
            esperados=[
                'panel', 'solicitudes', 'secretarias', 'unidades', 'almacenes',
                'usuarios', 'existencias', 'stock_unidad', 'entradas', 'salidas',
                'transferencias', 'lotes', 'movimientos', 'poa', 'ejecucion_poa',
                'compras', 'auditoria',
                'reporte_consumo', 'reporte_inventario', 'reporte_inventario_pdf',
            ],
        )

    # ---------------------------------------------------------
    # REPORTES — sección del Sidebar (matriz Tarjeta 3):
    # ADMINISTRADOR y ADMIN_ALMACENES => SÍ; resto de roles => NO.
    # ---------------------------------------------------------
    def test_reportes_admin_almacenes_ve_seccion_y_3_urls(self):
        user = self._crear_usuario('adm_alm_reporte', 'ADMIN_ALMACENES', almacenes=[self.central])
        respuesta = self._get_dashboard(user)
        self.assertEqual(respuesta.status_code, 200)
        for nombre in self.REPORTES:
            self.assertContains(
                respuesta,
                f'href="{self.HREFS[nombre]}"',
                msg_prefix=f"ADMIN_ALMACENES debe ver Reporte '{nombre}'",
            )
        # La sección no aparece vacía: exactamente 3 enlaces reales de reportes
        self.assertEqual(respuesta.content.decode().count('/inventario/reporte/'), 3)

    def test_reportes_administrador_ve_seccion(self):
        user = self._crear_usuario('admin_reporte', 'ADMINISTRADOR')
        respuesta = self._get_dashboard(user)
        self.assertEqual(respuesta.status_code, 200)
        for nombre in self.REPORTES:
            self.assertContains(
                respuesta,
                f'href="{self.HREFS[nombre]}"',
                msg_prefix=f"ADMINISTRADOR debe ver Reporte '{nombre}'",
            )

    def test_reportes_almacenero_no_ve_seccion(self):
        user = self._crear_usuario('alm_reporte', 'ALMACENERO', almacenes=[self.unasba])
        respuesta = self._get_dashboard(user)
        self.assertEqual(respuesta.status_code, 200)
        for nombre in self.REPORTES:
            self.assertNotContains(
                respuesta,
                f'href="{self.HREFS[nombre]}"',
                msg_prefix=f"ALMACENERO NO debe ver Reporte '{nombre}'",
            )

    def test_reportes_kardista_no_ve_seccion(self):
        user = self._crear_usuario('kard_reporte', 'KARDISTA', almacenes=[self.unasba])
        respuesta = self._get_dashboard(user)
        self.assertEqual(respuesta.status_code, 200)
        for nombre in self.REPORTES:
            self.assertNotContains(
                respuesta,
                f'href="{self.HREFS[nombre]}"',
                msg_prefix=f"KARDISTA NO debe ver Reporte '{nombre}'",
            )

    def test_reportes_unidad_solicitante_no_ve_seccion(self):
        user = self._crear_usuario('sole_reporte', 'UNIDAD_SOLICITANTE')
        respuesta = self._get_dashboard(user)
        self.assertEqual(respuesta.status_code, 200)
        for nombre in self.REPORTES:
            self.assertNotContains(
                respuesta,
                f'href="{self.HREFS[nombre]}"',
                msg_prefix=f"UNIDAD_SOLICITANTE NO debe ver Reporte '{nombre}'",
            )

    # ---------------------------------------------------------
    # Las 3 URLs reales de Reportes resuelven con reverse() sin NoReverseMatch
    # y el Sidebar de ADMIN_ALMACENES las renderiza sin error.
    # ---------------------------------------------------------
    def test_reportes_urls_resuelven_con_reverse(self):
        nombres = [
            'reporte_consumo_unidades', 'reporte_inventario',
            'reporte_inventario_oficial_pdf',
        ]
        for nombre in nombres:
            with self.subTest(nombre=nombre):
                ruta = reverse(nombre)
                self.assertTrue(ruta.startswith('/'))
        # El render del Sidebar no genera NoReverseMatch en ningún rol de Reportes
        user = self._crear_usuario('adm_alm_rev', 'ADMIN_ALMACENES', almacenes=[self.central])
        respuesta = self._get_dashboard(user)
        self.assertEqual(respuesta.status_code, 200)
        for nombre in self.REPORTES:
            self.assertContains(
                respuesta,
                f'href="{self.HREFS[nombre]}"',
            )

    # ---------------------------------------------------------
    # CASO 6 — Usuario autenticado sin PerfilUsuario: sin 500 ni privilegios
    # ---------------------------------------------------------
    def test_caso6_usuario_sin_perfil_renderiza_sin_error(self):
        user = User.objects.create_user(username='sin_perfil', password="password123")
        respuesta = self._get_dashboard(user)
        self.assertEqual(respuesta.status_code, 200)
        # Solo ve Panel y Solicitudes, ninguna opción privilegiada
        self.assertContains(respuesta, 'href="/dashboard/"')
        self.assertContains(respuesta, 'href="/solicitudes/"')
        self.assertNotContains(respuesta, '<details')
        for nombre in self.RAICES_NO_ADMIN + ['almacenes', 'existencias']:
            self.assertNotContains(respuesta, f'href="{self.HREFS[nombre]}"')

    # ---------------------------------------------------------
    # CASO 7 — Almacenero con múltiples subalmacenes autorizados
    # ---------------------------------------------------------
    def test_caso7_almacenero_multiple_subalmacenes_no_rompe_menu(self):
        user = self._crear_usuario(
            'pedro_multi',
            'ALMACENERO',
            almacenes=[self.unasba, self.farmacia],
        )
        self._verificar(
            user,
            esperados=[
                'panel', 'solicitudes', 'existencias', 'stock_unidad',
                'entradas', 'salidas', 'transferencias', 'lotes', 'movimientos',
            ],
            no_esperados=self.RAICES_NO_ADMIN + ['almacenes'],
        )

    # ---------------------------------------------------------
    # CASO 8 — Almacenero Central conserva su menú operativo
    # ---------------------------------------------------------
    def test_caso8_almacenero_central_no_se_rompe(self):
        user = self._crear_usuario('almacenero_central', 'ALMACENERO', almacenes=[self.central])
        self._verificar(
            user,
            esperados=[
                'panel', 'solicitudes', 'existencias', 'stock_unidad',
                'entradas', 'salidas', 'transferencias', 'lotes', 'movimientos',
            ],
            no_esperados=self.RAICES_NO_ADMIN + ['almacenes'],
        )

    # ---------------------------------------------------------
    # CASO 10 — Colapsables: solo existen <details> para roles con secciones
    # ---------------------------------------------------------
    def test_caso10_unidad_solicitante_sin_menus_colapsables(self):
        user = self._crear_usuario('usol_sin_collapse', 'UNIDAD_SOLICITANTE')
        respuesta = self._get_dashboard(user)
        self.assertNotContains(respuesta, '<details')

    # ---------------------------------------------------------
    # Propiedades read-only de PerfilUsuario usadas por el Sidebar
    # ---------------------------------------------------------
    def test_propiedades_rol_perfilusuario(self):
        admin = self._crear_usuario('admin_props', 'ADMINISTRADOR')
        sole = self._crear_usuario('sole_props', 'UNIDAD_SOLICITANTE')
        alm = self._crear_usuario('alm_props', 'ALMACENERO', almacenes=[self.unasba])
        kard = self._crear_usuario('kard_props', 'KARDISTA', almacenes=[self.unasba])
        adm_alm = self._crear_usuario('adm_alm_props', 'ADMIN_ALMACENES', almacenes=[self.central])

        self.assertTrue(admin.perfilusuario.es_administrador)
        self.assertTrue(admin.perfilusuario.es_rol_inventario)
        self.assertTrue(admin.perfilusuario.es_rol_operativo_almacen)

        self.assertTrue(sole.perfilusuario.es_unidad_solicitante)
        self.assertFalse(sole.perfilusuario.es_rol_inventario)
        self.assertFalse(sole.perfilusuario.es_rol_operativo_almacen)

        self.assertTrue(alm.perfilusuario.es_almacenero)
        self.assertTrue(alm.perfilusuario.es_rol_inventario)
        self.assertTrue(alm.perfilusuario.es_rol_operativo_almacen)

        self.assertTrue(kard.perfilusuario.es_kardista)
        self.assertTrue(kard.perfilusuario.es_rol_inventario)
        self.assertFalse(kard.perfilusuario.es_rol_operativo_almacen)

        self.assertTrue(adm_alm.perfilusuario.es_admin_almacenes)
        self.assertTrue(adm_alm.perfilusuario.es_rol_inventario)
        self.assertTrue(adm_alm.perfilusuario.es_rol_operativo_almacen)