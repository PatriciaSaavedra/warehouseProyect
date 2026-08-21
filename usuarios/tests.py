import re
from django.test import TestCase
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

# Importamos directamente las funciones de validación de nuestras vistas
from usuarios.views import normalizar_espacios, validar_datos_usuario


class TestValidacionesUsuario(TestCase):

    def setUp(self):
        # Configuramos un usuario inicial en la base de datos de prueba para validar unicidad
        self.usuario_existente = User.objects.create_user(
            username="admin_sistema",
            email="admin@gobernacion.gob",
            password="Password123",
            first_name="Administrador",
            last_name="General"
        )

    # 1. Comprobación de normalización de espacios
    def test_normalizacion_espacios(self):
        texto_sucio = "   Juan    Carlos   Pérez   "
        texto_limpio = normalizar_espacios(texto_sucio)
        self.assertEqual(texto_limpio, "Juan Carlos Pérez")

    # 2. Comprobación de que no se registren campos únicamente con espacios
    def test_evitar_campos_con_solo_espacios(self):
        datos = {
            'username': '   ',
            'first_name': 'Carlos',
            'last_name': 'Ramos',
            'rol': 'UNIDAD_SOLICITANTE',
            'password': 'Password123',
            'password_confirm': 'Password123'
        }
        datos_norm, errores = validar_datos_usuario(datos, es_creacion=True)
        self.assertIn("El nombre de usuario (username) es obligatorio.", errores)

    # 3. Comprobación de campos obligatorios
    def test_campos_obligatorios_vacios(self):
        datos = {
            'username': '',
            'first_name': '',
            'last_name': '',
            'rol': '',
            'password': '',
            'password_confirm': ''
        }
        datos_norm, errores = validar_datos_usuario(datos, es_creacion=True)
        self.assertIn("El nombre de usuario (username) es obligatorio.", errores)
        self.assertIn("El nombre es obligatorio y no puede contener únicamente espacios.", errores)
        self.assertIn("El apellido es obligatorio y no puede contener únicamente espacios.", errores)
        self.assertIn("El rol del usuario es obligatorio.", errores)
        self.assertIn("La contraseña es obligatoria.", errores)

    # 4. Comprobación de que el nombre/apellido no sean únicamente numéricos
    def test_nombre_no_numerico(self):
        datos = {
            'username': 'nuevousuario',
            'first_name': '12345',
            'last_name': 'Roca',
            'rol': 'UNIDAD_SOLICITANTE',
            'password': 'Password123',
            'password_confirm': 'Password123'
        }
        datos_norm, errores = validar_datos_usuario(datos, es_creacion=True)
        self.assertIn("El nombre no puede ser únicamente numérico.", errores)

    # 5. Comprobación de caracteres permitidos en nombres y apellidos
    def test_caracteres_no_permitidos_nombre(self):
        datos = {
            'username': 'nuevousuario',
            'first_name': 'Juan_Carlos#',
            'last_name': 'Roca',
            'rol': 'UNIDAD_SOLICITANTE',
            'password': 'Password123',
            'password_confirm': 'Password123'
        }
        datos_norm, errores = validar_datos_usuario(datos, es_creacion=True)
        self.assertIn("El nombre contiene caracteres no permitidos (solo se admiten letras, espacios, guiones y comillas simples).", errores)

    def test_caracteres_permitidos_especiales(self):
        # Nombres con caracteres válidos en español, guiones o comillas simples
        datos = {
            'username': 'nuevousuario',
            'first_name': "Jean-Pierre",
            'last_name': "O'Connor",
            'rol': 'UNIDAD_SOLICITANTE',
            'password': 'Password123',
            'password_confirm': 'Password123'
        }
        datos_norm, errores = validar_datos_usuario(datos, es_creacion=True)
        self.assertEqual(len(errores), 0)

    # 6. Comprobación de longitudes mínimas y máximas
    def test_longitudes_limite(self):
        # Nombre de usuario demasiado corto
        datos_corto = {
            'username': 'usr',
            'first_name': 'J',
            'last_name': 'R',
            'rol': 'UNIDAD_SOLICITANTE',
            'password': 'Pass',
            'password_confirm': 'Pass'
        }
        datos_norm, errores = validar_datos_usuario(datos_corto, es_creacion=True)
        self.assertIn("El nombre de usuario debe tener entre 4 y 150 caracteres.", errores)
        self.assertIn("El nombre debe tener entre 2 y 150 caracteres.", errores)
        self.assertIn("El apellido debe tener entre 2 y 150 caracteres.", errores)
        self.assertIn("La contraseña debe tener entre 6 y 128 caracteres.", errores)

    # 7. Comprobación de formato de correo electrónico
    def test_formato_correo_invalido(self):
        datos = {
            'username': 'nuevousuario',
            'first_name': 'Juan',
            'last_name': 'Roca',
            'email': 'correo_invalido.com',
            'rol': 'UNIDAD_SOLICITANTE',
            'password': 'Password123',
            'password_confirm': 'Password123'
        }
        datos_norm, errores = validar_datos_usuario(datos, es_creacion=True)
        self.assertIn("El formato del correo electrónico no es válido.", errores)

    # 8. Comprobación de username único (insensible a mayúsculas/minúsculas)
    def test_username_unico_duplicado(self):
        # Intentamos registrar "ADMIN_SISTEMA" (ya existe "admin_sistema")
        datos = {
            'username': 'ADMIN_SISTEMA',
            'first_name': 'Administrador',
            'last_name': 'General',
            'rol': 'ADMINISTRADOR',
            'password': 'Password123',
            'password_confirm': 'Password123'
        }
        datos_norm, errores = validar_datos_usuario(datos, es_creacion=True)
        self.assertIn("El nombre de usuario ya está registrado en el sistema.", errores)

    # 9. Comprobación de que la contraseña no empiece o termine con espacios
    def test_password_espacios_extremos(self):
        datos = {
            'username': 'nuevousuario',
            'first_name': 'Juan',
            'last_name': 'Roca',
            'rol': 'UNIDAD_SOLICITANTE',
            'password': ' Password123 ',
            'password_confirm': ' Password123 '
        }
        datos_norm, errores = validar_datos_usuario(datos, es_creacion=True)
        self.assertIn("La contraseña no debe comenzar ni terminar con espacios en blanco.", errores)

    # 10. Comprobación de confirmación de contraseña incorrecta
    def test_confirmacion_password_no_coincide(self):
        datos = {
            'username': 'nuevousuario',
            'first_name': 'Juan',
            'last_name': 'Roca',
            'rol': 'UNIDAD_SOLICITANTE',
            'password': 'Password123',
            'password_confirm': 'DiferentePassword'
        }
        datos_norm, errores = validar_datos_usuario(datos, es_creacion=True)
        self.assertIn("La contraseña y su confirmación no coinciden.", errores)