from datetime import time, timedelta
from unittest.mock import patch

from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from usuarios.htmlform_utils import datos_formulario_reales
from usuarios.models import Medico, TipoDocumento, Usuario

from .models import Cita, CitaLog, EstadoCita, Especialidad, ExcepcionHorario, HorarioMedico
from .services import cambiar_estado_cita, obtener_slots_disponibles


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

    def test_hu13_ida_y_vuelta_con_los_campos_reales_del_html(self):
        respuesta_get = self.client.get(reverse('panel_especialidad_nueva'))
        datos = datos_formulario_reales(respuesta_get.content.decode())
        datos.update({
            'nombre': 'Cardiología',
            'descripcion': 'Especialidad de prueba',
            'duracion_cita_min': 45,
        })

        respuesta_post = self.client.post(reverse('panel_especialidad_nueva'), datos)
        self.assertRedirects(respuesta_post, reverse('panel_especialidades'))

        especialidad = Especialidad.objects.get(nombre='Cardiología')
        self.assertEqual(especialidad.duracion_cita_min, 45)
        # activa viene marcada (checked) por defecto en el <input> del HTML
        self.assertTrue(especialidad.activa)

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

    def test_hu13_no_se_puede_desactivar_especialidad_con_medico_activo(self):
        tipo_documento = TipoDocumento.objects.get(nombre='Cédula de ciudadanía')
        especialidad = Especialidad.objects.get(nombre='Pediatría')
        usuario_medico = Usuario.objects.create_user(
            username='6100000001', password='ClaveSegura123', tipo_documento=tipo_documento,
            numero_documento='6100000001', fecha_nacimiento='1980-01-01', email='ped@example.com',
        )
        Medico.objects.create(usuario=usuario_medico, especialidad=especialidad)

        response = self.client.post(reverse('panel_especialidad_toggle', args=[especialidad.pk]))
        self.assertRedirects(response, reverse('panel_especialidades'))

        especialidad.refresh_from_db()
        self.assertTrue(especialidad.activa)

    def test_hu13_especialidad_con_solo_medicos_inactivos_se_puede_desactivar(self):
        tipo_documento = TipoDocumento.objects.get(nombre='Cédula de ciudadanía')
        especialidad = Especialidad.objects.get(nombre='Pediatría')
        usuario_medico = Usuario.objects.create_user(
            username='6100000002', password='ClaveSegura123', tipo_documento=tipo_documento,
            numero_documento='6100000002', fecha_nacimiento='1980-01-01', email='ped2@example.com',
        )
        Medico.objects.create(usuario=usuario_medico, especialidad=especialidad, activo=False)

        response = self.client.post(reverse('panel_especialidad_toggle', args=[especialidad.pk]))
        self.assertRedirects(response, reverse('panel_especialidades'))

        especialidad.refresh_from_db()
        self.assertFalse(especialidad.activa)

    def test_hu13_reactivar_especialidad_nunca_se_bloquea(self):
        tipo_documento = TipoDocumento.objects.get(nombre='Cédula de ciudadanía')
        especialidad = Especialidad.objects.get(nombre='Pediatría')
        especialidad.activa = False
        especialidad.save(update_fields=['activa'])

        usuario_medico = Usuario.objects.create_user(
            username='6100000003', password='ClaveSegura123', tipo_documento=tipo_documento,
            numero_documento='6100000003', fecha_nacimiento='1980-01-01', email='ped3@example.com',
        )
        Medico.objects.create(usuario=usuario_medico, especialidad=especialidad, activo=True)

        response = self.client.post(reverse('panel_especialidad_toggle', args=[especialidad.pk]))
        self.assertRedirects(response, reverse('panel_especialidades'))

        especialidad.refresh_from_db()
        self.assertTrue(especialidad.activa)


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


def crear_paciente_de_prueba(numero_documento):
    tipo_documento = TipoDocumento.objects.get(nombre='Cédula de ciudadanía')
    return Usuario.objects.create_user(
        username=numero_documento, password='ClaveSegura123', tipo_documento=tipo_documento,
        numero_documento=numero_documento, fecha_nacimiento='1995-01-01',
        email=f'{numero_documento}@example.com',
    )


def crear_medico_de_prueba(numero_documento, especialidad):
    usuario = crear_paciente_de_prueba(numero_documento)
    return Medico.objects.create(usuario=usuario, especialidad=especialidad)


class DisponibilidadServiceTests(TestCase):
    """Motor de disponibilidad (citas/services.py)."""

    def setUp(self):
        self.especialidad = Especialidad.objects.get(nombre='Pediatría')  # duracion_cita_min = 30
        self.medico = crear_medico_de_prueba('7100000001', self.especialidad)
        self.fecha_futura = timezone.localdate() + timedelta(days=7)
        self.dia_semana = self.fecha_futura.weekday()

    def test_horario_8_a_12_con_especialidad_de_30_min_genera_8_slots(self):
        HorarioMedico.objects.create(
            medico=self.medico, dia_semana=self.dia_semana, hora_inicio='08:00', hora_fin='12:00',
        )
        slots = obtener_slots_disponibles(
            self.medico, fecha_desde=self.fecha_futura, fecha_hasta=self.fecha_futura,
        )
        self.assertEqual(len(slots), 8)
        self.assertEqual(slots[0].hora_inicio, time(8, 0))
        self.assertEqual(slots[-1].hora_inicio, time(11, 30))

    def test_bloque_que_no_divide_exacto_descarta_el_remanente(self):
        HorarioMedico.objects.create(
            medico=self.medico, dia_semana=self.dia_semana, hora_inicio='08:00', hora_fin='08:50',
        )
        slots = obtener_slots_disponibles(
            self.medico, fecha_desde=self.fecha_futura, fecha_hasta=self.fecha_futura,
        )
        self.assertEqual(len(slots), 1)
        self.assertEqual(slots[0].hora_inicio, time(8, 0))
        self.assertEqual(slots[0].hora_fin, time(8, 30))

    def test_excepcion_de_dia_completo_deja_cero_slots(self):
        HorarioMedico.objects.create(
            medico=self.medico, dia_semana=self.dia_semana, hora_inicio='08:00', hora_fin='12:00',
        )
        ExcepcionHorario.objects.create(medico=self.medico, fecha=self.fecha_futura)

        slots = obtener_slots_disponibles(
            self.medico, fecha_desde=self.fecha_futura, fecha_hasta=self.fecha_futura,
        )
        self.assertEqual(slots, [])

    def test_excepcion_puntual_solo_excluye_los_slots_que_pisa(self):
        HorarioMedico.objects.create(
            medico=self.medico, dia_semana=self.dia_semana, hora_inicio='08:00', hora_fin='12:00',
        )
        ExcepcionHorario.objects.create(
            medico=self.medico, fecha=self.fecha_futura, hora_inicio='09:00', hora_fin='10:00',
        )
        slots = obtener_slots_disponibles(
            self.medico, fecha_desde=self.fecha_futura, fecha_hasta=self.fecha_futura,
        )
        horas = [slot.hora_inicio for slot in slots]
        self.assertNotIn(time(9, 0), horas)
        self.assertNotIn(time(9, 30), horas)
        self.assertIn(time(8, 0), horas)
        self.assertIn(time(10, 0), horas)
        self.assertEqual(len(slots), 6)

    def test_cita_existente_elimina_su_slot(self):
        HorarioMedico.objects.create(
            medico=self.medico, dia_semana=self.dia_semana, hora_inicio='08:00', hora_fin='12:00',
        )
        estado_confirmada = EstadoCita.objects.get(nombre='confirmada')
        Cita.objects.create(
            paciente=crear_paciente_de_prueba('7100000099'),
            medico=self.medico, estado=estado_confirmada,
            fecha=self.fecha_futura, hora_inicio='08:00', hora_fin='08:30',
        )

        slots = obtener_slots_disponibles(
            self.medico, fecha_desde=self.fecha_futura, fecha_hasta=self.fecha_futura,
        )
        self.assertEqual(len(slots), 7)
        self.assertNotIn(time(8, 0), [slot.hora_inicio for slot in slots])

    def test_cita_cancelada_libera_el_slot(self):
        HorarioMedico.objects.create(
            medico=self.medico, dia_semana=self.dia_semana, hora_inicio='08:00', hora_fin='12:00',
        )
        estado_confirmada = EstadoCita.objects.get(nombre='confirmada')
        paciente = crear_paciente_de_prueba('7100000098')

        cita = Cita.objects.create(
            paciente=paciente, medico=self.medico, estado=estado_confirmada,
            fecha=self.fecha_futura, hora_inicio='08:00', hora_fin='08:30',
        )
        slots_antes = obtener_slots_disponibles(
            self.medico, fecha_desde=self.fecha_futura, fecha_hasta=self.fecha_futura,
        )
        self.assertEqual(len(slots_antes), 7)

        cambiar_estado_cita(cita, 'cancelada', realizado_por=paciente)
        self.assertFalse(cita.ocupa_slot)

        slots_despues = obtener_slots_disponibles(
            self.medico, fecha_desde=self.fecha_futura, fecha_hasta=self.fecha_futura,
        )
        self.assertEqual(len(slots_despues), 8)
        self.assertIn(time(8, 0), [slot.hora_inicio for slot in slots_despues])

    def test_no_aparecen_slots_pasados(self):
        ahora = timezone.localtime().replace(hour=10, minute=0, second=0, microsecond=0)
        HorarioMedico.objects.create(
            medico=self.medico, dia_semana=ahora.weekday(), hora_inicio='08:00', hora_fin='12:00',
        )
        with patch('citas.services.timezone.localtime', return_value=ahora):
            slots = obtener_slots_disponibles(
                self.medico, fecha_desde=ahora.date(), fecha_hasta=ahora.date(),
            )
        horas = [slot.hora_inicio for slot in slots]
        self.assertNotIn(time(8, 0), horas)
        self.assertNotIn(time(9, 30), horas)
        self.assertNotIn(time(10, 0), horas)
        self.assertIn(time(10, 30), horas)
        self.assertEqual(len(slots), 3)


class CitaConstraintTests(TestCase):
    """Decisión de diseño 3: la UniqueConstraint parcial permite reusar el slot de una cita cancelada."""

    def setUp(self):
        especialidad = Especialidad.objects.get(nombre='Pediatría')
        self.medico = crear_medico_de_prueba('7200000001', especialidad)
        self.paciente_uno = crear_paciente_de_prueba('7200000002')
        self.paciente_dos = crear_paciente_de_prueba('7200000003')
        self.estado_confirmada = EstadoCita.objects.get(nombre='confirmada')
        self.fecha = timezone.localdate() + timedelta(days=7)

    def test_dos_citas_activas_en_el_mismo_slot_violan_la_constraint(self):
        Cita.objects.create(
            paciente=self.paciente_uno, medico=self.medico, estado=self.estado_confirmada,
            fecha=self.fecha, hora_inicio='08:00', hora_fin='08:30',
        )
        with self.assertRaises(IntegrityError):
            Cita.objects.create(
                paciente=self.paciente_dos, medico=self.medico, estado=self.estado_confirmada,
                fecha=self.fecha, hora_inicio='08:00', hora_fin='08:30',
            )

    def test_cancelar_libera_el_slot_para_una_nueva_cita(self):
        primera = Cita.objects.create(
            paciente=self.paciente_uno, medico=self.medico, estado=self.estado_confirmada,
            fecha=self.fecha, hora_inicio='08:00', hora_fin='08:30',
        )
        cambiar_estado_cita(primera, 'cancelada', realizado_por=self.paciente_uno)

        segunda = Cita.objects.create(
            paciente=self.paciente_dos, medico=self.medico, estado=self.estado_confirmada,
            fecha=self.fecha, hora_inicio='08:00', hora_fin='08:30',
        )
        self.assertTrue(segunda.pk)


class CambiarEstadoCitaServiceTests(TestCase):
    """citas.services.cambiar_estado_cita: único punto autorizado para transicionar estados."""

    def setUp(self):
        especialidad = Especialidad.objects.get(nombre='Pediatría')
        self.medico = crear_medico_de_prueba('7400000001', especialidad)
        self.paciente = crear_paciente_de_prueba('7400000002')
        self.estado_confirmada = EstadoCita.objects.get(nombre='confirmada')
        self.fecha = timezone.localdate() + timedelta(days=7)
        HorarioMedico.objects.create(
            medico=self.medico, dia_semana=self.fecha.weekday(), hora_inicio='08:00', hora_fin='09:00',
        )
        self.cita = Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=self.estado_confirmada,
            fecha=self.fecha, hora_inicio='08:00', hora_fin='08:30',
        )

    def test_cancelar_con_el_servicio_deja_ocupa_slot_false_y_libera_el_slot(self):
        cambiar_estado_cita(self.cita, 'cancelada', realizado_por=self.paciente, detalle='El paciente canceló.')

        self.cita.refresh_from_db()
        self.assertEqual(self.cita.estado.nombre, 'cancelada')
        self.assertFalse(self.cita.ocupa_slot)

        slots = obtener_slots_disponibles(self.medico, fecha_desde=self.fecha, fecha_hasta=self.fecha)
        self.assertIn(time(8, 0), [slot.hora_inicio for slot in slots])

    def test_cancelar_con_el_servicio_crea_el_citalog(self):
        cambiar_estado_cita(self.cita, 'cancelada', realizado_por=self.paciente, detalle='El paciente canceló.')

        log = CitaLog.objects.get(cita=self.cita, accion='cancelada')
        self.assertEqual(log.estado.nombre, 'cancelada')
        self.assertEqual(log.realizado_por, self.paciente)
        self.assertEqual(log.detalle, 'El paciente canceló.')

    def test_queryset_update_no_sincroniza_ocupa_slot_por_eso_no_debe_usarse(self):
        """
        Documenta a propósito el comportamiento peligroso de .update():
        bypasea Cita.save(), así que ocupa_slot queda desincronizado con
        estado. Es la razón por la que toda transición DEBE pasar por
        cambiar_estado_cita() y nunca por queryset.update()/bulk_update().
        """
        estado_cancelada = EstadoCita.objects.get(nombre='cancelada')

        Cita.objects.filter(pk=self.cita.pk).update(estado=estado_cancelada)

        self.cita.refresh_from_db()
        self.assertEqual(self.cita.estado.nombre, 'cancelada')
        self.assertTrue(
            self.cita.ocupa_slot,
            'ocupa_slot debería haber quedado desincronizado (True) tras un .update() '
            'que bypasea save(); si esto ya no es cierto, cambiar_estado_cita() dejó '
            'de ser necesario y este test/comentario debe revisarse.',
        )

        slots = obtener_slots_disponibles(self.medico, fecha_desde=self.fecha, fecha_hasta=self.fecha)
        self.assertNotIn(
            time(8, 0), [slot.hora_inicio for slot in slots],
            'El slot sigue bloqueado a pesar de la cancelación: exactamente el bug '
            'que cambiar_estado_cita() evita.',
        )


class AgendamientoViewTests(TestCase):
    """HU-03 a HU-06: flujo de agendamiento del paciente."""

    def setUp(self):
        self.especialidad = Especialidad.objects.get(nombre='Pediatría')
        self.medico = crear_medico_de_prueba('7300000001', self.especialidad)
        self.fecha_futura = timezone.localdate() + timedelta(days=7)
        HorarioMedico.objects.create(
            medico=self.medico, dia_semana=self.fecha_futura.weekday(),
            hora_inicio='08:00', hora_fin='12:00',
        )

        tipo_documento = TipoDocumento.objects.get(nombre='Cédula de ciudadanía')
        self.paciente = Usuario.objects.create_user(
            username='8100000001', password='ClaveSegura123', tipo_documento=tipo_documento,
            numero_documento='8100000001', fecha_nacimiento='1995-01-01', email='paciente@example.com',
        )
        self.otro_usuario = Usuario.objects.create_user(
            username='8100000002', password='ClaveSegura123', tipo_documento=tipo_documento,
            numero_documento='8100000002', fecha_nacimiento='1995-01-01', email='otro@example.com',
        )

    def datos_slot(self, **overrides):
        datos = {
            'fecha': self.fecha_futura.strftime('%Y-%m-%d'),
            'hora_inicio': '08:00',
            'motivo_consulta': 'Consulta de control',
        }
        datos.update(overrides)
        return datos

    def test_hu03_lista_especialidades_activas(self):
        self.client.login(username='8100000001', password='ClaveSegura123')
        response = self.client.get(reverse('agendar_especialidades'))
        self.assertContains(response, 'Pediatría')

    def test_hu04_lista_medicos_activos_de_la_especialidad(self):
        self.client.login(username='8100000001', password='ClaveSegura123')
        response = self.client.get(reverse('agendar_medicos', args=[self.especialidad.pk]))
        self.assertContains(response, self.medico.usuario.get_full_name())

    def test_hu05_muestra_slots_disponibles_agrupados_por_dia(self):
        self.client.login(username='8100000001', password='ClaveSegura123')
        response = self.client.get(reverse('agendar_slots', args=[self.medico.pk]))
        self.assertContains(response, '08:00')

    def test_hu06_agendar_exitosamente_crea_cita_confirmada_y_citalog(self):
        self.client.login(username='8100000001', password='ClaveSegura123')
        response = self.client.post(
            reverse('agendar_confirmar', args=[self.medico.pk]), self.datos_slot(),
        )
        self.assertRedirects(response, reverse('inicio'))

        cita = Cita.objects.get(medico=self.medico, fecha=self.fecha_futura, hora_inicio='08:00')
        self.assertEqual(cita.paciente, self.paciente)
        self.assertEqual(cita.estado.nombre, 'confirmada')
        self.assertEqual(cita.motivo_consulta, 'Consulta de control')

        log = CitaLog.objects.get(cita=cita)
        self.assertEqual(log.accion, 'creada')
        self.assertEqual(log.realizado_por, self.paciente)

    def test_hu06_segundo_intento_sobre_el_mismo_slot_falla_sin_duplicar(self):
        self.client.login(username='8100000001', password='ClaveSegura123')
        self.client.post(reverse('agendar_confirmar', args=[self.medico.pk]), self.datos_slot())

        self.client.login(username='8100000002', password='ClaveSegura123')
        response = self.client.post(
            reverse('agendar_confirmar', args=[self.medico.pk]), self.datos_slot(), follow=True,
        )
        self.assertContains(response, 'Ese horario ya no está disponible')
        self.assertEqual(
            Cita.objects.filter(medico=self.medico, fecha=self.fecha_futura, hora_inicio='08:00').count(), 1,
        )

    def test_hu06_paciente_no_puede_agendar_a_nombre_de_otro_usuario(self):
        self.client.login(username='8100000001', password='ClaveSegura123')
        datos = self.datos_slot(paciente=self.otro_usuario.pk)
        self.client.post(reverse('agendar_confirmar', args=[self.medico.pk]), datos)

        cita = Cita.objects.get(medico=self.medico, fecha=self.fecha_futura, hora_inicio='08:00')
        self.assertEqual(cita.paciente, self.paciente)

    def test_hu06_agendar_con_los_valores_reales_que_el_html_renderiza(self):
        """
        Regresión: la pantalla de confirmación renderiza hora_inicio como
        "08:00:00" (TimeField -> HiddenInput con segundos), no "08:00". Este
        test hace el viaje completo GET -> extraer values reales -> POST,
        en vez de asumir el formato "canónico" como los demás tests de esta
        clase (eso fue justo lo que dejó pasar el bug original).
        """
        self.client.login(username='8100000001', password='ClaveSegura123')

        respuesta_get = self.client.get(
            reverse('agendar_confirmar', args=[self.medico.pk]),
            {'fecha': self.fecha_futura.strftime('%Y-%m-%d'), 'hora_inicio': '08:00'},
        )
        datos = datos_formulario_reales(respuesta_get.content.decode())
        self.assertEqual(datos['hora_inicio'], '08:00:00')  # confirma que el HTML sí trae segundos
        datos['motivo_consulta'] = 'Control'

        respuesta_post = self.client.post(reverse('agendar_confirmar', args=[self.medico.pk]), datos)
        self.assertRedirects(respuesta_post, reverse('inicio'))

        cita = Cita.objects.get(medico=self.medico, fecha=self.fecha_futura, hora_inicio='08:00')
        self.assertEqual(cita.paciente, self.paciente)

    def test_hu06_agendar_con_hora_sin_segundos_tambien_funciona(self):
        self.client.login(username='8100000001', password='ClaveSegura123')
        response = self.client.post(
            reverse('agendar_confirmar', args=[self.medico.pk]),
            self.datos_slot(hora_inicio='10:00'),
        )
        self.assertRedirects(response, reverse('inicio'))
        self.assertTrue(
            Cita.objects.filter(medico=self.medico, fecha=self.fecha_futura, hora_inicio='10:00').exists(),
        )

    def test_usuario_anonimo_es_redirigido_a_login_en_todo_el_flujo(self):
        urls = [
            reverse('agendar_especialidades'),
            reverse('agendar_medicos', args=[self.especialidad.pk]),
            reverse('agendar_slots', args=[self.medico.pk]),
            reverse('agendar_confirmar', args=[self.medico.pk]),
        ]
        for url in urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, url)
            self.assertIn(reverse('login'), response.url, url)
