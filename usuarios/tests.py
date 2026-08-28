import re
from django.test import TestCase
from django.contrib.auth.models import User
from django.db import transaction, IntegrityError

# Importamos las dependencias de organización para las pruebas de jerarquía [28]
from organizacion.models import Secretaria, UnidadAdministrativa

# Importamos directamente nuestras utilidades y validador de views.py [28]
from usuarios.views import (
    normalizar_espacios, 
    capitalizar_nombre_propio, 
    validar_datos_usuario,
    PerfilUsuario
)


class TestValidacionesAvanzadasUsuario(TestCase):

    def setUp(self):
        """
        Configura datos base antes de ejecutar cada prueba.
        """
        # Crear un usuario inicial para simular colisiones e integridad de base de datos.
        # "Password123" cumple con las reglas de complejidad (letras, mayúsculas y números)
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
        datos = {
            'username': '   ',
            'first_name': 'Juan',
            'last_name': 'Perez',
            'rol': 'UNIDAD_SOLICITANTE'
        }
        datos_norm, errores = validar_datos_usuario(datos, es_creacion=True)
        self.assertIn("El nombre de usuario (username) es obligatorio.", errores)

    def test_username_con_espacios(self):
        datos = {
            'username': 'juan perez',
            'first_name': 'Juan',
            'last_name': 'Perez',
            'rol': 'UNIDAD_SOLICITANTE'
        }
        datos_norm, errores = validar_datos_usuario(datos, es_creacion=True)
        self.assertIn("El nombre de usuario no puede contener espacios en blanco.", errores)

    def test_username_longitud_limite(self):
        datos_corto = {
            'username': 'usr',
            'first_name': 'Juan',
            'last_name': 'Perez',
            'rol': 'UNIDAD_SOLICITANTE'
        }
        datos_norm, errores_corto = validar_datos_usuario(datos_corto, es_creacion=True)
        self.assertIn("El nombre de usuario debe tener entre 4 y 30 caracteres.", errores_corto)

        datos_largo = {
            'username': 'a' * 31,
            'first_name': 'Juan',
            'last_name': 'Perez',
            'rol': 'UNIDAD_SOLICITANTE'
        }
        datos_norm, errores_largo = validar_datos_usuario(datos_largo, es_creacion=True)
        self.assertIn("El nombre de usuario debe tener entre 4 y 30 caracteres.", errores_largo)

    def test_username_caracteres_permitidos(self):
        datos_validos = {
            'username': 'juan_21-08',
            'first_name': 'Juan',
            'last_name': 'Perez',
            'email': 'juan@institucion.gob.bo',
            'rol': 'UNIDAD_SOLICITANTE',
            'password': 'Password123',
            'password_confirm': 'Password123'
        }
        datos_norm, errores_validos = validar_datos_usuario(datos_validos, es_creacion=True)
        self.assertEqual(len(errores_validos), 0)

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
        datos = {
            'username': 'ADMIN_SISTEMA',
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
        datos = {
            'username': 'patricia21',
            'first_name': 'Patriciassssss',
            'last_name': 'Saavedra',
            'rol': 'UNIDAD_SOLICITANTE'
        }
        datos_norm, errores = validar_datos_usuario(datos, es_creacion=True)
        self.assertIn(
            "El nombre ingresado no es válido debido a una repetición excesiva de caracteres (anti-spam).", 
            errores
        )

    def test_nombre_con_repeticion_normal_permitida(self):
        datos = {
            'username': 'aaron21',
            'first_name': 'Aaron',
            'last_name': 'Molina',
            'email': 'aaron@institucion.gob.bo',
            'rol': 'UNIDAD_SOLICITANTE',
            'password': 'Password123',
            'password_confirm': 'Password123'
        }
        datos_norm, errores = validar_datos_usuario(datos, es_creacion=True)
        self.assertEqual(len(errores), 0)


    # ==========================================
    # 4. PRUEBAS DE VALIDACIÓN DE EMAIL
    # ==========================================
    def test_email_vacio(self):
        datos = {
            'username': 'juan2108',
            'first_name': 'Juan',
            'last_name': 'Molina',
            'email': '   ',
            'rol': 'UNIDAD_SOLICITANTE'
        }
        datos_norm, errores = validar_datos_usuario(datos, es_creacion=True)
        self.assertIn("El correo electrónico es obligatorio para el registro institucional.", errores)

    def test_email_formato_incorrecto(self):
        datos = {
            'username': 'juan2108',
            'first_name': 'Juan',
            'last_name': 'Molina',
            'email': 'correo_invalido@gob',
            'rol': 'UNIDAD_SOLICITANTE'
        }
        datos_norm, errores = validar_datos_usuario(datos, es_creacion=True)
        self.assertIn("El formato del correo electrónico no es válido (ejemplo: usuario@institucion.gob.bo).", errores)

    def test_email_longitud_limite(self):
        datos_corto = {
            'username': 'juan2108',
            'first_name': 'Juan',
            'last_name': 'Molina',
            'email': 'a@b',
            'rol': 'UNIDAD_SOLICITANTE'
        }
        datos_norm, errores_corto = validar_datos_usuario(datos_corto, es_creacion=True)
        self.assertIn("El correo electrónico debe tener entre 5 y 100 caracteres.", errores_corto)

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
        datos = {
            'username': 'juan2108',
            'first_name': 'Juan',
            'last_name': 'Molina',
            'email': 'admin@gobernacion.gob',
            'rol': 'UNIDAD_SOLICITANTE'
        }
        datos_norm, errores = validar_datos_usuario(datos, es_creacion=True)
        self.assertIn("El correo electrónico ya está registrado por otro funcionario en el sistema.", errores)


    # ==========================================
    # 5. PRUEBAS DE SEGURIDAD DE CONTRASEÑAS
    # ==========================================
    def test_password_longitud_minima(self):
        """
        Verifica que se rechacen contraseñas menores a 8 caracteres.
        """
        datos = {
            'username': 'juan2108',
            'first_name': 'Juan',
            'last_name': 'Molina',
            'email': 'juan@institucion.gob.bo',
            'rol': 'UNIDAD_SOLICITANTE',
            'password': 'Clav31',  # 6 caracteres
            'password_confirm': 'Clav31'
        }
        datos_norm, errores = validar_datos_usuario(datos, es_creacion=True)
        self.assertIn("La contraseña debe tener entre 8 y 128 caracteres.", errores)

    def test_password_demasiado_simple(self):
        """
        Verifica que se rechacen contraseñas puramente alfabéticas, 
        puramente numéricas, o que no contengan mezcla de mayúsculas/minúsculas/números.
        """
        # Solo letras
        datos_letras = {
            'username': 'juan2108',
            'first_name': 'Juan',
            'last_name': 'Molina',
            'email': 'juan@institucion.gob.bo',
            'rol': 'UNIDAD_SOLICITANTE',
            'password': 'sololetras',
            'password_confirm': 'sololetras'
        }
        datos_norm, errores_letras = validar_datos_usuario(datos_letras, es_creacion=True)
        self.assertIn("La contraseña es demasiado simple. No puede contener únicamente letras.", errores_letras)

        # Letras sin número o sin mayúscula
        datos_debil = {
            'username': 'juan2108',
            'first_name': 'Juan',
            'last_name': 'Molina',
            'email': 'juan@institucion.gob.bo',
            'rol': 'UNIDAD_SOLICITANTE',
            'password': 'claveSinnumeros',
            'password_confirm': 'claveSinnumeros'
        }
        datos_norm, errores_debil = validar_datos_usuario(datos_debil, es_creacion=True)
        self.assertIn(
            "La contraseña es débil. Debe incluir al menos una letra mayúscula, una letra minúscula y un número.", 
            errores_debil
        )

    def test_password_no_coincide(self):
        """
        Verifica que no se permita registrar si la confirmación no coincide.
        """
        datos = {
            'username': 'juan2108',
            'first_name': 'Juan',
            'last_name': 'Molina',
            'email': 'juan@institucion.gob.bo',
            'rol': 'UNIDAD_SOLICITANTE',
            'password': 'ClaveSegura123',
            'password_confirm': 'ClaveDiferente123'
        }
        datos_norm, errores = validar_datos_usuario(datos, es_creacion=True)
        self.assertIn("La contraseña y su confirmación no coinciden.", errores)

    def test_django_almacena_password_con_hash(self):
        """
        Verifica criptográficamente que Django no almacene contraseñas en texto plano,
        sino que use hashing seguro (PBKDF2 por defecto).
        """
        usuario = User.objects.get(username="admin_sistema")
        # El string original es "Password123"
        self.assertNotEqual(usuario.password, "Password123")
        # Comprobar firma criptográfica de almacenamiento de Django
        self.assertTrue(usuario.password.startswith("pbkdf2_sha256$"))


    # ==========================================
    # 6. PRUEBA DE INTEGRIDAD DE BASE DE DATOS
    # ==========================================
    def test_error_integridad_base_datos(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                User.objects.create_user(
                    username="admin_sistema",
                    password="Password123"
                )

    # ==========================================
    # 7. PRUEBAS DE JERARQUÍA: SECRETARÍA -> UNIDAD (NUEVAS) [28]
    # ==========================================
    def test_creacion_usuario_con_secretaria_y_unidad_consistente(self):
        """
        Prueba que se permita registrar un usuario si la unidad pertenece a la secretaría seleccionada.
        """
        secretaria = Secretaria.objects.create(nombre="Secretaría de Obras Públicas", codigo="SOP")
        unidad = UnidadAdministrativa.objects.create(nombre="Infraestructura Vial", secretaria=secretaria)

        datos = {
            'username': 'funcionario_sop',
            'first_name': 'Rene',
            'last_name': 'Joaquino',
            'email': 'rene@potosi.gob.bo',
            'rol': 'UNIDAD_SOLICITANTE',
            'password': 'Password123',
            'password_confirm': 'Password123',
            'secretaria': str(secretaria.id),
            'unidad': str(unidad.id)
        }
        datos_norm, errores = validar_datos_usuario(datos, es_creacion=True)
        self.assertEqual(len(errores), 0)
        self.assertEqual(datos_norm['secretaria_id'], str(secretaria.id))
        self.assertEqual(datos_norm['unidad_id'], str(unidad.id))

    def test_creacion_usuario_con_secretaria_y_unidad_inconsistente(self):
        """
        Prueba que se impida el registro si la unidad no pertenece a la secretaría seleccionada.
        """
        sec_a = Secretaria.objects.create(nombre="Secretaría de Planificación", codigo="SDPD")
        sec_b = Secretaria.objects.create(nombre="Secretaría de Minería", codigo="SDMM")
        
        # Unidad que pertenece a la Secretaría de Minería (sec_b)
        unidad_b = UnidadAdministrativa.objects.create(nombre="Fiscalización Minera", secretaria=sec_b)

        datos = {
            'username': 'funcionario_err',
            'first_name': 'Arturo',
            'last_name': 'Malfert',
            'email': 'arturo@potosi.gob.bo',
            'rol': 'UNIDAD_SOLICITANTE',
            'password': 'Password123',
            'password_confirm': 'Password123',
            'secretaria': str(sec_a.id),  # Selección Secretaría de Planificación
            'unidad': str(unidad_b.id)     # Mismatch: Unidad de Minería
        }
        datos_norm, errores = validar_datos_usuario(datos, es_creacion=True)
        self.assertIn("La Unidad Administrativa seleccionada no pertenece a la Secretaría indicada.", errores)

        # ==========================================
    # 8. PRUEBAS DE ROLES, PERMISOS Y DESACTIVACIÓN (NUEVAS)
    # ==========================================
    def test_rol_inexistente_es_rechazado(self):
        """
        Prueba que si se intenta inyectar un rol no definido en el sistema (ej: 'HACKER'),
        el validador detenga el registro de forma segura.
        """
        datos = {
            'username': 'funcionario_h',
            'first_name': 'Ramiro',
            'last_name': 'Mendoza',
            'email': 'ramiro@potosi.gob.bo',
            'rol': 'HACKER',  # Rol inválido/manipulado
            'password': 'Password123',
            'password_confirm': 'Password123'
        }
        datos_norm, errores = validar_datos_usuario(datos, es_creacion=True)
        self.assertIn("El rol seleccionado no es válido en el sistema.", errores)

    def test_rol_requerido_bloquea_rol_incorrecto(self):
        """
        Prueba que un usuario con rol 'UNIDAD_SOLICITANTE' sea bloqueado
        e impedido de ingresar a vistas exclusivas del ADMINISTRADOR.
        """
        # Creamos un usuario con rol de Unidad Solicitante
        usuario_solicitante = User.objects.create_user(
            username="solicitante_potosi",
            email="solicitante@potosi.gob.bo",
            password="Password123",
            first_name="Juan",
            last_name="Perez"
        )
        PerfilUsuario.objects.create(
            user=usuario_solicitante,
            rol="UNIDAD_SOLICITANTE"
        )

        # Iniciamos sesión con este usuario
        self.client.login(username="solicitante_potosi", password="Password123")

        # Intentar acceder a la creación de usuarios (exclusivo de administradores)
        response = self.client.get('/usuarios/crear/')
        
        # El decorador debe redirigirlo (302) al dashboard con mensaje de error
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.endswith('/dashboard/'))

    def test_comportamiento_usuario_desactivado(self):
        """
        Prueba que un usuario inactivo tenga bloqueado el acceso al sistema.
        """
        # Desactivamos al usuario existente de prueba
        self.usuario_existente.is_active = False
        self.usuario_existente.save()

        # Intentar iniciar sesión con credenciales válidas pero cuenta inactiva
        login_exitoso = self.client.login(username='admin_sistema', password='Password123')
        
        # Django debe denegar la autenticación automática
        self.assertFalse(login_exitoso)