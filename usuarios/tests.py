import re
from django.test import TestCase
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction

# Importamos directamente nuestras utilidades y validador de views.py
from usuarios.views import (
    normalizar_espacios, 
    capitalizar_nombre_propio, 
    validar_datos_usuario
)


class TestValidacionesAvanzadasUsuario(TestCase):

    def setUp(self):
        """
        Configura datos base antes de ejecutar cada prueba.
        """
        # Crear un usuario inicial para simular colisiones e integridad de base de datos
        self.usuario_existente = User.objects.create_user(
            username="admin_sistema",
            email="admin@gobernacion.gob",
            password="Password123",
            first_name="Administrador",
            last_name="General"
        )

    # ==========================================
    # 1. PRUEBAS DE NORMALIZACIÓN DE NOMBRE PROPIO
    # ==========================================
    def test_capitalizacion_inteligente(self):
        """
        Prueba que las cadenas se conviertan a formato Tipo Título 
        manteniendo las partículas del español en minúsculas.
        """
        nombre_sucio = "JUAN DE LA CRUZ"
        nombre_limpio = capitalizar_nombre_propio(nombre_sucio)
        self.assertEqual(nombre_limpio, "Juan de la Cruz")

        nombre_mezclado = "luZCEro SAAvEDra"
        nombre_normalizado = capitalizar_nombre_propio(normalizar_espacios(nombre_mezclado))
        self.assertEqual(nombre_normalizado, "Luzcero Saavedra")


    # ==========================================
    # 2. PRUEBAS DE VALIDACIÓN DE USERNAME
    # ==========================================
    def test_username_vacio(self):
        """
        Prueba que un nombre de usuario vacío o con puros espacios sea rechazado.
        """
        datos = {
            'username': '   ',  # Únicamente espacios
            'first_name': 'Juan',
            'last_name': 'Perez',
            'rol': 'UNIDAD_SOLICITANTE'
        }
        datos_norm, errores = validar_datos_usuario(datos, es_creacion=True)
        self.assertIn("El nombre de usuario (username) es obligatorio.", errores)

    def test_username_con_espacios(self):
        """
        Prueba que un nombre de usuario con espacios intermedios sea rechazado.
        """
        datos = {
            'username': 'juan perez',
            'first_name': 'Juan',
            'last_name': 'Perez',
            'rol': 'UNIDAD_SOLICITANTE'
        }
        datos_norm, errores = validar_datos_usuario(datos, es_creacion=True)
        self.assertIn("El nombre de usuario no puede contener espacios en blanco.", errores)

    def test_username_longitud_limite(self):
        """
        Prueba los límites estrictos de longitud (mínimo 4, máximo 30 caracteres).
        """
        # Muy corto (3 caracteres)
        datos_corto = {
            'username': 'usr',
            'first_name': 'Juan',
            'last_name': 'Perez',
            'rol': 'UNIDAD_SOLICITANTE'
        }
        datos_norm, errores_corto = validar_datos_usuario(datos_corto, es_creacion=True)
        self.assertIn("El nombre de usuario debe tener entre 4 y 30 caracteres.", errores_corto)

        # Muy largo (31 caracteres)
        datos_largo = {
            'username': 'a' * 31,
            'first_name': 'Juan',
            'last_name': 'Perez',
            'rol': 'UNIDAD_SOLICITANTE'
        }
        datos_norm, errores_largo = validar_datos_usuario(datos_largo, es_creacion=True)
        self.assertIn("El nombre de usuario debe tener entre 4 y 30 caracteres.", errores_largo)

    def test_username_caracteres_permitidos(self):
        """
        Prueba que se admitan números, guiones y letras, pero no otros caracteres especiales.
        """
        # Formato válido con números y guiones -> DEBE PASAR
        datos_validos = {
            'username': 'juan_21-08',
            'first_name': 'Juan',
            'last_name': 'Perez',
            'rol': 'UNIDAD_SOLICITANTE',
            'password': 'Password123',
            'password_confirm': 'Password123'
        }
        datos_norm, errores_validos = validar_datos_usuario(datos_validos, es_creacion=True)
        self.assertEqual(len(errores_validos), 0)

        # Formato inválido con punto o arroba -> DEBE FALLAR
        datos_invalidos = {
            'username': 'juan.perez@',
            'first_name': 'Juan',
            'last_name': 'Perez',
            'rol': 'UNIDAD_SOLICITANTE'
        }
        datos_norm, errores_invalidos = validar_datos_usuario(datos_invalidos, es_creacion=True)
        self.assertIn(
            "El nombre de usuario solo puede contener letras, números, guiones (-) y guiones bajos (_).", 
            errores_invalidos
        )

    def test_username_duplicado_caso_insensible(self):
        """
        Prueba que no se permitan usernames duplicados incluso si varían mayúsculas/minúsculas.
        """
        datos = {
            'username': 'ADMIN_SISTEMA',  # ya existe "admin_sistema" en setup()
            'first_name': 'Luzcero',
            'last_name': 'Saavedra',
            'rol': 'UNIDAD_SOLICITANTE'
        }
        datos_norm, errores = validar_datos_usuario(datos, es_creacion=True)
        self.assertIn("El nombre de usuario ya está registrado por otra cuenta.", errores)


    # ==========================================
    # 3. PRUEBAS DE ANTI-SPAM (CONSECUTIVIDAD)
    # ==========================================
    def test_nombre_con_letras_repetidas_excesivas(self):
        """
        Prueba que se rechacen nombres ingresados mediante keyboard-mashing (ej: "sssss").
        """
        datos = {
            'username': 'patricia21',
            'first_name': 'Patriciassssss',  # Repetición exagerada
            'last_name': 'Saavedra',
            'rol': 'UNIDAD_SOLICITANTE'
        }
        datos_norm, errores = validar_datos_usuario(datos, es_creacion=True)
        self.assertIn(
            "El nombre ingresado no es válido debido a una repetición excesiva de caracteres (anti-spam).", 
            errores
        )

    def test_nombre_con_repeticion_normal_permitida(self):
        """
        Prueba que repeticiones naturales de caracteres (como "Aaron") no sean bloqueadas.
        """
        datos = {
            'username': 'aaron21',
            'first_name': 'Aaron',  # Doble 'A' permitida
            'last_name': 'Molina',
            'rol': 'UNIDAD_SOLICITANTE',
            'password': 'Password123',
            'password_confirm': 'Password123'
        }
        datos_norm, errores = validar_datos_usuario(datos, es_creacion=True)
        self.assertEqual(len(errores), 0)


    # ==========================================
    # 4. PRUEBA DE INTEGRIDAD DE BASE DE DATOS
    # ==========================================
    def test_error_integridad_base_datos(self):
        """
        Comprueba que la base de datos lance correctamente una excepción de integridad
        si se intenta forzar la creación de un registro con el mismo username.
        """
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                # Forzar la inserción saltándose las validaciones del validador de Django
                User.objects.create_user(
                    username="admin_sistema",  # Nombre idéntico al del setUp
                    password="Password123"
                )


# ==========================================
    # 5. PRUEBAS DE VALIDACIÓN DE EMAIL (OBLIGATORIO Y ÚNICO)
    # ==========================================
    def test_email_vacio(self):
        """
        Prueba que se rechace un registro si el correo electrónico viene vacío.
        """
        datos = {
            'username': 'juan2108',
            'first_name': 'Juan',
            'last_name': 'Molina',
            'email': '   ',  # Vacío con espacios
            'rol': 'UNIDAD_SOLICITANTE'
        }
        datos_norm, errores = validar_datos_usuario(datos, es_creacion=True)
        self.assertIn("El correo electrónico es obligatorio para el registro institucional.", errores)

    def test_email_formato_incorrecto(self):
        """
        Prueba que estructuras mal formadas de correo sean detectadas.
        """
        datos = {
            'username': 'juan2108',
            'first_name': 'Juan',
            'last_name': 'Molina',
            'email': 'correo_invalido@gob',  # Falta dominio de nivel superior
            'rol': 'UNIDAD_SOLICITANTE'
        }
        datos_norm, errores = validar_datos_usuario(datos, es_creacion=True)
        self.assertIn("El formato del correo electrónico no es válido (ejemplo: usuario@institucion.gob.bo).", errores)

    def test_email_longitud_limite(self):
        """
        Prueba límites de longitud del correo (mínimo 5, máximo 100).
        """
        # Muy corto
        datos_corto = {
            'username': 'juan2108',
            'first_name': 'Juan',
            'last_name': 'Molina',
            'email': 'a@b',
            'rol': 'UNIDAD_SOLICITANTE'
        }
        datos_norm, errores_corto = validar_datos_usuario(datos_corto, es_creacion=True)
        self.assertIn("El correo electrónico debe tener entre 5 y 100 caracteres.", errores_corto)

        # Muy largo (Ejemplo con más de 100 caracteres)
        datos_largo = {
            'username': 'juan2108',
            'first_name': 'Juan',
            'last_name': 'Molina',
            'email': ('a' * 90) + '@institucion.gob.bo',
            'rol': 'UNIDAD_SOLICITANTE'
        }
        datos_norm, errores_largo = validar_datos_usuario(datos_largo, es_creacion=True)
        self.assertIn("El correo electrónico debe tener entre 5 y 100 caracteres.", errores_largo)

    def test_email_duplicado(self):
        """
        Prueba que no se admitan correos duplicados de otros funcionarios.
        """
        datos = {
            'username': 'juan2108',
            'first_name': 'Juan',
            'last_name': 'Molina',
            'email': 'admin@gobernacion.gob',  # Ya pertenece al admin en setUp()
            'rol': 'UNIDAD_SOLICITANTE'
        }
        datos_norm, errores = validar_datos_usuario(datos, es_creacion=True)
        self.assertIn("El correo electrónico ya está registrado por otro funcionario en el sistema.", errores)