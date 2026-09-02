from decimal import Decimal
from django.test import TestCase, RequestFactory
from django.contrib.auth.models import User
from django.contrib.messages.storage.fallback import FallbackStorage
from django.utils import timezone

from solicitudes.models import Solicitud, DetalleSolicitud
from solicitudes.views import aprobar_solicitud, rechazar_solicitud, entregar_solicitud
from presupuestos.models import POA
from inventario.models import PartidaPresupuestaria, Material, UnidadMedida
from organizacion.models import UnidadOrganizacional
# Importamos el modelo real de perfiles de usuario
from usuarios.models import PerfilUsuario

class PresupuestoAutomatedTestCase(TestCase):

    def setUp(self):
        # 1. Crear la fábrica de peticiones (RequestFactory) para simular llamadas a las vistas
        self.factory = RequestFactory()

        # 2. Crear las dependencias organizacionales e inventario
        self.unidad_organizacional = UnidadOrganizacional.objects.create(
            nombre="Dirección de Sistemas"
        )
        self.partida = PartidaPresupuestaria.objects.create(
            codigo="31100",
            nombre="Alimentos y Bebidas para Personas"
        )
        self.unidad_medida = UnidadMedida.objects.create(
            nombre="Unidad",
            codigo="UNI"
        )

        # 3. Crear un material catalogado asociado a la partida
        self.material = Material.objects.create(
            partida=self.partida,
            codigo="31100-0001",
            nombre="Agua Mineral 500ml",
            unidad_medida="Unidad",
            unidad_medida_fk=self.unidad_medida,
            stock_actual=100,
            stock_minimo=5
        )

        # 4. Crear el Techo Presupuestario (POA) para la gestión fiscal 2026
        self.poa = POA.objects.create(
            unidad=self.unidad_organizacional,
            partida=self.partida,
            gestion=2026,
            monto_inicial=Decimal('1000.00'),
            monto_disponible=Decimal('1000.00'),
            monto_comprometido=Decimal('0.00'),
            monto_ejecutado=Decimal('0.00')
        )

        # 5. Crear el usuario que operará las acciones (Administrador/Presupuestos)
        self.usuario = User.objects.create_user(
            username="analista_presupuestos",
            password="password123",
            first_name="Emilia",
            last_name="Saavedra"
        )
        
        # 6. Crear una instancia real de PerfilUsuario enlazada al usuario
        self.perfil = PerfilUsuario.objects.create(
            user=self.usuario,
            rol='ADMINISTRADOR',
            unidad=self.unidad_organizacional
        )

    def _preparar_request_con_mensajes(self, request):
        """Método auxiliar para simular el almacenamiento de mensajes flash de Django en la petición"""
        setattr(request, 'session', 'session_mock')
        messages = FallbackStorage(request)
        setattr(request, '_messages', messages)
        return request

    def test_tarjeta_8_aprobacion_con_presupuesto_suficiente(self):
        """
        Prueba que si el POA de la unidad tiene fondos suficientes, se aprueba la solicitud (Tarjeta 8)
        y los fondos estimados pasan a estado Comprometido (Tarjeta 9).
        """
        # Crear solicitud en estado VALIDADA_SAF y flujo de ADQUISICION (Requiere POA)
        solicitud = Solicitud.objects.create(
            codigo="REQ-ADQ-00001",
            unidad_solicitante=self.unidad_organizacional,
            solicitante=self.usuario,
            fecha=timezone.now().date(),
            justificacion="Requerimiento para reunión de coordinación",
            tipo_requerimiento="BIEN",
            flujo_atencion="ADQUISICION",
            estado="VALIDADA_SAF"
        )

        # Detalle de 10 unidades a Bs. 15.00 cada una (Total estimado: Bs. 150.00)
        DetalleSolicitud.objects.create(
            solicitud=solicitud,
            material=self.material,
            cantidad_solicitada=10,
            precio_unitario_referencial=Decimal('15.00')
        )

        # Simular llamada a la vista aprobar_solicitud
        request = self.factory.post(f'/solicitudes/aprobar/{solicitud.id}/')
        request.user = self.usuario
        request = self._preparar_request_con_mensajes(request)

        # Ejecutar la vista
        response = aprobar_solicitud(request, id=solicitud.id)

        # Recargar los objetos de la base de datos
        solicitud.refresh_from_db()
        self.poa.refresh_from_db()

        # Verificar resultados
        self.assertEqual(solicitud.estado, "VALIDADA_PRESUPUESTOS")
        self.assertEqual(self.poa.monto_comprometido, Decimal('150.00'))
        self.assertEqual(self.poa.monto_disponible, Decimal('850.00'))  # 1000 - 150

    def test_tarjeta_8_bloqueo_por_presupuesto_insuficiente(self):
        """
        Prueba que si el monto estimado supera el saldo disponible del POA, el sistema bloquea
        la aprobación (Tarjeta 8) y no altera los montos del POA (Tarjeta 9).
        """
        solicitud = Solicitud.objects.create(
            codigo="REQ-ADQ-00002",
            unidad_solicitante=self.unidad_organizacional,
            solicitante=self.usuario,
            fecha=timezone.now().date(),
            justificacion="Requerimiento masivo",
            tipo_requerimiento="BIEN",
            flujo_atencion="ADQUISICION",
            estado="VALIDADA_SAF"
        )

        # Detalle de 100 unidades a Bs. 15.00 (Total estimado: Bs. 1500.00 -> supera los Bs. 1000.00 del POA)
        DetalleSolicitud.objects.create(
            solicitud=solicitud,
            material=self.material,
            cantidad_solicitada=100,
            precio_unitario_referencial=Decimal('15.00')
        )

        request = self.factory.post(f'/solicitudes/aprobar/{solicitud.id}/')
        request.user = self.usuario
        request = self._preparar_request_con_mensajes(request)

        # Ejecutar la vista
        response = aprobar_solicitud(request, id=solicitud.id)

        solicitud.refresh_from_db()
        self.poa.refresh_from_db()

        # Verificar que el estado siga en VALIDADA_SAF (bloqueado) y los saldos intactos
        self.assertEqual(solicitud.estado, "VALIDADA_SAF")
        self.assertEqual(self.poa.monto_disponible, Decimal('1000.00'))
        self.assertEqual(self.poa.monto_comprometido, Decimal('0.00'))

    def test_tarjeta_9_liberacion_presupuesto_al_rechazar(self):
        """
        Prueba que si una solicitud previamente comprometida (reservada) es rechazada por RPA o Jefatura,
        los fondos reservados regresen inmediatamente al presupuesto disponible (Tarjeta 9).
        """
        solicitud = Solicitud.objects.create(
            codigo="REQ-ADQ-00003",
            unidad_solicitante=self.unidad_organizacional,
            solicitante=self.usuario,
            fecha=timezone.now().date(),
            justificacion="Requerimiento que será de baja",
            tipo_requerimiento="BIEN",
            flujo_atencion="ADQUISICION",
            estado="VALIDADA_PRESUPUESTOS" # Ya comprometida
        )

        DetalleSolicitud.objects.create(
            solicitud=solicitud,
            material=self.material,
            cantidad_solicitada=20,
            precio_unitario_referencial=Decimal('10.00') # Comprometido estimado: Bs. 200.00
        )

        # Establecer la reserva inicial en el POA manualmente para emular el flujo
        self.poa.monto_comprometido = Decimal('200.00')
        self.poa.monto_disponible = Decimal('800.00')
        self.poa.save()

        # Simular llamada a la vista rechazar_solicitud
        request = self.factory.post(f'/solicitudes/rechazar/{solicitud.id}/', {
            'motivo_predefinido': 'ANULADO_POR_RPA'
        })
        request.user = self.usuario
        request = self._preparar_request_con_mensajes(request)

        # Ejecutar rechazo
        response = rechazar_solicitud(request, id=solicitud.id)

        solicitud.refresh_from_db()
        self.poa.refresh_from_db()

        # Verificar devolución de saldos
        self.assertEqual(solicitud.estado, "RECHAZADA")
        self.assertEqual(self.poa.monto_comprometido, Decimal('0.00'))
        self.assertEqual(self.poa.monto_disponible, Decimal('1000.00'))  # Retorna a 1000
# --- EN TU ARCHIVO presupuestos/tests.py (Añadir al final de la clase PresupuestoAutomatedTestCase) ---

    def test_tarjeta_10_modificacion_incremento_exitoso(self):
        """
        Prueba que al registrar una modificación de tipo INCREMENTO, 
        el saldo disponible del POA aumenta automáticamente en esa misma proporción.
        """
        from presupuestos.models import ModificacionPresupuestaria

        # Registrar incremento de Bs. 250.00
        ModificacionPresupuestaria.objects.create(
            poa=self.poa,
            tipo='INCREMENTO',
            monto=Decimal('250.00'),
            justificacion="Inyección presupuestaria por traspaso intra-institucional",
            usuario=self.usuario
        )

        self.poa.refresh_from_db()

        # Verificar que el disponible aumentó: 1000 + 250 = 1250
        self.assertEqual(self.poa.monto_disponible, Decimal('1250.00'))

    def test_tarjeta_10_modificacion_reduccion_exitosa(self):
        """
        Prueba que al registrar una modificación de tipo REDUCCION,
        el saldo disponible del POA disminuye automáticamente de forma proporcional.
        """
        from presupuestos.models import ModificacionPresupuestaria

        # Registrar reducción de Bs. 300.00
        ModificacionPresupuestaria.objects.create(
            poa=self.poa,
            tipo='REDUCCION',
            monto=Decimal('300.00'),
            justificacion="Reducción por ajuste trimestral",
            usuario=self.usuario
        )

        self.poa.refresh_from_db()

        # Verificar que el disponible disminuyó: 1000 - 300 = 700
        self.assertEqual(self.poa.monto_disponible, Decimal('700.00'))

    def test_tarjeta_10_reduccion_insuficiente_bloqueada(self):
        """
        Prueba que el sistema bloquea y arroja una ValidationError si se intenta realizar
        una reducción que supere el saldo disponible actual (evitando saldos negativos).
        """
        from django.core.exceptions import ValidationError
        from presupuestos.models import ModificacionPresupuestaria

        # Intentar reducir Bs. 1500.00 (El POA solo tiene Bs. 1000.00 disponibles)
        with self.assertRaises(ValidationError):
            ModificacionPresupuestaria.objects.create(
                poa=self.poa,
                tipo='REDUCCION',
                monto=Decimal('1500.00'),
                justificacion="Reducción que excede el saldo",
                usuario=self.usuario
            )

        self.poa.refresh_from_db()

        # El saldo disponible del POA debe mantenerse intacto en Bs. 1000.00
        self.assertEqual(self.poa.monto_disponible, Decimal('1000.00'))