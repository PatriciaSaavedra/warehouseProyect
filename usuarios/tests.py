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