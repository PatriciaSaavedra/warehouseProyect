from django.test import TestCase

from organizacion.models import Secretaria, UnidadOrganizacional
from inventario.models import Almacen


class Tarjeta2RelacionUnidadAlmacenTest(TestCase):
    """
    Tarjeta 2: relación Unidad Organizacional → Almacén que la atiende.
    Se reutiliza la relación existente Almacen.unidades_atendidas (M2M,
    related_name='almacenes_que_atienden'); NO se crea una relación duplicada ni migración.
    """

    def setUp(self):
        self.secretaria = Secretaria.objects.create(nombre="Secretaría de Salud")
        self.unidad_a = UnidadOrganizacional.objects.create(nombre="UNASBA", secretaria=self.secretaria)
        self.unidad_b = UnidadOrganizacional.objects.create(nombre="Unidad B", secretaria=self.secretaria)

    def test_asociar_unidad_a_almacen(self):
        central = Almacen.objects.create(nombre="Almacén Central", tipo="CENTRAL")
        central.unidades_atendidas.add(self.unidad_a)
        self.assertEqual(list(central.unidades_atendidas.all()), [self.unidad_a])
        self.assertEqual(list(self.unidad_a.almacenes_que_atienden.all()), [central])

    def test_cambiar_almacen_de_una_unidad(self):
        central = Almacen.objects.create(nombre="Almacén Central", tipo="CENTRAL")
        unasba = Almacen.objects.create(nombre="Subalmacén UNASBA", tipo="SUBALMACEN")
        central.unidades_atendidas.add(self.unidad_a)
        central.unidades_atendidas.remove(self.unidad_a)
        unasba.unidades_atendidas.add(self.unidad_a)
        self.assertEqual(list(self.unidad_a.almacenes_que_atienden.all()), [unasba])
        self.assertEqual(central.unidades_atendidas.count(), 0)

    def test_varias_unidades_atendidas_por_un_mismo_almacen(self):
        central = Almacen.objects.create(nombre="Almacén Central", tipo="CENTRAL")
        central.unidades_atendidas.add(self.unidad_a, self.unidad_b)
        self.assertEqual(central.unidades_atendidas.count(), 2)

    def test_dos_almacenes_con_unidades_diferentes(self):
        central = Almacen.objects.create(nombre="Almacén Central", tipo="CENTRAL")
        unasba = Almacen.objects.create(nombre="Subalmacén UNASBA", tipo="SUBALMACEN")
        central.unidades_atendidas.add(self.unidad_a)
        unasba.unidades_atendidas.add(self.unidad_b)
        self.assertEqual(list(self.unidad_a.almacenes_que_atienden.all()), [central])
        self.assertEqual(list(self.unidad_b.almacenes_que_atienden.all()), [unasba])

    def test_secretaria_unidad_permanece_correcta(self):
        self.assertEqual(self.unidad_a.secretaria, self.secretaria)
        otra = Secretaria.objects.create(nombre="Secretaría de Educación")
        unidad_otra = UnidadOrganizacional.objects.create(nombre="Unidad C", secretaria=otra)
        self.assertEqual(unidad_otra.secretaria, otra)

    def test_unidad_sin_almacen_no_provoca_error(self):
        self.assertEqual(self.unidad_b.almacenes_que_atienden.count(), 0)

    def test_relacion_inversa_almacen_unidad(self):
        central = Almacen.objects.create(nombre="Almacén Central", tipo="CENTRAL")
        central.unidades_atendidas.add(self.unidad_a)
        self.assertEqual(central.unidades_atendidas.count(), 1)
        self.assertEqual(self.unidad_a.almacenes_que_atienden.first(), central)