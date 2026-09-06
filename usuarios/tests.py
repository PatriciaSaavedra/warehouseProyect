# --- EN TU ARCHIVO usuarios/tests.py ---

from django.test import TestCase
from django.contrib.auth.models import User
from inventario.models import Almacen
from usuarios.models import PerfilUsuario

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