from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from usuarios.models import Medico, TipoDocumento, Usuario

from .models import Especialidad, HorarioMedico


class EspecialidadPanelTests(TestCase):
    """HU-13: gestionar especialidades desde el panel del Administrador."""

    def setUp(self):
        tipo_documento = TipoDocumento.objects.get(nombre='Cédula de ciudadanía')
        self.admin = Usuario.objects.create_user(
            username='1111111111', password='ClaveSegura123', tipo_documento=tipo_documento,
            numero_documento='1111111111', fecha_nacimiento='1990-01-01', email='admin@example.com',
        )
        self.admin.groups.add(Group.objects.get(name='Administrador'))
        self.client.login(username='1111111111', password='ClaveSegura123')

    def test_hu13_crear_especialidad_duplicada_da_error(self):
        response = self.client.post(reverse('panel_especialidad_nueva'), {
            'nombre': 'Medicina general', 'descripcion': '', 'duracion_cita_min': 25, 'activa': 'on',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context['form'], 'nombre', 'Ya existe una especialidad con este nombre.',
        )
        self.assertEqual(Especialidad.objects.filter(nombre='Medicina general').count(), 1)

    def test_hu13_desactivar_especialidad_la_excluye_del_registro_de_medicos(self):
        especialidad = Especialidad.objects.get(nombre='Odontología')

        response = self.client.post(reverse('panel_especialidad_toggle', args=[especialidad.pk]))
        self.assertRedirects(response, reverse('panel_especialidades'))
        especialidad.refresh_from_db()
        self.assertFalse(especialidad.activa)

        response = self.client.get(reverse('panel_medico_nuevo'))
        self.assertNotContains(response, 'Odontología')


class HorarioMedicoModelTests(TestCase):
    """HU-14: validaciones del modelo de horario recurrente."""

    def setUp(self):
        tipo_documento = TipoDocumento.objects.get(nombre='Cédula de ciudadanía')
        especialidad = Especialidad.objects.get(nombre='Medicina general')
        usuario = Usuario.objects.create_user(
            username='5000000001', password='ClaveSegura123', tipo_documento=tipo_documento,
            numero_documento='5000000001', fecha_nacimiento='1980-01-01', email='medico@example.com',
        )
        self.medico = Medico.objects.create(usuario=usuario, especialidad=especialidad)
        HorarioMedico.objects.create(
            medico=self.medico, dia_semana=0, hora_inicio='08:00', hora_fin='12:00',
        )

    def test_hu14_bloque_solapado_es_invalido(self):
        bloque = HorarioMedico(medico=self.medico, dia_semana=0, hora_inicio='09:00', hora_fin='11:00')
        with self.assertRaises(ValidationError):
            bloque.clean()

    def test_hu14_bloques_no_solapados_mismo_dia_son_validos(self):
        bloque = HorarioMedico(medico=self.medico, dia_semana=0, hora_inicio='14:00', hora_fin='18:00')
        bloque.clean()

    def test_hu14_hora_fin_menor_o_igual_a_inicio_es_invalida(self):
        bloque = HorarioMedico(medico=self.medico, dia_semana=1, hora_inicio='10:00', hora_fin='10:00')
        with self.assertRaises(ValidationError):
            bloque.clean()
