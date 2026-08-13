from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from datetime import time, timedelta
from unittest.mock import patch

from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from usuarios.htmlform_utils import datos_formulario_reales, opciones_reales_de_select
from usuarios.models import Medico, TipoDocumento, Usuario

from .models import Cita, CitaLog, EstadoCita, Especialidad, ExcepcionHorario, HorarioMedico
from .services import (
    VENTANA_AGENDA_MEDICO_DIAS,
    _particionar_bloque,
    cambiar_estado_cita,
    obtener_eventos_calendario,
    obtener_slots_disponibles,
    puede_cancelar_cita,
    puede_editar_comentario,
    puede_marcar_atendida,
)


def ejecutar_con_timeout(funcion, *args, segundos=3, **kwargs):
    """Corre `funcion` en un hilo aparte y falla con un mensaje claro si no
    termina en `segundos`, en vez de dejar el test (y potencialmente todo el
    runner) colgado indefinidamente. Multiplataforma: no depende de
    signal.alarm, que no existe en Windows.

    Cuidados necesarios para que el timeout sea real y no arrastre el
    problema a otro lado:
    - NO usar ThreadPoolExecutor como context manager: su __exit__ hace
      shutdown(wait=True), que se queda esperando al hilo colgado y anula
      el propósito del timeout. Se apaga explícito con wait=False.
    - El hilo nuevo comparte la MISMA conexión a la base que el hilo
      principal (en vez de dejar que Django abra una propia): así ve los
      datos de la transacción de TestCase sin necesitar TransactionTestCase
      (que hace flush() real entre tests — y en este archivo eso pisa el
      catálogo sembrado por las migraciones del que dependen otras pruebas,
      ver MigracionTiposDocumentoTests en usuarios/tests.py). Solo hay un
      hilo activo a la vez (el principal espera bloqueado en result()), así
      que compartir la conexión es seguro aquí. Django valida por defecto
      que una conexión no cruce hilos (validate_thread_sharing); hay que
      autorizarlo explícito con inc_thread_sharing().
    """
    from django.db import connections

    conexion_hilo_principal = connections['default']
    conexion_hilo_principal.inc_thread_sharing()

    def _tarea():
        connections['default'] = conexion_hilo_principal
        return funcion(*args, **kwargs)

    executor = ThreadPoolExecutor(max_workers=1)
    futuro = executor.submit(_tarea)
    try:
        resultado = futuro.result(timeout=segundos)
    except FuturesTimeoutError:
        # No decrementamos el contador de thread-sharing: el hilo puede
        # seguir vivo (colgado) y todavía usando la conexión.
        executor.shutdown(wait=False)
        nombre = getattr(funcion, '__qualname__', repr(funcion))
        raise AssertionError(
            f'{nombre} no terminó en {segundos}s (¿loop infinito? revisa '
            'citas/services.py: _particionar_bloque/sumar_minutos y el '
            'manejo de horarios que cruzan medianoche).',
        )
    else:
        executor.shutdown(wait=False)
        conexion_hilo_principal.dec_thread_sharing()
        return resultado


class EspecialidadPanelTests(TestCase):
    """HU-13: gestionar especialidades desde el panel del Administrador."""

    def setUp(self):
        tipo_documento = TipoDocumento.objects.get(nombre='Cédula de Ciudadanía (CC)')
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
        tipo_documento = TipoDocumento.objects.get(nombre='Cédula de Ciudadanía (CC)')
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
        tipo_documento = TipoDocumento.objects.get(nombre='Cédula de Ciudadanía (CC)')
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
        tipo_documento = TipoDocumento.objects.get(nombre='Cédula de Ciudadanía (CC)')
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
        tipo_documento = TipoDocumento.objects.get(nombre='Cédula de Ciudadanía (CC)')
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
    tipo_documento = TipoDocumento.objects.get(nombre='Cédula de Ciudadanía (CC)')
    usuario = Usuario.objects.create_user(
        username=numero_documento, password='ClaveSegura123', tipo_documento=tipo_documento,
        numero_documento=numero_documento, fecha_nacimiento='1995-01-01',
        email=f'{numero_documento}@example.com',
    )
    usuario.groups.add(Group.objects.get(name='Paciente'))
    return usuario


def crear_medico_de_prueba(numero_documento, especialidad):
    usuario = crear_paciente_de_prueba(numero_documento)
    usuario.groups.set([Group.objects.get(name='Medico')])
    return Medico.objects.create(usuario=usuario, especialidad=especialidad)


def crear_admin_de_prueba(numero_documento):
    usuario = crear_paciente_de_prueba(numero_documento)
    usuario.groups.set([Group.objects.get(name='Administrador')])
    return usuario


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

        tipo_documento = TipoDocumento.objects.get(nombre='Cédula de Ciudadanía (CC)')
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

    def test_las_cuatro_pantallas_del_flujo_tienen_enlace_de_regreso_a_mis_citas(self):
        self.client.login(username='8100000001', password='ClaveSegura123')
        enlace_mis_citas = reverse('mis_citas')

        respuesta_especialidades = self.client.get(reverse('agendar_especialidades'))
        self.assertContains(respuesta_especialidades, enlace_mis_citas)

        respuesta_medicos = self.client.get(reverse('agendar_medicos', args=[self.especialidad.pk]))
        self.assertContains(respuesta_medicos, enlace_mis_citas)

        respuesta_slots = self.client.get(reverse('agendar_slots', args=[self.medico.pk]))
        self.assertContains(respuesta_slots, enlace_mis_citas)

        respuesta_confirmar = self.client.get(
            reverse('agendar_confirmar', args=[self.medico.pk]),
            {'fecha': self.fecha_futura.strftime('%Y-%m-%d'), 'hora_inicio': '08:00'},
        )
        self.assertContains(respuesta_confirmar, enlace_mis_citas)

    def test_hu06_agendar_exitosamente_crea_cita_confirmada_y_citalog(self):
        self.client.login(username='8100000001', password='ClaveSegura123')
        response = self.client.post(
            reverse('agendar_confirmar', args=[self.medico.pk]), self.datos_slot(),
        )
        self.assertRedirects(response, reverse('mis_citas'))

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
        self.assertRedirects(respuesta_post, reverse('mis_citas'))

        cita = Cita.objects.get(medico=self.medico, fecha=self.fecha_futura, hora_inicio='08:00')
        self.assertEqual(cita.paciente, self.paciente)

    def test_hu06_agendar_con_hora_sin_segundos_tambien_funciona(self):
        self.client.login(username='8100000001', password='ClaveSegura123')
        response = self.client.post(
            reverse('agendar_confirmar', args=[self.medico.pk]),
            self.datos_slot(hora_inicio='10:00'),
        )
        self.assertRedirects(response, reverse('mis_citas'))
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


class ObtenerEventosCalendarioTests(TestCase):
    """Fase 5, tarea 1: obtener_eventos_calendario decide el alcance por
    rol, expone las tres capas (disponibilidad/bloqueo/ocupación) y las
    mantiene coherentes con el motor de disponibilidad existente."""

    def setUp(self):
        self.especialidad = Especialidad.objects.get(nombre='Pediatría')  # 30 min
        self.medico_uno = crear_medico_de_prueba('9100000001', self.especialidad)
        self.medico_dos = crear_medico_de_prueba('9100000002', self.especialidad)
        self.fecha = timezone.localdate() + timedelta(days=7)
        HorarioMedico.objects.create(
            medico=self.medico_uno, dia_semana=self.fecha.weekday(), hora_inicio='08:00', hora_fin='12:00',
        )
        HorarioMedico.objects.create(
            medico=self.medico_dos, dia_semana=self.fecha.weekday(), hora_inicio='08:00', hora_fin='12:00',
        )
        self.admin = crear_admin_de_prueba('9100000003')
        self.paciente = crear_paciente_de_prueba('9100000004')
        self.estado_confirmada = EstadoCita.objects.get(nombre='confirmada')

    def test_admin_ve_eventos_de_todos_los_medicos(self):
        eventos = obtener_eventos_calendario(self.admin, self.fecha, self.fecha)
        medicos_en_eventos = {evento.medico.pk for evento in eventos}
        self.assertIn(self.medico_uno.pk, medicos_en_eventos)
        self.assertIn(self.medico_dos.pk, medicos_en_eventos)

    def test_admin_puede_filtrar_por_medico(self):
        eventos = obtener_eventos_calendario(
            self.admin, self.fecha, self.fecha, filtros={'medico_id': self.medico_uno.pk},
        )
        self.assertTrue(eventos)
        self.assertTrue(all(evento.medico.pk == self.medico_uno.pk for evento in eventos))

    def test_medico_ve_solo_sus_propios_eventos(self):
        eventos = obtener_eventos_calendario(self.medico_uno.usuario, self.fecha, self.fecha)
        self.assertTrue(eventos)
        self.assertTrue(all(evento.medico.pk == self.medico_uno.pk for evento in eventos))

    def test_paciente_ve_solo_sus_propias_citas_sin_capas_de_disponibilidad(self):
        cita = Cita.objects.create(
            paciente=self.paciente, medico=self.medico_uno, estado=self.estado_confirmada,
            fecha=self.fecha, hora_inicio='08:00', hora_fin='08:30',
        )
        otro_paciente = crear_paciente_de_prueba('9100000005')
        Cita.objects.create(
            paciente=otro_paciente, medico=self.medico_dos, estado=self.estado_confirmada,
            fecha=self.fecha, hora_inicio='08:00', hora_fin='08:30',
        )

        eventos = obtener_eventos_calendario(self.paciente, self.fecha, self.fecha)

        self.assertEqual(len(eventos), 1)
        self.assertEqual(eventos[0].tipo, 'ocupacion')
        self.assertEqual(eventos[0].origen, cita)

    def test_las_tres_capas_aparecen_para_el_medico(self):
        Cita.objects.create(
            paciente=self.paciente, medico=self.medico_uno, estado=self.estado_confirmada,
            fecha=self.fecha, hora_inicio='08:00', hora_fin='08:30',
        )
        ExcepcionHorario.objects.create(
            medico=self.medico_uno, fecha=self.fecha, hora_inicio='10:00', hora_fin='10:30',
        )

        eventos = obtener_eventos_calendario(self.medico_uno.usuario, self.fecha, self.fecha)
        tipos = {evento.tipo for evento in eventos}

        self.assertEqual(tipos, {'disponibilidad', 'bloqueo', 'ocupacion'})

    def test_una_excepcion_anula_la_disponibilidad_que_pisa(self):
        ExcepcionHorario.objects.create(
            medico=self.medico_uno, fecha=self.fecha, hora_inicio='09:00', hora_fin='10:00',
        )

        eventos = obtener_eventos_calendario(self.medico_uno.usuario, self.fecha, self.fecha)
        disponibilidad = [evento for evento in eventos if evento.tipo == 'disponibilidad']
        bloqueos = [evento for evento in eventos if evento.tipo == 'bloqueo']

        self.assertTrue(bloqueos)
        self.assertFalse(any(time(9, 0) <= evento.inicio.time() < time(10, 0) for evento in disponibilidad))
        # El resto del bloque (08:00-09:00 y 10:00-12:00) sigue disponible.
        self.assertTrue(any(evento.inicio.time() == time(8, 0) for evento in disponibilidad))

    def test_cita_cancelada_deja_de_ser_ocupacion_y_libera_el_horario(self):
        cita = Cita.objects.create(
            paciente=self.paciente, medico=self.medico_uno, estado=self.estado_confirmada,
            fecha=self.fecha, hora_inicio='08:00', hora_fin='08:30',
        )

        eventos_antes = obtener_eventos_calendario(self.medico_uno.usuario, self.fecha, self.fecha)
        self.assertTrue(any(evento.tipo == 'ocupacion' and evento.origen == cita for evento in eventos_antes))
        self.assertFalse(any(
            evento.tipo == 'disponibilidad' and evento.inicio.time() == time(8, 0) for evento in eventos_antes
        ))

        cambiar_estado_cita(cita, 'cancelada', realizado_por=self.paciente)

        eventos_despues = obtener_eventos_calendario(self.medico_uno.usuario, self.fecha, self.fecha)
        self.assertFalse(any(evento.tipo == 'ocupacion' and evento.origen.pk == cita.pk for evento in eventos_despues))
        self.assertTrue(any(
            evento.tipo == 'disponibilidad' and evento.inicio.time() == time(8, 0) for evento in eventos_despues
        ))

    def test_desplazamiento_min_coincide_con_la_hora_de_inicio(self):
        Cita.objects.create(
            paciente=self.paciente, medico=self.medico_uno, estado=self.estado_confirmada,
            fecha=self.fecha, hora_inicio='08:30', hora_fin='09:00',
        )
        eventos = obtener_eventos_calendario(self.medico_uno.usuario, self.fecha, self.fecha)
        ocupacion = next(evento for evento in eventos if evento.tipo == 'ocupacion')
        self.assertEqual(ocupacion.desplazamiento_min, 8 * 60 + 30)


class AccionesPermitidasCitaTests(TestCase):
    """Decisiones de diseño 3, 4 y 5: qué puede hacer cada rol sobre una
    cita según su estado y si el horario ya pasó."""

    def setUp(self):
        especialidad = Especialidad.objects.get(nombre='Pediatría')
        self.medico = crear_medico_de_prueba('9200000001', especialidad)
        self.otro_medico = crear_medico_de_prueba('9200000002', especialidad)
        self.paciente = crear_paciente_de_prueba('9200000003')
        self.otro_paciente = crear_paciente_de_prueba('9200000004')
        self.admin = crear_admin_de_prueba('9200000005')
        self.estado_confirmada = EstadoCita.objects.get(nombre='confirmada')
        self.estado_atendida = EstadoCita.objects.get(nombre='atendida')
        self.estado_cancelada = EstadoCita.objects.get(nombre='cancelada')
        self.fecha_futura = timezone.localdate() + timedelta(days=7)

    def _cita(self, estado, fecha=None, hora_inicio='08:00', hora_fin='08:30'):
        cita = Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=estado,
            fecha=fecha or self.fecha_futura, hora_inicio=hora_inicio, hora_fin=hora_fin,
        )
        # Con strings 'HH:MM' en memoria (sin pasar por la DB), _ya_paso()
        # compararía str contra datetime.time; refrescamos para tener los
        # tipos reales, como en cualquier Cita obtenida por queryset.
        cita.refresh_from_db()
        return cita

    def test_paciente_dueno_puede_cancelar_cita_confirmada_futura(self):
        cita = self._cita(self.estado_confirmada)
        self.assertTrue(puede_cancelar_cita(cita, self.paciente))

    def test_paciente_no_puede_cancelar_cita_ajena(self):
        cita = self._cita(self.estado_confirmada)
        self.assertFalse(puede_cancelar_cita(cita, self.otro_paciente))

    def test_admin_puede_cancelar_cualquier_cita_confirmada_futura(self):
        cita = self._cita(self.estado_confirmada)
        self.assertTrue(puede_cancelar_cita(cita, self.admin))

    def test_nadie_puede_cancelar_cita_ya_cancelada(self):
        cita = self._cita(self.estado_cancelada)
        self.assertFalse(puede_cancelar_cita(cita, self.paciente))
        self.assertFalse(puede_cancelar_cita(cita, self.admin))

    def test_nadie_puede_cancelar_cita_atendida(self):
        cita = self._cita(self.estado_atendida)
        self.assertFalse(puede_cancelar_cita(cita, self.paciente))
        self.assertFalse(puede_cancelar_cita(cita, self.admin))

    def test_no_se_puede_cancelar_una_cita_confirmada_que_ya_empezo(self):
        ahora = timezone.localtime().replace(hour=10, minute=0, second=0, microsecond=0)
        cita = self._cita(self.estado_confirmada, fecha=ahora.date(), hora_inicio='08:00', hora_fin='08:30')
        with patch('citas.services.timezone.localtime', return_value=ahora):
            self.assertFalse(puede_cancelar_cita(cita, self.paciente))
            self.assertFalse(puede_cancelar_cita(cita, self.admin))

    def test_medico_dueno_puede_marcar_atendida_solo_tras_el_horario(self):
        ahora = timezone.localtime().replace(hour=10, minute=0, second=0, microsecond=0)
        cita_futura = self._cita(self.estado_confirmada, fecha=ahora.date(), hora_inicio='11:00', hora_fin='11:30')
        cita_pasada = self._cita(self.estado_confirmada, fecha=ahora.date(), hora_inicio='08:00', hora_fin='08:30')
        with patch('citas.services.timezone.localtime', return_value=ahora):
            self.assertFalse(puede_marcar_atendida(cita_futura, self.medico.usuario))
            self.assertTrue(puede_marcar_atendida(cita_pasada, self.medico.usuario))

    def test_solo_el_medico_dueno_puede_marcar_atendida(self):
        ahora = timezone.localtime().replace(hour=10, minute=0, second=0, microsecond=0)
        cita = self._cita(self.estado_confirmada, fecha=ahora.date(), hora_inicio='08:00', hora_fin='08:30')
        with patch('citas.services.timezone.localtime', return_value=ahora):
            self.assertFalse(puede_marcar_atendida(cita, self.otro_medico.usuario))
            self.assertFalse(puede_marcar_atendida(cita, self.admin))
            self.assertFalse(puede_marcar_atendida(cita, self.paciente))

    def test_no_se_puede_marcar_atendida_una_cita_no_confirmada(self):
        cita = self._cita(self.estado_atendida)
        self.assertFalse(puede_marcar_atendida(cita, self.medico.usuario))

    def test_solo_el_medico_dueno_puede_editar_el_comentario_y_solo_en_atendida(self):
        cita_atendida = self._cita(self.estado_atendida)
        cita_confirmada = self._cita(self.estado_confirmada, hora_inicio='09:00', hora_fin='09:30')

        self.assertTrue(puede_editar_comentario(cita_atendida, self.medico.usuario))
        self.assertFalse(puede_editar_comentario(cita_confirmada, self.medico.usuario))
        self.assertFalse(puede_editar_comentario(cita_atendida, self.otro_medico.usuario))
        self.assertFalse(puede_editar_comentario(cita_atendida, self.paciente))
        self.assertFalse(puede_editar_comentario(cita_atendida, self.admin))


class PacientePanelTests(TestCase):
    """HU-07, HU-08, HU-09: panel del paciente."""

    def setUp(self):
        especialidad = Especialidad.objects.get(nombre='Pediatría')
        self.medico = crear_medico_de_prueba('9300000001', especialidad)
        HorarioMedico.objects.create(
            medico=self.medico, dia_semana=(timezone.localdate() + timedelta(days=7)).weekday(),
            hora_inicio='08:00', hora_fin='12:00',
        )
        self.paciente = crear_paciente_de_prueba('9300000002')
        self.otro_paciente = crear_paciente_de_prueba('9300000003')
        self.estado_confirmada = EstadoCita.objects.get(nombre='confirmada')
        self.estado_cancelada = EstadoCita.objects.get(nombre='cancelada')
        self.estado_atendida = EstadoCita.objects.get(nombre='atendida')
        self.fecha_futura = timezone.localdate() + timedelta(days=7)
        self.client.login(username='9300000002', password='ClaveSegura123')

    def test_hu07_mis_citas_lista_solo_las_propias_y_proximas(self):
        propia = Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=self.estado_confirmada,
            fecha=self.fecha_futura, hora_inicio='08:00', hora_fin='08:30',
        )
        Cita.objects.create(
            paciente=self.otro_paciente, medico=self.medico, estado=self.estado_confirmada,
            fecha=self.fecha_futura, hora_inicio='09:00', hora_fin='09:30',
        )

        response = self.client.get(reverse('mis_citas'))

        self.assertContains(response, reverse('mis_citas_detalle', args=[propia.pk]))
        self.assertEqual(len(response.context['citas']), 1)

    def test_hu07_no_puede_abrir_el_detalle_de_una_cita_ajena(self):
        cita_ajena = Cita.objects.create(
            paciente=self.otro_paciente, medico=self.medico, estado=self.estado_confirmada,
            fecha=self.fecha_futura, hora_inicio='08:00', hora_fin='08:30',
        )
        response = self.client.get(reverse('mis_citas_detalle', args=[cita_ajena.pk]))
        self.assertEqual(response.status_code, 403)

    def test_hu08_cancelar_libera_el_horario_y_crea_citalog(self):
        cita = Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=self.estado_confirmada,
            fecha=self.fecha_futura, hora_inicio='08:00', hora_fin='08:30',
        )
        slots_antes = {slot.hora_inicio for slot in obtener_slots_disponibles(
            self.medico, fecha_desde=self.fecha_futura, fecha_hasta=self.fecha_futura,
        )}
        self.assertNotIn(time(8, 0), slots_antes)

        response = self.client.post(reverse('mis_citas_cancelar', args=[cita.pk]))
        self.assertRedirects(response, reverse('mis_citas'))

        cita.refresh_from_db()
        self.assertEqual(cita.estado.nombre, 'cancelada')
        self.assertFalse(cita.ocupa_slot)

        log = CitaLog.objects.get(cita=cita, accion='cancelada')
        self.assertEqual(log.realizado_por, self.paciente)

        slots_despues = {slot.hora_inicio for slot in obtener_slots_disponibles(
            self.medico, fecha_desde=self.fecha_futura, fecha_hasta=self.fecha_futura,
        )}
        self.assertIn(time(8, 0), slots_despues)

    def test_hu08_no_se_puede_cancelar_una_cita_pasada(self):
        ahora = timezone.localtime().replace(hour=10, minute=0, second=0, microsecond=0)
        cita = Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=self.estado_confirmada,
            fecha=ahora.date(), hora_inicio='08:00', hora_fin='08:30',
        )
        with patch('citas.services.timezone.localtime', return_value=ahora):
            response = self.client.post(reverse('mis_citas_cancelar', args=[cita.pk]))
        self.assertRedirects(response, reverse('mis_citas_detalle', args=[cita.pk]))

        cita.refresh_from_db()
        self.assertEqual(cita.estado.nombre, 'confirmada')

    def test_hu08_no_se_puede_cancelar_una_cita_ya_cancelada(self):
        cita = Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=self.estado_cancelada,
            fecha=self.fecha_futura, hora_inicio='08:00', hora_fin='08:30',
        )
        response = self.client.post(reverse('mis_citas_cancelar', args=[cita.pk]))
        self.assertRedirects(response, reverse('mis_citas_detalle', args=[cita.pk]))
        self.assertEqual(CitaLog.objects.filter(cita=cita, accion='cancelada').count(), 0)

    def test_hu08_no_se_puede_cancelar_una_cita_atendida(self):
        cita = Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=self.estado_atendida,
            fecha=self.fecha_futura, hora_inicio='08:00', hora_fin='08:30',
        )
        response = self.client.post(reverse('mis_citas_cancelar', args=[cita.pk]))
        self.assertRedirects(response, reverse('mis_citas_detalle', args=[cita.pk]))
        cita.refresh_from_db()
        self.assertEqual(cita.estado.nombre, 'atendida')

    def test_hu08_no_puede_cancelar_una_cita_ajena_manipulando_la_url(self):
        cita_ajena = Cita.objects.create(
            paciente=self.otro_paciente, medico=self.medico, estado=self.estado_confirmada,
            fecha=self.fecha_futura, hora_inicio='08:00', hora_fin='08:30',
        )
        response = self.client.post(reverse('mis_citas_cancelar', args=[cita_ajena.pk]))
        self.assertEqual(response.status_code, 403)

        cita_ajena.refresh_from_db()
        self.assertEqual(cita_ajena.estado.nombre, 'confirmada')

    def test_hu08_ida_y_vuelta_cancelacion(self):
        cita = Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=self.estado_confirmada,
            fecha=self.fecha_futura, hora_inicio='08:00', hora_fin='08:30',
        )
        respuesta_get = self.client.get(reverse('mis_citas_cancelar', args=[cita.pk]))
        datos = datos_formulario_reales(respuesta_get.content.decode())

        respuesta_post = self.client.post(reverse('mis_citas_cancelar', args=[cita.pk]), datos)
        self.assertRedirects(respuesta_post, reverse('mis_citas'))

        cita.refresh_from_db()
        self.assertEqual(cita.estado.nombre, 'cancelada')

    def test_hu09_historial_separa_pasadas_y_canceladas_de_proximas(self):
        ahora = timezone.localtime().replace(hour=10, minute=0, second=0, microsecond=0)
        proxima = Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=self.estado_confirmada,
            fecha=ahora.date() + timedelta(days=1), hora_inicio='08:00', hora_fin='08:30',
        )
        pasada_sin_marcar = Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=self.estado_confirmada,
            fecha=ahora.date(), hora_inicio='08:00', hora_fin='08:30',
        )
        cancelada = Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=self.estado_cancelada,
            fecha=ahora.date() + timedelta(days=2), hora_inicio='08:00', hora_fin='08:30',
        )
        atendida = Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=self.estado_atendida,
            fecha=ahora.date() - timedelta(days=1), hora_inicio='08:00', hora_fin='08:30',
        )

        with patch('citas.views.timezone.localtime', return_value=ahora):
            respuesta_proximas = self.client.get(reverse('mis_citas'))
            respuesta_historial = self.client.get(reverse('mis_citas_historial'))

        ids_proximas = {cita.pk for cita in respuesta_proximas.context['citas']}
        ids_historial = {cita.pk for cita in respuesta_historial.context['citas']}

        self.assertEqual(ids_proximas, {proxima.pk})
        self.assertEqual(ids_historial, {pasada_sin_marcar.pk, cancelada.pk, atendida.pk})

    def test_conserva_el_enlace_para_agendar_una_nueva_cita(self):
        response = self.client.get(reverse('mis_citas'))
        self.assertContains(response, reverse('agendar_especialidades'))


class MedicoPanelTests(TestCase):
    """Panel del médico: agenda, marcar atendida y comentario de consulta."""

    def setUp(self):
        especialidad = Especialidad.objects.get(nombre='Pediatría')
        self.medico = crear_medico_de_prueba('9400000001', especialidad)
        self.otro_medico = crear_medico_de_prueba('9400000002', especialidad)
        self.paciente = crear_paciente_de_prueba('9400000003')
        self.estado_confirmada = EstadoCita.objects.get(nombre='confirmada')
        self.estado_atendida = EstadoCita.objects.get(nombre='atendida')
        self.fecha_futura = timezone.localdate() + timedelta(days=3)
        self.client.login(username='9400000001', password='ClaveSegura123')

    def test_agenda_solo_muestra_citas_del_medico_propietario(self):
        propia = Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=self.estado_confirmada,
            fecha=self.fecha_futura, hora_inicio='08:00', hora_fin='08:30', motivo_consulta='Control',
        )
        ajena = Cita.objects.create(
            paciente=self.paciente, medico=self.otro_medico, estado=self.estado_confirmada,
            fecha=self.fecha_futura, hora_inicio='08:00', hora_fin='08:30',
        )

        response = self.client.get(reverse('medico_agenda'))

        self.assertContains(response, reverse('medico_cita_detalle', args=[propia.pk]))
        self.assertNotContains(response, reverse('medico_cita_detalle', args=[ajena.pk]))

    def test_marcar_atendida_solo_funciona_tras_el_horario_y_sobre_confirmada(self):
        ahora = timezone.localtime().replace(hour=10, minute=0, second=0, microsecond=0)
        cita_pasada = Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=self.estado_confirmada,
            fecha=ahora.date(), hora_inicio='08:00', hora_fin='08:30',
        )
        cita_futura = Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=self.estado_confirmada,
            fecha=ahora.date(), hora_inicio='11:00', hora_fin='11:30',
        )

        with patch('citas.views.timezone.localtime', return_value=ahora):
            self.client.post(reverse('medico_marcar_atendida', args=[cita_futura.pk]))
            self.client.post(reverse('medico_marcar_atendida', args=[cita_pasada.pk]))

        cita_futura.refresh_from_db()
        cita_pasada.refresh_from_db()
        self.assertEqual(cita_futura.estado.nombre, 'confirmada')
        self.assertEqual(cita_pasada.estado.nombre, 'atendida')

    def test_no_se_puede_marcar_atendida_una_cita_de_otro_medico(self):
        cita_ajena = Cita.objects.create(
            paciente=self.paciente, medico=self.otro_medico, estado=self.estado_confirmada,
            fecha=self.fecha_futura, hora_inicio='08:00', hora_fin='08:30',
        )
        response = self.client.post(reverse('medico_marcar_atendida', args=[cita_ajena.pk]))
        self.assertEqual(response.status_code, 404)

    def test_medico_dueno_escribe_y_edita_el_comentario_solo_en_atendida(self):
        cita = Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=self.estado_atendida,
            fecha=self.fecha_futura, hora_inicio='08:00', hora_fin='08:30',
        )
        response = self.client.post(
            reverse('medico_comentario', args=[cita.pk]), {'comentario_medico': 'Paciente estable.'},
        )
        self.assertRedirects(response, reverse('medico_cita_detalle', args=[cita.pk]))
        cita.refresh_from_db()
        self.assertEqual(cita.comentario_medico, 'Paciente estable.')

    def test_no_se_puede_editar_comentario_de_una_cita_no_atendida(self):
        cita = Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=self.estado_confirmada,
            fecha=self.fecha_futura, hora_inicio='08:00', hora_fin='08:30',
        )
        response = self.client.get(reverse('medico_comentario', args=[cita.pk]))
        self.assertEqual(response.status_code, 403)

    def test_otro_medico_no_puede_editar_comentario_de_cita_ajena(self):
        cita = Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=self.estado_atendida,
            fecha=self.fecha_futura, hora_inicio='08:00', hora_fin='08:30',
        )
        self.client.logout()
        self.client.login(username='9400000002', password='ClaveSegura123')

        response = self.client.post(
            reverse('medico_comentario', args=[cita.pk]), {'comentario_medico': 'Intento ajeno.'},
        )
        self.assertEqual(response.status_code, 404)
        cita.refresh_from_db()
        self.assertEqual(cita.comentario_medico, '')

    def test_paciente_no_puede_editar_el_comentario_aunque_envie_el_post(self):
        cita = Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=self.estado_atendida,
            fecha=self.fecha_futura, hora_inicio='08:00', hora_fin='08:30',
        )
        self.client.logout()
        self.client.login(username='9400000003', password='ClaveSegura123')

        response = self.client.post(
            reverse('medico_comentario', args=[cita.pk]), {'comentario_medico': 'Editado por el paciente.'},
        )
        self.assertEqual(response.status_code, 403)
        cita.refresh_from_db()
        self.assertEqual(cita.comentario_medico, '')

    def test_ida_y_vuelta_comentario_medico(self):
        cita = Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=self.estado_atendida,
            fecha=self.fecha_futura, hora_inicio='08:00', hora_fin='08:30',
        )
        respuesta_get = self.client.get(reverse('medico_comentario', args=[cita.pk]))
        datos = datos_formulario_reales(respuesta_get.content.decode())
        datos['comentario_medico'] = 'Se indica reposo y control en una semana.'

        respuesta_post = self.client.post(reverse('medico_comentario', args=[cita.pk]), datos)
        self.assertRedirects(respuesta_post, reverse('medico_cita_detalle', args=[cita.pk]))

        cita.refresh_from_db()
        self.assertEqual(cita.comentario_medico, 'Se indica reposo y control en una semana.')


class MedicoHorarioYExcepcionTests(TestCase):
    """El médico gestiona su propio horario y sus propias excepciones; nunca
    los de otro médico. El modelo ExcepcionHorario no tenía interfaz antes
    de esta fase."""

    def setUp(self):
        especialidad = Especialidad.objects.get(nombre='Pediatría')
        self.medico = crear_medico_de_prueba('9500000001', especialidad)
        self.otro_medico = crear_medico_de_prueba('9500000002', especialidad)
        self.fecha_futura = timezone.localdate() + timedelta(days=7)
        self.horario_ajeno = HorarioMedico.objects.create(
            medico=self.otro_medico, dia_semana=0, hora_inicio='08:00', hora_fin='12:00',
        )
        self.client.login(username='9500000001', password='ClaveSegura123')

    def test_medico_agrega_un_bloque_de_su_propio_horario(self):
        response = self.client.post(reverse('medico_mi_horario'), {
            'dia_semana': 0, 'hora_inicio': '08:00', 'hora_fin': '12:00',
        })
        self.assertRedirects(response, reverse('medico_mi_horario'))
        self.assertEqual(self.medico.horarios.count(), 1)

    def test_medico_no_puede_eliminar_el_horario_de_otro_medico(self):
        response = self.client.post(
            reverse('medico_mi_horario_eliminar', args=[self.horario_ajeno.pk]),
        )
        self.assertEqual(response.status_code, 404)
        self.assertTrue(HorarioMedico.objects.filter(pk=self.horario_ajeno.pk).exists())

    def test_ida_y_vuelta_agregar_horario(self):
        respuesta_get = self.client.get(reverse('medico_mi_horario'))
        html = respuesta_get.content.decode()
        datos = datos_formulario_reales(html)
        datos['dia_semana'] = opciones_reales_de_select(html, 'dia_semana')[0]
        datos.update({'hora_inicio': '14:00', 'hora_fin': '16:00'})

        respuesta_post = self.client.post(reverse('medico_mi_horario'), datos)
        self.assertRedirects(respuesta_post, reverse('medico_mi_horario'))
        self.assertEqual(self.medico.horarios.count(), 1)

    def test_excepcion_de_dia_completo_creada_por_el_medico_se_refleja_en_su_disponibilidad(self):
        HorarioMedico.objects.create(
            medico=self.medico, dia_semana=self.fecha_futura.weekday(), hora_inicio='08:00', hora_fin='12:00',
        )
        slots_antes = obtener_slots_disponibles(
            self.medico, fecha_desde=self.fecha_futura, fecha_hasta=self.fecha_futura,
        )
        self.assertTrue(slots_antes)

        response = self.client.post(reverse('medico_mis_excepciones'), {
            'fecha': self.fecha_futura.strftime('%Y-%m-%d'), 'hora_inicio': '', 'hora_fin': '', 'motivo': 'Vacaciones',
        })
        self.assertRedirects(response, reverse('medico_mis_excepciones'))

        slots_despues = obtener_slots_disponibles(
            self.medico, fecha_desde=self.fecha_futura, fecha_hasta=self.fecha_futura,
        )
        self.assertEqual(slots_despues, [])

    def test_medico_no_puede_eliminar_la_excepcion_de_otro_medico(self):
        excepcion_ajena = ExcepcionHorario.objects.create(medico=self.otro_medico, fecha=self.fecha_futura)
        response = self.client.post(
            reverse('medico_mi_excepcion_eliminar', args=[excepcion_ajena.pk]),
        )
        self.assertEqual(response.status_code, 404)
        self.assertTrue(ExcepcionHorario.objects.filter(pk=excepcion_ajena.pk).exists())

    def test_ida_y_vuelta_agregar_excepcion(self):
        respuesta_get = self.client.get(reverse('medico_mis_excepciones'))
        html = respuesta_get.content.decode()
        datos = datos_formulario_reales(html)
        datos.update({'fecha': self.fecha_futura.strftime('%Y-%m-%d'), 'motivo': 'Incapacidad'})

        respuesta_post = self.client.post(reverse('medico_mis_excepciones'), datos)
        self.assertRedirects(respuesta_post, reverse('medico_mis_excepciones'))
        self.assertEqual(self.medico.excepciones.count(), 1)


class PanelCitasAdminTests(TestCase):
    """Fase 5, tarea 5: cancelación de citas desde el panel del administrador."""

    def setUp(self):
        especialidad = Especialidad.objects.get(nombre='Pediatría')
        self.medico = crear_medico_de_prueba('9600000001', especialidad)
        self.paciente = crear_paciente_de_prueba('9600000002')
        self.admin = crear_admin_de_prueba('9600000003')
        self.estado_confirmada = EstadoCita.objects.get(nombre='confirmada')
        self.estado_cancelada = EstadoCita.objects.get(nombre='cancelada')
        self.fecha_futura = timezone.localdate() + timedelta(days=7)
        self.client.login(username='9600000003', password='ClaveSegura123')

    def test_admin_ve_las_citas_confirmadas_proximas(self):
        cita = Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=self.estado_confirmada,
            fecha=self.fecha_futura, hora_inicio='08:00', hora_fin='08:30',
        )
        response = self.client.get(reverse('panel_citas'))
        self.assertContains(response, self.paciente.get_full_name())
        self.assertContains(response, reverse('panel_citas_cancelar', args=[cita.pk]))

    def test_admin_cancela_una_cita_confirmada_futura_y_queda_auditada(self):
        cita = Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=self.estado_confirmada,
            fecha=self.fecha_futura, hora_inicio='08:00', hora_fin='08:30',
        )
        response = self.client.post(reverse('panel_citas_cancelar', args=[cita.pk]))
        self.assertRedirects(response, reverse('panel_citas'))

        cita.refresh_from_db()
        self.assertEqual(cita.estado.nombre, 'cancelada')
        log = CitaLog.objects.get(cita=cita, accion='cancelada')
        self.assertEqual(log.realizado_por, self.admin)

    def test_admin_no_puede_cancelar_una_cita_ya_cancelada(self):
        cita = Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=self.estado_cancelada,
            fecha=self.fecha_futura, hora_inicio='08:00', hora_fin='08:30',
        )
        self.client.post(reverse('panel_citas_cancelar', args=[cita.pk]))
        self.assertEqual(CitaLog.objects.filter(cita=cita, accion='cancelada').count(), 0)

    def test_no_admin_no_accede_al_panel_de_citas(self):
        self.client.logout()
        self.client.login(username='9600000002', password='ClaveSegura123')
        response = self.client.get(reverse('panel_citas'))
        self.assertEqual(response.status_code, 403)


class CalculoDeSlotsNoSeCuelgaTests(TestCase):
    """Regresión: un HorarioMedico cuyo bloque es múltiplo exacto de la
    duración de la especialidad (720min / 40min = 18) y ese múltiplo cae
    justo en hora_fin hacía que _particionar_bloque() entrara en un loop
    infinito. Causa: sumar_minutos() pierde el día al cruzar medianoche
    (devuelve solo .time()), así que fin_actual (envuelto a 00:28) nunca
    queda "> hora_fin" (23:48) y el `while True` no termina nunca."""

    def test_particionar_bloque_no_se_cuelga_con_bloque_multiplo_exacto(self):
        intervalos = ejecutar_con_timeout(
            _particionar_bloque, time(11, 48), time(23, 48), 40, segundos=3,
        )
        self.assertEqual(len(intervalos), 18)
        self.assertEqual(intervalos[0], (time(11, 48), time(12, 28)))
        self.assertEqual(intervalos[-1], (time(23, 8), time(23, 48)))


class AgendaMedicoNoSeCuelgaTests(TestCase):
    """Reproduce exactamente los datos reportados en producción:
    HorarioMedico dia_semana=1 (martes), hora_inicio=11:48, hora_fin=23:48;
    Especialidad duracion_cita_min=40; sin ExcepcionHorario ni Cita.

    TestCase normal (no TransactionTestCase): ejecutar_con_timeout() comparte
    la conexión del hilo principal con el hilo del timeout, así que no hace
    falta un commit real para que el hilo vea los datos de setUp.
    TransactionTestCase se evaluó y se descartó a propósito: hace flush()
    real entre tests, lo que le borra el catálogo sembrado por las
    migraciones (TipoDocumento/Group) a MigracionTiposDocumentoTests
    (usuarios/tests.py) cuando ambas clases corren en la misma suite —
    confirmado incluso con una TransactionTestCase vacía, sin datos propios.
    """

    def setUp(self):
        especialidad = Especialidad.objects.create(
            nombre='Especialidad de prueba (regresión cuelgue)', duracion_cita_min=40,
        )
        self.medico = crear_medico_de_prueba('9800000001', especialidad)
        HorarioMedico.objects.create(
            medico=self.medico, dia_semana=1, hora_inicio='11:48', hora_fin='23:48',
        )
        hoy = timezone.localdate()
        self.fecha_martes = hoy + timedelta(days=(1 - hoy.weekday()) % 7)
        if self.fecha_martes == hoy:
            self.fecha_martes += timedelta(days=7)

    def test_obtener_slots_disponibles_no_se_cuelga(self):
        slots = ejecutar_con_timeout(
            obtener_slots_disponibles, self.medico,
            fecha_desde=self.fecha_martes, fecha_hasta=self.fecha_martes, segundos=3,
        )
        self.assertEqual(len(slots), 18)

    def test_obtener_eventos_calendario_no_se_cuelga(self):
        eventos = ejecutar_con_timeout(
            obtener_eventos_calendario, self.medico.usuario, self.fecha_martes, self.fecha_martes, segundos=3,
        )
        self.assertEqual(len(eventos), 18)
        self.assertTrue(all(evento.tipo == 'disponibilidad' for evento in eventos))

    def test_vista_medico_agenda_no_se_cuelga(self):
        # VENTANA_AGENDA_MEDICO_DIAS (14) siempre incluye al menos un martes,
        # sin importar en qué día de la semana se corra el test.
        self.assertGreater(VENTANA_AGENDA_MEDICO_DIAS, 7)
        self.client.login(username='9800000001', password='ClaveSegura123')
        response = ejecutar_con_timeout(self.client.get, reverse('medico_agenda'), segundos=3)
        self.assertEqual(response.status_code, 200)
