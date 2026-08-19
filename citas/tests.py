import re
import unittest
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from io import StringIO
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.urls import reverse
from django.utils import timezone

from usuarios.htmlform_utils import datos_formulario_reales, opciones_reales_de_select
from usuarios.models import Medico, TipoDocumento, Usuario

from .calendario_geometria import (
    VENTANA_MIN_FIN,
    VENTANA_MIN_INICIO,
    MAX_COLUMNAS_VISIBLES,
    calcular_ventana,
    color_medico,
    empaquetar,
    generar_cajas_dia,
    limitar_columnas,
)
from .models import Cita, CitaLog, EstadoCita, Especialidad, ExcepcionHorario, HorarioMedico
from .services import (
    VENTANA_AGENDA_MEDICO_DIAS,
    _particionar_bloque,
    cambiar_estado_cita,
    cerrar_citas_vencidas,
    fusionar_slots_contiguos,
    obtener_densidad_citas_por_dia,
    obtener_eventos_calendario,
    obtener_slots_disponibles,
    puede_cancelar_cita,
    puede_editar_comentario,
    puede_marcar_atendida,
)
from .services import Slot


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

    def test_celda_de_duracion_muestra_el_numero_con_su_unidad(self):
        """Fase 6b.1, decisión 6: la unidad viaja con el dato, no con la
        cabecera ("Duración", no "Duración (min)")."""
        especialidad = Especialidad.objects.get(nombre='Pediatría')
        response = self.client.get(reverse('panel_especialidades'))
        self.assertContains(response, f'{especialidad.duracion_cita_min} min')
        self.assertNotContains(response, 'Duración (min)')

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

    def test_hu13_reactivar_especialidad_no_reactiva_a_sus_medicos(self):
        """Decisión 8 (fase 6a.1): la reactivación de cada médico es una
        decisión explícita del Administrador, no un efecto secundario de
        reactivar la especialidad."""
        tipo_documento = TipoDocumento.objects.get(nombre='Cédula de Ciudadanía (CC)')
        especialidad = Especialidad.objects.get(nombre='Pediatría')
        usuario_medico = Usuario.objects.create_user(
            username='6100000004', password='ClaveSegura123', tipo_documento=tipo_documento,
            numero_documento='6100000004', fecha_nacimiento='1980-01-01', email='ped4@example.com',
        )
        medico = Medico.objects.create(usuario=usuario_medico, especialidad=especialidad, activo=False)
        usuario_medico.is_active = False
        usuario_medico.save(update_fields=['is_active'])

        especialidad.activa = False
        especialidad.save(update_fields=['activa'])

        response = self.client.post(reverse('panel_especialidad_toggle', args=[especialidad.pk]))
        self.assertRedirects(response, reverse('panel_especialidades'))

        especialidad.refresh_from_db()
        medico.refresh_from_db()
        usuario_medico.refresh_from_db()
        self.assertTrue(especialidad.activa)
        self.assertFalse(medico.activo)
        self.assertFalse(usuario_medico.is_active)


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


class HorarioMedicoDjangoAdminTests(TestCase):
    """Fase 6b.2, decisión 2: el sitio de Django admin (citas/admin.py,
    HorarioMedicoAdmin) no usa HorarioMedicoForm — genera su propio
    ModelForm con todos los campos del modelo. Si la validación de
    solapamiento viviera solo en HorarioMedicoForm (la del panel de la app)
    en vez de en HorarioMedico.clean(), esta pantalla no la aplicaría."""

    def setUp(self):
        especialidad = Especialidad.objects.get(nombre='Medicina general')
        self.medico = crear_medico_de_prueba('9520000001', especialidad)
        HorarioMedico.objects.create(
            medico=self.medico, dia_semana=0, hora_inicio='08:00', hora_fin='12:00',
        )
        tipo_documento = TipoDocumento.objects.get(nombre='Cédula de Ciudadanía (CC)')
        Usuario.objects.create_superuser(
            username='9520000002', email='super9520000002@example.com', password='ClaveSegura123',
            tipo_documento=tipo_documento, numero_documento='9520000002', fecha_nacimiento='1980-01-01',
        )
        self.client.login(username='9520000002', password='ClaveSegura123')

    def test_admin_de_django_rechaza_un_bloque_solapado(self):
        response = self.client.post(reverse('admin:citas_horariomedico_add'), {
            'medico': self.medico.pk, 'dia_semana': 0, 'hora_inicio': '09:00', 'hora_fin': '11:00',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'se solapa')
        self.assertEqual(self.medico.horarios.count(), 1)


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

    def test_no_atendida_ocupa_slot(self):
        """Decisión 3 (fase 6a.1): a diferencia de cancelada, no atendida NO libera el turno."""
        cita = Cita.objects.create(
            paciente=self.paciente_uno, medico=self.medico, estado=self.estado_confirmada,
            fecha=self.fecha, hora_inicio='08:00', hora_fin='08:30',
        )
        cambiar_estado_cita(cita, 'no atendida', realizado_por=self.paciente_uno)
        cita.refresh_from_db()
        self.assertTrue(cita.ocupa_slot)

    def test_no_atendida_no_libera_el_slot_para_una_nueva_cita(self):
        primera = Cita.objects.create(
            paciente=self.paciente_uno, medico=self.medico, estado=self.estado_confirmada,
            fecha=self.fecha, hora_inicio='08:00', hora_fin='08:30',
        )
        cambiar_estado_cita(primera, 'no atendida', realizado_por=self.paciente_uno)

        with self.assertRaises(IntegrityError):
            Cita.objects.create(
                paciente=self.paciente_dos, medico=self.medico, estado=self.estado_confirmada,
                fecha=self.fecha, hora_inicio='08:00', hora_fin='08:30',
            )

    def test_cita_que_cruzaria_medianoche_viola_la_constraint_de_horas(self):
        """Tarea 2: comprueba que una Cita no puede cruzar medianoche, con o
        sin pasar por el generador de slots. hora_fin < hora_inicio como
        TimeField (ej. 00:20 < 23:50) siempre viola ck_cita_fin_mayor_inicio,
        así que esta situación no puede llegar a persistirse."""
        with self.assertRaises(IntegrityError):
            Cita.objects.create(
                paciente=self.paciente_uno, medico=self.medico, estado=self.estado_confirmada,
                fecha=self.fecha, hora_inicio='23:50', hora_fin='00:20',
            )


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

    def test_no_atendida_registra_origen_y_destino(self):
        """CitaLog guarda el estado de destino en la fila que crea (mismo
        patrón que cancelada/atendida: no hay un campo estado_origen
        separado); el origen es el estado que tenía la Cita antes de la
        llamada, verificable en la propia Cita."""
        estado_origen = self.cita.estado.nombre
        cambiar_estado_cita(self.cita, 'no atendida', realizado_por=None, detalle='Cierre automático.')

        self.assertEqual(estado_origen, 'confirmada')
        self.cita.refresh_from_db()
        self.assertEqual(self.cita.estado.nombre, 'no atendida')

        log = CitaLog.objects.get(cita=self.cita, accion='no atendida')
        self.assertEqual(log.estado.nombre, 'no atendida')
        self.assertIsNone(log.realizado_por)

    def test_realizado_por_nulo_no_se_vuelve_el_default_de_toda_transicion(self):
        """El null es exclusivo de las transiciones automáticas: una hecha
        por una persona real sigue registrando a esa persona, sin importar
        que el campo ahora admita nulos."""
        cambiar_estado_cita(self.cita, 'cancelada', realizado_por=self.paciente, detalle='Cancelada por el paciente.')

        log = CitaLog.objects.get(cita=self.cita, accion='cancelada')
        self.assertEqual(log.realizado_por, self.paciente)

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


class MisCitasNoAceptaParametroPacienteTests(TestCase):
    """Fase 6c, decisión 3: mis_citas y mis_citas_historial siguen sacando
    el paciente siempre de request.user. Este test protege la regla contra
    una refactorización futura que intentara aceptarlo como parámetro (lo
    que abriría la puerta a que un paciente vea las citas de otro) — ver
    citas_confirmadas_futuras/citas_historial en citas/services.py, que
    ahora también usa la ficha de solo lectura del administrador."""

    def setUp(self):
        especialidad = Especialidad.objects.get(nombre='Pediatría')
        self.medico = crear_medico_de_prueba('9910000001', especialidad)
        self.paciente = crear_paciente_de_prueba('9910000002')
        self.otro_paciente = crear_paciente_de_prueba('9910000003')
        fecha_futura = timezone.localdate() + timedelta(days=7)
        Cita.objects.create(
            paciente=self.otro_paciente, medico=self.medico, estado=EstadoCita.objects.get(nombre='confirmada'),
            fecha=fecha_futura, hora_inicio='08:00', hora_fin='08:30',
        )
        self.client.login(username='9910000002', password='ClaveSegura123')

    def test_mis_citas_ignora_un_paciente_inyectado_por_querystring(self):
        response = self.client.get(reverse('mis_citas'), {'paciente': self.otro_paciente.pk})
        self.assertEqual(len(response.context['citas']), 0)

    def test_mis_citas_historial_ignora_un_paciente_inyectado_por_querystring(self):
        response = self.client.get(reverse('mis_citas_historial'), {'paciente': self.otro_paciente.pk})
        self.assertEqual(len(response.context['citas']), 0)


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
    """Fase 6b.1, decisión 4: el médico gestiona todas las excepciones de su
    propia agenda (sin distinguir autoría), nunca las de otro médico. El
    horario recurrente ya NO es de escritura del médico — ver
    MedicoHorarioSoloLecturaTests."""

    def setUp(self):
        especialidad = Especialidad.objects.get(nombre='Pediatría')
        self.medico = crear_medico_de_prueba('9500000001', especialidad)
        self.otro_medico = crear_medico_de_prueba('9500000002', especialidad)
        self.fecha_futura = timezone.localdate() + timedelta(days=7)
        self.client.login(username='9500000001', password='ClaveSegura123')

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


class MedicoHorarioSoloLecturaTests(TestCase):
    """Fase 6b.1, decisiones 1-3: el horario recurrente (HorarioMedico) lo
    escribe solo el administrador (HU-14). El médico lo ve, nunca lo
    escribe — ni por interfaz ni por POST directo, con el chequeo en la
    vista, no en la plantilla."""

    def setUp(self):
        especialidad = Especialidad.objects.get(nombre='Pediatría')
        self.medico = crear_medico_de_prueba('9510000001', especialidad)
        self.otro_medico = crear_medico_de_prueba('9510000002', especialidad)
        self.admin = crear_admin_de_prueba('9510000003')
        self.horario_propio = HorarioMedico.objects.create(
            medico=self.medico, dia_semana=0, hora_inicio='08:00', hora_fin='12:00',
        )
        self.horario_ajeno = HorarioMedico.objects.create(
            medico=self.otro_medico, dia_semana=1, hora_inicio='08:00', hora_fin='12:00',
        )
        self.client.login(username='9510000001', password='ClaveSegura123')

    def test_medico_ve_su_horario_recurrente_en_solo_lectura(self):
        response = self.client.get(reverse('medico_mi_horario'))
        self.assertContains(response, 'Lunes')
        self.assertContains(response, '08:00')

    def test_tabla_ordena_por_dia_y_hora_inicio(self):
        """Fase 6b.2, tarea 3: mismo fixture desordenado que el equivalente
        del administrador (usuarios.tests.PanelMedicoHorarioTests)."""
        HorarioMedico.objects.create(medico=self.medico, dia_semana=3, hora_inicio='08:00', hora_fin='10:00')
        HorarioMedico.objects.create(medico=self.medico, dia_semana=0, hora_inicio='14:00', hora_fin='16:00')
        HorarioMedico.objects.create(medico=self.medico, dia_semana=4, hora_inicio='08:00', hora_fin='10:00')
        HorarioMedico.objects.create(medico=self.medico, dia_semana=1, hora_inicio='08:00', hora_fin='10:00')
        HorarioMedico.objects.create(medico=self.medico, dia_semana=2, hora_inicio='08:00', hora_fin='10:00')

        response = self.client.get(reverse('medico_mi_horario'))
        pares = [(h.dia_semana, h.hora_inicio) for h in response.context['horarios']]
        self.assertEqual(pares, sorted(pares))
        self.assertEqual(len(pares), 6)

    def test_la_pantalla_no_contiene_ningun_control_de_edicion(self):
        response = self.client.get(reverse('medico_mi_horario'))
        html = response.content.decode()
        contenido = html[html.index('<main'):html.index('</main>') + len('</main>')]
        self.assertNotIn('<form', contenido)
        self.assertNotIn('<select', contenido)
        self.assertNotIn('<button', contenido)

    def test_medico_no_puede_crear_un_bloque_via_post(self):
        response = self.client.post(reverse('medico_mi_horario'), {
            'dia_semana': 2, 'hora_inicio': '14:00', 'hora_fin': '16:00',
        })
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.medico.horarios.count(), 1)

    def test_medico_no_puede_eliminar_su_propio_bloque_via_post(self):
        response = self.client.post(reverse('medico_mi_horario_eliminar', args=[self.horario_propio.pk]))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(HorarioMedico.objects.filter(pk=self.horario_propio.pk).exists())

    def test_medico_no_puede_eliminar_el_bloque_de_otro_medico_via_post(self):
        response = self.client.post(reverse('medico_mi_horario_eliminar', args=[self.horario_ajeno.pk]))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(HorarioMedico.objects.filter(pk=self.horario_ajeno.pk).exists())

    def test_medico_no_accede_al_horario_de_otro_medico_manipulando_la_url_del_admin(self):
        response = self.client.get(reverse('panel_medico_horarios', args=[self.otro_medico.pk]))
        self.assertEqual(response.status_code, 403)

    def test_medico_no_puede_eliminar_via_url_del_admin(self):
        response = self.client.post(
            reverse('panel_medico_horario_eliminar', args=[self.medico.pk, self.horario_propio.pk]),
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(HorarioMedico.objects.filter(pk=self.horario_propio.pk).exists())

    def test_admin_sigue_pudiendo_crear_y_eliminar_horarios(self):
        self.client.logout()
        self.client.login(username='9510000003', password='ClaveSegura123')

        response = self.client.post(reverse('panel_medico_horarios', args=[self.medico.pk]), {
            'dia_semana': 2, 'hora_inicio': '08:00', 'hora_fin': '10:00',
        })
        self.assertRedirects(response, reverse('panel_medico_horarios', args=[self.medico.pk]))
        self.assertEqual(self.medico.horarios.count(), 2)

        nuevo = self.medico.horarios.get(dia_semana=2)
        response = self.client.post(
            reverse('panel_medico_horario_eliminar', args=[self.medico.pk, nuevo.pk]),
        )
        self.assertRedirects(response, reverse('panel_medico_horarios', args=[self.medico.pk]))
        self.assertFalse(HorarioMedico.objects.filter(pk=nuevo.pk).exists())


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


class PanelCitasListadoGlobalTests(TestCase):
    """Fase 6c, decisión 9: el listado global del administrador muestra
    citas en cualquier estado, con filtro opcional por estado."""

    def setUp(self):
        especialidad = Especialidad.objects.get(nombre='Pediatría')
        self.medico = crear_medico_de_prueba('9900000001', especialidad)
        self.paciente = crear_paciente_de_prueba('9900000002')
        self.admin = crear_admin_de_prueba('9900000003')
        fecha_futura = timezone.localdate() + timedelta(days=7)
        fecha_pasada = timezone.localdate() - timedelta(days=7)
        self.cita_confirmada = Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=EstadoCita.objects.get(nombre='confirmada'),
            fecha=fecha_futura, hora_inicio='08:00', hora_fin='08:30',
        )
        self.cita_atendida = Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=EstadoCita.objects.get(nombre='atendida'),
            fecha=fecha_pasada, hora_inicio='08:00', hora_fin='08:30',
        )
        self.cita_cancelada = Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=EstadoCita.objects.get(nombre='cancelada'),
            fecha=fecha_pasada, hora_inicio='09:00', hora_fin='09:30',
        )
        self.cita_no_atendida = Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=EstadoCita.objects.get(nombre='no atendida'),
            fecha=fecha_pasada, hora_inicio='10:00', hora_fin='10:30',
        )
        self.client.login(username='9900000003', password='ClaveSegura123')

    def test_sin_filtro_muestra_los_cuatro_estados(self):
        response = self.client.get(reverse('panel_citas'))
        pks = {cita.pk for cita in response.context['citas']}
        self.assertEqual(
            pks,
            {self.cita_confirmada.pk, self.cita_atendida.pk, self.cita_cancelada.pk, self.cita_no_atendida.pk},
        )

    def test_filtro_por_estado_muestra_solo_ese_estado(self):
        response = self.client.get(reverse('panel_citas'), {'estado': 'cancelada'})
        citas = response.context['citas']
        self.assertEqual(len(citas), 1)
        self.assertEqual(citas[0].pk, self.cita_cancelada.pk)

    def test_estado_invalido_en_querystring_se_ignora_como_si_no_hubiera_filtro(self):
        response = self.client.get(reverse('panel_citas'), {'estado': 'no-existe'})
        self.assertEqual(len(response.context['citas']), 4)

    def test_solo_las_citas_cancelables_muestran_el_boton_cancelar(self):
        response = self.client.get(reverse('panel_citas'))
        self.assertContains(response, reverse('panel_citas_cancelar', args=[self.cita_confirmada.pk]))
        self.assertNotContains(response, reverse('panel_citas_cancelar', args=[self.cita_atendida.pk]))
        self.assertNotContains(response, reverse('panel_citas_cancelar', args=[self.cita_cancelada.pk]))
        self.assertNotContains(response, reverse('panel_citas_cancelar', args=[self.cita_no_atendida.pk]))

    def test_ida_y_vuelta_formulario_de_filtro(self):
        response = self.client.get(reverse('panel_citas'))
        html = response.content.decode()
        datos = datos_formulario_reales(html)
        opciones = opciones_reales_de_select(html, 'estado')
        self.assertIn('cancelada', opciones)

        datos['estado'] = 'cancelada'
        response2 = self.client.get(reverse('panel_citas'), datos)
        self.assertEqual(len(response2.context['citas']), 1)
        self.assertEqual(response2.context['citas'][0].pk, self.cita_cancelada.pk)

    def test_filtro_se_repuebla_con_el_valor_enviado(self):
        response = self.client.get(reverse('panel_citas'), {'estado': 'atendida'})
        self.assertContains(response, 'value="atendida" selected')


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
        # 18 slots contiguos de 40 min (11:48-23:48) se funden en un único
        # evento de disponibilidad (fase 6a, tarea 3) — ya no 18.
        eventos = ejecutar_con_timeout(
            obtener_eventos_calendario, self.medico.usuario, self.fecha_martes, self.fecha_martes, segundos=3,
        )
        self.assertEqual(len(eventos), 1)
        self.assertTrue(all(evento.tipo == 'disponibilidad' for evento in eventos))
        self.assertEqual(eventos[0].desplazamiento_min, 11 * 60 + 48)

    def test_vista_medico_agenda_no_se_cuelga(self):
        # VENTANA_AGENDA_MEDICO_DIAS (14) siempre incluye al menos un martes,
        # sin importar en qué día de la semana se corra el test.
        self.assertGreater(VENTANA_AGENDA_MEDICO_DIAS, 7)
        self.client.login(username='9800000001', password='ClaveSegura123')
        response = ejecutar_con_timeout(self.client.get, reverse('medico_agenda'), segundos=3)
        self.assertEqual(response.status_code, 200)


# --- Fase 6a: fusión de slots (tarea 3) ---------------------------------------

class FusionarSlotsContiguosTests(unittest.TestCase):
    """Función pura, sin base de datos."""

    def test_fusion_cortada_por_una_cita_en_el_medio(self):
        fecha = date(2026, 1, 5)
        slots = [
            Slot(fecha, time(8, 0), time(8, 30)),
            Slot(fecha, time(8, 30), time(9, 0)),
            # 09:00-09:30 no aparece: lo ocupa una cita.
            Slot(fecha, time(9, 30), time(10, 0)),
        ]
        fusionados = fusionar_slots_contiguos(slots)
        self.assertEqual(len(fusionados), 2)
        self.assertEqual((fusionados[0].hora_inicio, fusionados[0].hora_fin), (time(8, 0), time(9, 0)))
        self.assertEqual((fusionados[1].hora_inicio, fusionados[1].hora_fin), (time(9, 30), time(10, 0)))

    def test_fusion_cortada_por_un_bloqueo_en_el_medio(self):
        fecha = date(2026, 1, 5)
        slots = [
            Slot(fecha, time(8, 0), time(8, 30)),
            # 08:30-10:00 no aparece: lo cubre una ExcepcionHorario.
            Slot(fecha, time(10, 0), time(10, 30)),
        ]
        fusionados = fusionar_slots_contiguos(slots)
        self.assertEqual(len(fusionados), 2)

    def test_jornada_entera_libre_un_solo_evento(self):
        fecha = date(2026, 1, 5)
        slots = [
            Slot(fecha, time(8, 0), time(8, 30)),
            Slot(fecha, time(8, 30), time(9, 0)),
            Slot(fecha, time(9, 0), time(9, 30)),
            Slot(fecha, time(9, 30), time(10, 0)),
        ]
        fusionados = fusionar_slots_contiguos(slots)
        self.assertEqual(len(fusionados), 1)
        self.assertEqual(fusionados[0].hora_inicio, time(8, 0))
        self.assertEqual(fusionados[0].hora_fin, time(10, 0))

    def test_dos_bloques_separados_por_hueco_no_se_fusionan(self):
        fecha = date(2026, 1, 5)
        slots = [
            Slot(fecha, time(8, 0), time(9, 0)),
            Slot(fecha, time(9, 0), time(10, 0)),
            Slot(fecha, time(14, 0), time(15, 0)),
            Slot(fecha, time(15, 0), time(16, 0)),
        ]
        fusionados = fusionar_slots_contiguos(slots)
        self.assertEqual(len(fusionados), 2)
        self.assertEqual((fusionados[0].hora_inicio, fusionados[0].hora_fin), (time(8, 0), time(10, 0)))
        self.assertEqual((fusionados[1].hora_inicio, fusionados[1].hora_fin), (time(14, 0), time(16, 0)))

    def test_dos_bloques_adyacentes_si_se_fusionan(self):
        fecha = date(2026, 1, 5)
        slots = [
            Slot(fecha, time(8, 0), time(12, 0)),
            Slot(fecha, time(12, 0), time(14, 0)),
        ]
        fusionados = fusionar_slots_contiguos(slots)
        self.assertEqual(len(fusionados), 1)
        self.assertEqual((fusionados[0].hora_inicio, fusionados[0].hora_fin), (time(8, 0), time(14, 0)))

    def test_regresion_rollover_no_funde_a_traves_de_medianoche_ni_cuelga(self):
        # Mismo minuto de reloj (21:00) en dos fechas distintas: si la
        # fusión comparara solo hora-de-reloj sin agrupar por fecha primero,
        # esto se fundiría por error a través de la medianoche.
        slots = [
            Slot(date(2026, 1, 5), time(20, 0), time(21, 0)),
            Slot(date(2026, 1, 6), time(21, 0), time(22, 0)),
        ]
        fusionados = ejecutar_con_timeout(fusionar_slots_contiguos, slots, segundos=3)
        self.assertEqual(len(fusionados), 2)


# --- Fase 6a: geometría del calendario (tarea 6) ------------------------------

@dataclass
class _MedicoFake:
    pk: int


@dataclass
class _EventoFake:
    tipo: str
    titulo: str
    desplazamiento_min: int
    duracion_min: int
    dia_completo: bool = False
    medico: object = None
    estado: str = None

    def __post_init__(self):
        base = datetime(2026, 1, 5, 0, 0, tzinfo=dt_timezone.utc)
        self.inicio = base + timedelta(minutes=self.desplazamiento_min)
        self.fin = self.inicio + timedelta(minutes=self.duracion_min)


class CalendarioGeometriaTests(unittest.TestCase):
    """Funciones puras de citas/calendario_geometria.py: sin base de datos."""

    def test_empaquetar_dos_eventos_solapados_en_dos_columnas(self):
        m = _MedicoFake(1)
        a = _EventoFake('bloqueo', 'A', 60, 60, medico=m)
        b = _EventoFake('bloqueo', 'B', 90, 60, medico=m)
        colocados = empaquetar([a, b])
        self.assertEqual({c.cols for c in colocados}, {2})
        self.assertEqual({c.col for c in colocados}, {0, 1})

    def test_cluster_supera_max_columnas_aparece_resumen_con_conteo_correcto(self):
        eventos = [_EventoFake('bloqueo', f'm{i}', 60, 60, medico=_MedicoFake(i)) for i in range(5)]
        limitados = limitar_columnas(empaquetar(eventos))
        resumenes = [c for c in limitados if hasattr(c, 'ocultos')]
        self.assertEqual(len(resumenes), 1)
        self.assertEqual(len(resumenes[0].ocultos), 5 - (MAX_COLUMNAS_VISIBLES - 1))

    def test_etiqueta_resumen_homogenea_dice_bloqueos(self):
        eventos = [_EventoFake('bloqueo', f'b{i}', 60, 60, medico=_MedicoFake(i)) for i in range(5)]
        ventana = calcular_ventana(eventos)
        cajas = generar_cajas_dia(eventos, ventana)
        resumen = next(c for c in cajas if c.es_resumen)
        self.assertIn('bloqueos', resumen.titulo)

    def test_etiqueta_resumen_mixta_dice_eventos(self):
        # Orden importa: el empaquetado desempata por orden de entrada entre
        # eventos idénticos en el tiempo, así que el bloqueo se intercala
        # después de los dos primeros (que quedan visibles) para que el
        # grupo OCULTO termine mezclando bloqueo + citas.
        eventos = [
            _EventoFake('ocupacion', 'c0', 60, 60, medico=_MedicoFake(0), estado='confirmada'),
            _EventoFake('ocupacion', 'c1', 60, 60, medico=_MedicoFake(1), estado='confirmada'),
            _EventoFake('bloqueo', 'b', 60, 60, medico=_MedicoFake(2)),
            _EventoFake('ocupacion', 'c2', 60, 60, medico=_MedicoFake(3), estado='confirmada'),
            _EventoFake('ocupacion', 'c3', 60, 60, medico=_MedicoFake(4), estado='confirmada'),
        ]
        ventana = calcular_ventana(eventos)
        cajas = generar_cajas_dia(eventos, ventana)
        resumen = next(c for c in cajas if c.es_resumen)
        ocultos_tipos = {o.tipo for o in resumen.ocultos}
        self.assertEqual(ocultos_tipos, {'bloqueo', 'ocupacion'})
        self.assertIn('eventos', resumen.titulo)

    def test_ventana_ignora_dia_completo_y_respeta_piso(self):
        m = _MedicoFake(1)
        eventos = [
            _EventoFake('bloqueo', 'normal', 8 * 60, 60, medico=m),
            _EventoFake('bloqueo', 'completo', 0, 23 * 60 + 59, dia_completo=True, medico=m),
        ]
        ventana = calcular_ventana(eventos)
        self.assertEqual(ventana.inicio_min, VENTANA_MIN_INICIO)
        self.assertEqual(ventana.fin_min, VENTANA_MIN_FIN)

    def test_evento_antes_de_la_ventana_se_recorta_sin_quedar_negativo(self):
        m = _MedicoFake(1)
        completo = _EventoFake('bloqueo', 'completo', 0, 23 * 60 + 59, dia_completo=True, medico=m)
        normal = _EventoFake('bloqueo', 'normal', 9 * 60, 60, medico=m)
        ventana = calcular_ventana([completo, normal])
        cajas = generar_cajas_dia([completo, normal], ventana)
        caja_completo = next(c for c in cajas if c.titulo == 'completo')
        top_px = float(re.search(r'top:(-?[\d.]+)px', caja_completo.estilo).group(1))
        self.assertGreaterEqual(top_px, 0)

    def test_color_medico_es_determinista_y_ciclico(self):
        self.assertEqual(color_medico(3), color_medico(3))
        self.assertEqual(color_medico(0), color_medico(8))


# --- Fase 6a: contrato de EventoCalendario (tareas 1 y 2) ---------------------

class EventoCalendarioContratoTests(TestCase):
    def setUp(self):
        self.especialidad = Especialidad.objects.create(nombre='Cardiología contrato', duracion_cita_min=60)
        self.medico = crear_medico_de_prueba('9700000001', self.especialidad)
        self.admin = crear_admin_de_prueba('9700000002')
        self.paciente = crear_paciente_de_prueba('9700000003')
        self.estado_confirmada = EstadoCita.objects.get(nombre='confirmada')
        self.estado_atendida = EstadoCita.objects.get(nombre='atendida')
        self.fecha = timezone.localdate() + timedelta(days=7)

    def test_dia_completo_true_para_bloqueo_de_dia_completo(self):
        ExcepcionHorario.objects.create(medico=self.medico, fecha=self.fecha)
        eventos = obtener_eventos_calendario(self.medico.usuario, self.fecha, self.fecha)
        bloqueo = next(e for e in eventos if e.tipo == 'bloqueo')
        self.assertTrue(bloqueo.dia_completo)

    def test_dia_completo_false_para_bloqueo_puntual(self):
        ExcepcionHorario.objects.create(
            medico=self.medico, fecha=self.fecha, hora_inicio='09:00', hora_fin='10:00',
        )
        eventos = obtener_eventos_calendario(self.medico.usuario, self.fecha, self.fecha)
        bloqueo = next(e for e in eventos if e.tipo == 'bloqueo')
        self.assertFalse(bloqueo.dia_completo)

    def test_comentario_llega_en_evento_de_cita_atendida(self):
        Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=self.estado_atendida,
            fecha=self.fecha, hora_inicio='08:00', hora_fin='09:00',
            comentario_medico='Todo bien, control en un mes.',
        )
        eventos = obtener_eventos_calendario(self.medico.usuario, self.fecha, self.fecha)
        ocupacion = next(e for e in eventos if e.tipo == 'ocupacion')
        self.assertEqual(ocupacion.comentario, 'Todo bien, control en un mes.')

    def test_comentario_es_none_sin_comentario_medico(self):
        Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=self.estado_confirmada,
            fecha=self.fecha, hora_inicio='08:00', hora_fin='09:00',
        )
        eventos = obtener_eventos_calendario(self.medico.usuario, self.fecha, self.fecha)
        ocupacion = next(e for e in eventos if e.tipo == 'ocupacion')
        self.assertIsNone(ocupacion.comentario)

    def test_titulo_para_administrador_no_contiene_motivo_consulta(self):
        Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=self.estado_confirmada,
            fecha=self.fecha, hora_inicio='08:00', hora_fin='09:00',
            motivo_consulta='Dolor de cabeza persistente',
        )
        eventos = obtener_eventos_calendario(
            self.admin, self.fecha, self.fecha, filtros={'medico_id': self.medico.pk},
        )
        ocupacion = next(e for e in eventos if e.tipo == 'ocupacion')
        self.assertNotIn('Dolor de cabeza persistente', ocupacion.titulo)
        self.assertIn(self.paciente.get_full_name(), ocupacion.titulo)

    def test_titulo_para_medico_dueno_si_contiene_motivo_consulta(self):
        Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=self.estado_confirmada,
            fecha=self.fecha, hora_inicio='08:00', hora_fin='09:00',
            motivo_consulta='Dolor de cabeza persistente',
        )
        eventos = obtener_eventos_calendario(self.medico.usuario, self.fecha, self.fecha)
        ocupacion = next(e for e in eventos if e.tipo == 'ocupacion')
        self.assertEqual(ocupacion.titulo, 'Dolor de cabeza persistente')

    def test_contrato_completo_de_eventocalendario(self):
        cita = Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=self.estado_confirmada,
            fecha=self.fecha, hora_inicio='08:00', hora_fin='09:00', motivo_consulta='motivo',
        )
        eventos = obtener_eventos_calendario(self.medico.usuario, self.fecha, self.fecha)
        ocupacion = next(e for e in eventos if e.tipo == 'ocupacion')
        for campo in (
            'tipo', 'id', 'titulo', 'inicio', 'fin', 'estado', 'medico', 'especialidad',
            'acciones_permitidas', 'origen', 'desplazamiento_min', 'dia_completo', 'comentario',
        ):
            self.assertTrue(hasattr(ocupacion, campo), f'falta el campo {campo}')
        self.assertEqual(ocupacion.origen, cita)
        self.assertFalse(ocupacion.dia_completo)


# --- Fase 6a: capas y densidad (tareas 4 y 5) ---------------------------------

class CapasYDensidadTests(TestCase):
    def setUp(self):
        self.especialidad = Especialidad.objects.create(nombre='Especialidad capas', duracion_cita_min=30)
        self.medico_uno = crear_medico_de_prueba('9750000001', self.especialidad)
        self.medico_dos = crear_medico_de_prueba('9750000002', self.especialidad)
        self.admin = crear_admin_de_prueba('9750000003')
        self.paciente = crear_paciente_de_prueba('9750000004')
        self.fecha = timezone.localdate() + timedelta(days=7)
        HorarioMedico.objects.create(
            medico=self.medico_uno, dia_semana=self.fecha.weekday(), hora_inicio='08:00', hora_fin='12:00',
        )
        self.estado_confirmada = EstadoCita.objects.get(nombre='confirmada')
        self.estado_cancelada = EstadoCita.objects.get(nombre='cancelada')
        self.estado_atendida = EstadoCita.objects.get(nombre='atendida')

    def test_panoramico_no_incluye_ocupacion_ni_disponibilidad(self):
        Cita.objects.create(
            paciente=self.paciente, medico=self.medico_uno, estado=self.estado_confirmada,
            fecha=self.fecha, hora_inicio='08:00', hora_fin='08:30',
        )
        ExcepcionHorario.objects.create(
            medico=self.medico_uno, fecha=self.fecha, hora_inicio='09:00', hora_fin='10:00',
        )
        eventos = obtener_eventos_calendario(self.admin, self.fecha, self.fecha, filtros={'capas': {'bloqueo'}})
        self.assertEqual({e.tipo for e in eventos}, {'bloqueo'})

    def test_medico_concreto_tiene_las_tres_capas(self):
        Cita.objects.create(
            paciente=self.paciente, medico=self.medico_uno, estado=self.estado_confirmada,
            fecha=self.fecha, hora_inicio='08:00', hora_fin='08:30',
        )
        ExcepcionHorario.objects.create(
            medico=self.medico_uno, fecha=self.fecha, hora_inicio='09:00', hora_fin='10:00',
        )
        eventos = obtener_eventos_calendario(
            self.admin, self.fecha, self.fecha, filtros={'medico_id': self.medico_uno.pk, 'capas': None},
        )
        self.assertEqual({e.tipo for e in eventos}, {'disponibilidad', 'bloqueo', 'ocupacion'})

    def test_capas_no_amplia_alcance_del_rol(self):
        Cita.objects.create(
            paciente=self.paciente, medico=self.medico_uno, estado=self.estado_confirmada,
            fecha=self.fecha, hora_inicio='08:00', hora_fin='08:30',
        )
        eventos = obtener_eventos_calendario(
            self.paciente, self.fecha, self.fecha,
            filtros={'capas': {'disponibilidad', 'bloqueo', 'ocupacion'}},
        )
        self.assertEqual({e.tipo for e in eventos}, {'ocupacion'})

    def test_contador_no_suma_cancelada_si_suma_atendida(self):
        Cita.objects.create(
            paciente=self.paciente, medico=self.medico_uno, estado=self.estado_cancelada,
            fecha=self.fecha, hora_inicio='08:00', hora_fin='08:30',
        )
        Cita.objects.create(
            paciente=self.paciente, medico=self.medico_uno, estado=self.estado_atendida,
            fecha=self.fecha, hora_inicio='09:00', hora_fin='09:30',
        )
        densidad = obtener_densidad_citas_por_dia(self.admin, self.fecha, self.fecha)
        self.assertEqual(densidad.get(self.fecha, 0), 1)

    def test_contador_respeta_filtro_por_medico(self):
        Cita.objects.create(
            paciente=self.paciente, medico=self.medico_uno, estado=self.estado_confirmada,
            fecha=self.fecha, hora_inicio='08:00', hora_fin='08:30',
        )
        Cita.objects.create(
            paciente=self.paciente, medico=self.medico_dos, estado=self.estado_confirmada,
            fecha=self.fecha, hora_inicio='08:00', hora_fin='08:30',
        )
        densidad = obtener_densidad_citas_por_dia(
            self.admin, self.fecha, self.fecha, filtros={'medico_id': self.medico_uno.pk},
        )
        self.assertEqual(densidad.get(self.fecha, 0), 1)

    def test_contador_respeta_filtro_por_especialidad(self):
        otra_especialidad = Especialidad.objects.create(nombre='Otra especialidad capas', duracion_cita_min=30)
        otro_medico = crear_medico_de_prueba('9750000005', otra_especialidad)
        Cita.objects.create(
            paciente=self.paciente, medico=self.medico_uno, estado=self.estado_confirmada,
            fecha=self.fecha, hora_inicio='08:00', hora_fin='08:30',
        )
        Cita.objects.create(
            paciente=self.paciente, medico=otro_medico, estado=self.estado_confirmada,
            fecha=self.fecha, hora_inicio='08:00', hora_fin='08:30',
        )
        densidad = obtener_densidad_citas_por_dia(
            self.admin, self.fecha, self.fecha, filtros={'especialidad_id': self.especialidad.pk},
        )
        self.assertEqual(densidad.get(self.fecha, 0), 1)

    def test_contador_se_resuelve_en_una_sola_consulta(self):
        # Además del agregado, _alcance_por_rol llama a es_administrador(),
        # que en sí misma es una consulta (comprueba pertenencia al grupo) —
        # el mismo costo que ya paga obtener_eventos_calendario. Lo que
        # importa de "una sola consulta" es que el agregado no se repite por
        # día ni por médico: un rango de 7 días cuesta lo mismo que uno de 1.
        Cita.objects.create(
            paciente=self.paciente, medico=self.medico_uno, estado=self.estado_confirmada,
            fecha=self.fecha, hora_inicio='08:00', hora_fin='08:30',
        )
        with CaptureQueriesContext(connection) as un_dia:
            obtener_densidad_citas_por_dia(self.admin, self.fecha, self.fecha)
        with CaptureQueriesContext(connection) as siete_dias:
            obtener_densidad_citas_por_dia(self.admin, self.fecha, self.fecha + timedelta(days=6))
        self.assertEqual(len(un_dia.captured_queries), 2)
        self.assertEqual(len(un_dia.captured_queries), len(siete_dias.captured_queries))


# --- Fase 6a: vista del calendario del administrador (tareas 8-13) -----------

class PanelCalendarioSeguridadTests(TestCase):
    def setUp(self):
        especialidad = Especialidad.objects.create(nombre='Especialidad seguridad calendario', duracion_cita_min=30)
        self.medico = crear_medico_de_prueba('9760000001', especialidad)
        self.paciente = crear_paciente_de_prueba('9760000002')
        self.admin = crear_admin_de_prueba('9760000003')
        self.fecha = timezone.localdate() + timedelta(days=7)

    def test_paciente_no_accede(self):
        self.client.login(username='9760000002', password='ClaveSegura123')
        response = self.client.get(reverse('panel_calendario'))
        self.assertEqual(response.status_code, 403)

    def test_medico_no_accede(self):
        self.client.login(username='9760000001', password='ClaveSegura123')
        response = self.client.get(reverse('panel_calendario'))
        self.assertEqual(response.status_code, 403)

    def test_anonimo_no_accede(self):
        response = self.client.get(reverse('panel_calendario'))
        self.assertIn(response.status_code, (302, 403))

    def test_post_no_crea_ni_modifica_nada(self):
        self.client.login(username='9760000003', password='ClaveSegura123')
        citas_antes = Cita.objects.count()
        excepciones_antes = ExcepcionHorario.objects.count()
        self.client.post(reverse('panel_calendario'), {'especialidad': '', 'medico': ''})
        self.assertEqual(Cita.objects.count(), citas_antes)
        self.assertEqual(ExcepcionHorario.objects.count(), excepciones_antes)

    def test_titulo_sin_motivo_consulta_para_admin_y_con_motivo_para_medico_dueno(self):
        Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=EstadoCita.objects.get(nombre='confirmada'),
            fecha=self.fecha, hora_inicio='08:00', hora_fin='08:30',
            motivo_consulta='Motivo secreto de la consulta',
        )
        self.client.login(username='9760000003', password='ClaveSegura123')
        response = self.client.get(
            reverse('panel_calendario'), {'medico': self.medico.pk, 'semana': self.fecha.isoformat()},
        )
        self.assertNotContains(response, 'Motivo secreto de la consulta')

        eventos_medico = obtener_eventos_calendario(self.medico.usuario, self.fecha, self.fecha)
        ocupacion = next(e for e in eventos_medico if e.tipo == 'ocupacion')
        self.assertIn('Motivo secreto de la consulta', ocupacion.titulo)

    def test_capas_en_querystring_no_amplia_alcance(self):
        Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=EstadoCita.objects.get(nombre='confirmada'),
            fecha=self.fecha, hora_inicio='08:00', hora_fin='08:30',
        )
        eventos = obtener_eventos_calendario(
            self.paciente, self.fecha, self.fecha,
            filtros={'capas': {'disponibilidad', 'bloqueo', 'ocupacion'}, 'medico_id': self.medico.pk},
        )
        self.assertTrue(all(e.tipo == 'ocupacion' for e in eventos))


class PanelCalendarioFiltrosTests(TestCase):
    def setUp(self):
        self.especialidad_uno = Especialidad.objects.create(nombre='Cardiología filtros', duracion_cita_min=60)
        self.especialidad_dos = Especialidad.objects.create(nombre='Dermatología filtros', duracion_cita_min=20)
        self.medico_uno = crear_medico_de_prueba('9770000001', self.especialidad_uno)
        self.medico_dos = crear_medico_de_prueba('9770000002', self.especialidad_dos)
        self.admin = crear_admin_de_prueba('9770000003')
        self.client.login(username='9770000003', password='ClaveSegura123')

    def test_ida_y_vuelta_formulario_de_filtros(self):
        response = self.client.get(reverse('panel_calendario'))
        html = response.content.decode()
        datos = datos_formulario_reales(html)
        medicos_reales = opciones_reales_de_select(html, 'medico')
        self.assertIn(str(self.medico_uno.pk), medicos_reales)

        datos['especialidad'] = str(self.especialidad_uno.pk)
        datos['medico'] = str(self.medico_uno.pk)
        response2 = self.client.get(reverse('panel_calendario'), datos)
        self.assertEqual(response2.status_code, 200)
        self.assertContains(response2, self.medico_uno.usuario.get_full_name())

    def test_options_de_medico_llevan_data_especialidad_y_data_cedula(self):
        response = self.client.get(reverse('panel_calendario'))
        html = response.content.decode()
        self.assertIn(f'data-especialidad="{self.especialidad_uno.pk}"', html)
        self.assertIn(f'data-cedula="{self.medico_uno.usuario.username}"', html)

    def test_filtros_se_repueblan_con_los_valores_enviados(self):
        response = self.client.get(reverse('panel_calendario'), {'especialidad': self.especialidad_uno.pk})
        self.assertContains(response, f'value="{self.especialidad_uno.pk}" selected')

    def test_querystring_contradictorio_redirige_a_combinacion_consistente(self):
        response = self.client.get(reverse('panel_calendario'), {
            'especialidad': self.especialidad_dos.pk, 'medico': self.medico_uno.pk,
        })
        self.assertEqual(response.status_code, 302)
        self.assertIn(f'especialidad={self.especialidad_uno.pk}', response.url)
        self.assertIn(f'medico={self.medico_uno.pk}', response.url)
        respuesta_final = self.client.get(response.url)
        self.assertEqual(respuesta_final.status_code, 200)

    def test_navegacion_conserva_filtros(self):
        response = self.client.get(reverse('panel_calendario'), {'especialidad': self.especialidad_uno.pk})
        self.assertContains(response, f'especialidad={self.especialidad_uno.pk}')

    def test_semana_empieza_en_lunes_incluido_salto_de_mes(self):
        response = self.client.get(reverse('panel_calendario'), {'semana': '2026-04-29'})
        self.assertEqual(response.context['lunes'], date(2026, 4, 27))
        self.assertEqual(response.context['domingo'], date(2026, 5, 3))

    def test_semana_empieza_en_lunes_incluido_salto_de_ano(self):
        response = self.client.get(reverse('panel_calendario'), {'semana': '2026-12-31'})
        self.assertEqual(response.context['lunes'], date(2026, 12, 28))
        self.assertEqual(response.context['domingo'], date(2027, 1, 3))


class PanelCalendarioQueryCountTests(TestCase):
    def setUp(self):
        self.especialidad = Especialidad.objects.create(nombre='Especialidad rendimiento', duracion_cita_min=30)
        self.admin = crear_admin_de_prueba('9780000001')
        self.fecha = timezone.localdate() + timedelta(days=7)
        self.client.login(username='9780000001', password='ClaveSegura123')
        self._contador = 0

    def _crear_medicos_con_datos(self, cantidad):
        medicos = []
        for _ in range(cantidad):
            i = self._contador
            self._contador += 1
            medico = crear_medico_de_prueba(f'9790{i:06d}', self.especialidad)
            HorarioMedico.objects.create(
                medico=medico, dia_semana=self.fecha.weekday(), hora_inicio='08:00', hora_fin='12:00',
            )
            ExcepcionHorario.objects.create(
                medico=medico, fecha=self.fecha, hora_inicio='09:00', hora_fin='10:00',
            )
            paciente = crear_paciente_de_prueba(f'9791{i:06d}')
            Cita.objects.create(
                paciente=paciente, medico=medico, estado=EstadoCita.objects.get(nombre='confirmada'),
                fecha=self.fecha, hora_inicio='10:00', hora_fin='10:30',
            )
            medicos.append(medico)
        return medicos

    def test_numero_de_consultas_panoramico_no_crece_con_el_padron(self):
        self._crear_medicos_con_datos(3)
        with CaptureQueriesContext(connection) as pocos:
            self.client.get(reverse('panel_calendario'), {'semana': self.fecha.isoformat()})
        self._crear_medicos_con_datos(15)
        with CaptureQueriesContext(connection) as muchos:
            self.client.get(reverse('panel_calendario'), {'semana': self.fecha.isoformat()})
        self.assertEqual(len(pocos.captured_queries), len(muchos.captured_queries))

    def test_numero_de_consultas_medico_concreto_no_crece_con_el_padron(self):
        """Varía el PADRÓN (más médicos), no la cantidad de citas del médico
        filtrado (queda fija en 1: una por médico creado en
        _crear_medicos_con_datos). No alcanza para detectar un N+1 sobre las
        citas de un médico concreto — ver el test de abajo, que sí varía esa
        cantidad y fue el que detectó el bug de fase 6a."""
        medicos_pocos = self._crear_medicos_con_datos(3)
        medico_objetivo = medicos_pocos[0]
        with CaptureQueriesContext(connection) as pocos:
            self.client.get(
                reverse('panel_calendario'), {'medico': medico_objetivo.pk, 'semana': self.fecha.isoformat()},
            )
        self._crear_medicos_con_datos(15)
        with CaptureQueriesContext(connection) as muchos:
            self.client.get(
                reverse('panel_calendario'), {'medico': medico_objetivo.pk, 'semana': self.fecha.isoformat()},
            )
        self.assertEqual(len(pocos.captured_queries), len(muchos.captured_queries))

    def _crear_citas_para(self, medico, cantidad, offset=0):
        for i in range(cantidad):
            n = offset + i
            paciente = crear_paciente_de_prueba(f'9792{n:06d}')
            Cita.objects.create(
                paciente=paciente, medico=medico, estado=EstadoCita.objects.get(nombre='confirmada'),
                fecha=self.fecha, hora_inicio=f'{13 + n:02d}:00', hora_fin=f'{13 + n:02d}:30',
            )

    def test_numero_de_consultas_medico_concreto_no_crece_con_la_cantidad_de_sus_citas(self):
        """Fase 6b.1, tarea 4: hermano del test de arriba, pero variando el
        eje que sí importa — la cantidad de CITAS del médico filtrado, que es
        lo que _evento_ocupacion recorre en un bucle. Con el bug de fase 6a
        (es_administrador llamado dos veces por cita dentro de ese bucle)
        este test falla: 2 citas -> N consultas, 10 citas -> N+16. Con el
        arreglo (es_admin calculado una sola vez), el conteo no se mueve."""
        medico_objetivo = self._crear_medicos_con_datos(1)[0]
        self._crear_citas_para(medico_objetivo, 2)
        with CaptureQueriesContext(connection) as pocas:
            self.client.get(
                reverse('panel_calendario'), {'medico': medico_objetivo.pk, 'semana': self.fecha.isoformat()},
            )
        self._crear_citas_para(medico_objetivo, 8, offset=2)
        with CaptureQueriesContext(connection) as muchas:
            self.client.get(
                reverse('panel_calendario'), {'medico': medico_objetivo.pk, 'semana': self.fecha.isoformat()},
            )
        self.assertEqual(len(pocas.captured_queries), len(muchas.captured_queries))


# --- Fase 6a.1: cierre del ciclo de vida de la cita y correcciones ----------


class CerrarCitasVencidasTests(TestCase):
    """HU-17: citas.services.cerrar_citas_vencidas."""

    def setUp(self):
        self.especialidad = Especialidad.objects.get(nombre='Pediatría')
        self.medico = crear_medico_de_prueba('7500000001', self.especialidad)
        self.paciente = crear_paciente_de_prueba('7500000002')
        self.hoy = timezone.localdate()

    def _crear_cita(self, hora_inicio, hora_fin, estado_nombre='confirmada', fecha=None):
        estado = EstadoCita.objects.get(nombre=estado_nombre)
        return Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=estado,
            fecha=fecha or self.hoy, hora_inicio=hora_inicio, hora_fin=hora_fin,
        )

    def _hora(self, hora, minuto):
        return timezone.localtime().replace(hour=hora, minute=minuto, second=0, microsecond=0)

    def test_confirmada_con_hora_fin_pasada_queda_no_atendida(self):
        cita = self._crear_cita('08:00', '08:30')
        cerrar_citas_vencidas(ahora=self._hora(15, 0))
        cita.refresh_from_db()
        self.assertEqual(cita.estado.nombre, 'no atendida')

    def test_confirmada_con_hora_fin_futura_no_cambia(self):
        cita = self._crear_cita('08:00', '08:30')
        cerrar_citas_vencidas(ahora=self._hora(6, 0))
        cita.refresh_from_db()
        self.assertEqual(cita.estado.nombre, 'confirmada')

    def test_limite_termino_hace_un_minuto_si_cierra(self):
        cita = self._crear_cita('08:00', '08:30')
        cerrar_citas_vencidas(ahora=self._hora(8, 31))
        cita.refresh_from_db()
        self.assertEqual(cita.estado.nombre, 'no atendida')

    def test_limite_termina_dentro_de_un_minuto_no_cierra(self):
        """Es esto lo que distingue 'hora de fin' de 'fin del día' (decisión 4)."""
        cita = self._crear_cita('08:00', '08:30')
        cerrar_citas_vencidas(ahora=self._hora(8, 29))
        cita.refresh_from_db()
        self.assertEqual(cita.estado.nombre, 'confirmada')

    def test_ya_cancelada_no_cambia(self):
        cita = self._crear_cita('08:00', '08:30', estado_nombre='cancelada')
        cerrar_citas_vencidas(ahora=self._hora(15, 0))
        cita.refresh_from_db()
        self.assertEqual(cita.estado.nombre, 'cancelada')

    def test_ya_atendida_no_cambia(self):
        cita = self._crear_cita('08:00', '08:30', estado_nombre='atendida')
        cerrar_citas_vencidas(ahora=self._hora(15, 0))
        cita.refresh_from_db()
        self.assertEqual(cita.estado.nombre, 'atendida')

    def test_ya_no_atendida_no_cambia_ni_duplica_log(self):
        cita = self._crear_cita('08:00', '08:30', estado_nombre='no atendida')
        total_logs_antes = CitaLog.objects.filter(cita=cita).count()
        cerrar_citas_vencidas(ahora=self._hora(15, 0))
        cita.refresh_from_db()
        self.assertEqual(cita.estado.nombre, 'no atendida')
        self.assertEqual(CitaLog.objects.filter(cita=cita).count(), total_logs_antes)

    def test_idempotente_segunda_ejecucion_no_cambia_nada(self):
        cita = self._crear_cita('08:00', '08:30')
        ahora = self._hora(15, 0)

        primera = cerrar_citas_vencidas(ahora=ahora)
        logs_tras_primera = CitaLog.objects.filter(cita=cita).count()

        segunda = cerrar_citas_vencidas(ahora=ahora)
        logs_tras_segunda = CitaLog.objects.filter(cita=cita).count()

        self.assertEqual(primera, 1)
        self.assertEqual(segunda, 0)
        self.assertEqual(logs_tras_primera, logs_tras_segunda)
        cita.refresh_from_db()
        self.assertEqual(cita.estado.nombre, 'no atendida')

    def test_simular_no_escribe_nada(self):
        cita = self._crear_cita('08:00', '08:30')
        total = cerrar_citas_vencidas(ahora=self._hora(15, 0), dry_run=True)
        self.assertEqual(total, 1)
        cita.refresh_from_db()
        self.assertEqual(cita.estado.nombre, 'confirmada')
        self.assertFalse(CitaLog.objects.filter(cita=cita).exists())

    def test_regresion_rollover_23_59_no_cuelga(self):
        """No hay una suma que pueda envolver medianoche acá (a diferencia de
        _particionar_bloque): es una comparación, no una aritmética. El test
        igual usa ejecutar_con_timeout, por paridad defensiva con el resto
        del archivo, cerca del mismo horario límite que motivó ese bug."""
        cita = self._crear_cita('23:00', '23:59')
        resultado = ejecutar_con_timeout(cerrar_citas_vencidas, ahora=self._hora(23, 59), segundos=3)
        cita.refresh_from_db()
        self.assertEqual(resultado, 1)
        self.assertEqual(cita.estado.nombre, 'no atendida')

    def test_realizado_por_queda_nulo_en_el_cierre_automatico(self):
        cita = self._crear_cita('08:00', '08:30')
        cerrar_citas_vencidas(ahora=self._hora(15, 0))
        log = CitaLog.objects.get(cita=cita, accion='no atendida')
        self.assertIsNone(log.realizado_por)


class CerrarCitasVencidasComandoTests(TestCase):
    """El comando de gestión que envuelve cerrar_citas_vencidas."""

    def setUp(self):
        especialidad = Especialidad.objects.get(nombre='Pediatría')
        self.medico = crear_medico_de_prueba('7600000001', especialidad)
        self.paciente = crear_paciente_de_prueba('7600000002')
        self.estado_confirmada = EstadoCita.objects.get(nombre='confirmada')
        self.ayer = timezone.localdate() - timedelta(days=1)

    def test_comando_informa_cuantas_citas_cerro(self):
        Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=self.estado_confirmada,
            fecha=self.ayer, hora_inicio='08:00', hora_fin='08:30',
        )
        salida = StringIO()
        call_command('cerrar_citas_vencidas', stdout=salida)
        self.assertIn('1 cita(s)', salida.getvalue())

    def test_comando_con_simular_no_escribe(self):
        cita = Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=self.estado_confirmada,
            fecha=self.ayer, hora_inicio='08:00', hora_fin='08:30',
        )
        salida = StringIO()
        call_command('cerrar_citas_vencidas', '--simular', stdout=salida)
        cita.refresh_from_db()
        self.assertEqual(cita.estado.nombre, 'confirmada')
        self.assertIn('Simulación', salida.getvalue())


class CitaLogAdminRealizadoPorNuloTests(TestCase):
    """La pantalla de historial de transiciones que existe hoy es el admin
    de Django (CitaLog no se muestra en ninguna plantilla propia del
    proyecto): tiene que rendear sin error y con un texto explícito cuando
    realizado_por es nulo."""

    def setUp(self):
        especialidad = Especialidad.objects.get(nombre='Pediatría')
        self.medico = crear_medico_de_prueba('7700000001', especialidad)
        self.paciente = crear_paciente_de_prueba('7700000002')
        tipo_documento = TipoDocumento.objects.get(nombre='Cédula de Ciudadanía (CC)')
        Usuario.objects.create_superuser(
            username='7700000003', email='super7700000003@example.com', password='ClaveSegura123',
            tipo_documento=tipo_documento, numero_documento='7700000003', fecha_nacimiento='1980-01-01',
        )
        estado_confirmada = EstadoCita.objects.get(nombre='confirmada')
        self.cita = Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=estado_confirmada,
            fecha=timezone.localdate() - timedelta(days=1), hora_inicio='08:00', hora_fin='08:30',
        )
        cambiar_estado_cita(self.cita, 'no atendida', realizado_por=None, detalle='Cierre automático.')
        self.client.login(username='7700000003', password='ClaveSegura123')

    def test_changelist_muestra_texto_de_sistema_para_realizado_por_nulo(self):
        response = self.client.get(reverse('admin:citas_citalog_changelist'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Sistema (automático)')


class SeparadorDecimalRegressionTests(TestCase):
    """Tarea 6: regresión del bug de coma decimal con LANGUAGE_CODE='es-co'
    (ver citas/calendario_geometria.py). Corre bajo el LANGUAGE_CODE real del
    proyecto (no hay override acá) — si no, no prueba nada."""

    def setUp(self):
        self.especialidad = Especialidad.objects.create(nombre='Regresión decimal', duracion_cita_min=30)
        self.admin = crear_admin_de_prueba('9970000001')
        self.medico = crear_medico_de_prueba('9970000002', self.especialidad)
        self.fecha = timezone.localdate() + timedelta(days=7)
        HorarioMedico.objects.create(
            medico=self.medico, dia_semana=self.fecha.weekday(), hora_inicio='08:00', hora_fin='12:00',
        )
        self.client.login(username='9970000001', password='ClaveSegura123')

    def test_el_atributo_style_usa_punto_decimal_bajo_el_language_code_real(self):
        self.assertEqual(settings.LANGUAGE_CODE, 'es-co')

        response = self.client.get(
            reverse('panel_calendario'), {'medico': self.medico.pk, 'semana': self.fecha.isoformat()},
        )
        html = response.content.decode()
        estilos = re.findall(r'style="([^"]*top:[^"]*)"', html)
        self.assertTrue(estilos, 'No se encontró ninguna caja posicionada en el HTML para verificar.')
        for estilo in estilos:
            numeros = re.findall(r'top:(-?[\d.,]+)px', estilo)
            self.assertTrue(numeros, f'No se pudo extraer el valor de top: en {estilo!r}')
            for numero in numeros:
                self.assertNotIn(',', numero, f'Separador decimal inválido (coma) en style: {estilo!r}')


# --- Fase 6b: corrección de una cita no atendida a atendida (decisión 4) ----

class CorreccionNoAtendidaTests(TestCase):
    def setUp(self):
        especialidad = Especialidad.objects.get(nombre='Pediatría')
        self.medico = crear_medico_de_prueba('9800000001', especialidad)
        self.otro_medico = crear_medico_de_prueba('9800000002', especialidad)
        self.paciente = crear_paciente_de_prueba('9800000003')
        self.estado_no_atendida = EstadoCita.objects.get(nombre='no atendida')
        self.estado_cancelada = EstadoCita.objects.get(nombre='cancelada')
        self.fecha_pasada = timezone.localdate() - timedelta(days=1)
        self.fecha_futura = timezone.localdate() + timedelta(days=7)

    def _cita(self, estado, fecha, medico=None):
        cita = Cita.objects.create(
            paciente=self.paciente, medico=medico or self.medico, estado=estado,
            fecha=fecha, hora_inicio='08:00', hora_fin='08:30',
        )
        cita.refresh_from_db()
        return cita

    def test_no_atendida_vencida_propia_se_puede_marcar_atendida(self):
        cita = self._cita(self.estado_no_atendida, self.fecha_pasada)
        self.assertTrue(puede_marcar_atendida(cita, self.medico.usuario))

    def test_no_atendida_no_vencida_no_se_puede_marcar_atendida(self):
        cita = self._cita(self.estado_no_atendida, self.fecha_futura)
        self.assertFalse(puede_marcar_atendida(cita, self.medico.usuario))

    def test_cancelada_no_se_puede_marcar_atendida(self):
        cita = self._cita(self.estado_cancelada, self.fecha_pasada)
        self.assertFalse(puede_marcar_atendida(cita, self.medico.usuario))

    def test_no_atendida_de_otro_medico_no_se_puede_corregir(self):
        cita = self._cita(self.estado_no_atendida, self.fecha_pasada, medico=self.otro_medico)
        self.assertFalse(puede_marcar_atendida(cita, self.medico.usuario))

    def test_correccion_via_vista_queda_con_la_cadena_completa_en_citalog(self):
        self.client.login(username='9800000001', password='ClaveSegura123')
        cita = Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=EstadoCita.objects.get(nombre='confirmada'),
            fecha=self.fecha_pasada, hora_inicio='08:00', hora_fin='08:30',
        )
        cerrar_citas_vencidas(ahora=timezone.localtime())
        cita.refresh_from_db()
        self.assertEqual(cita.estado.nombre, 'no atendida')

        response = self.client.post(reverse('medico_marcar_atendida', args=[cita.pk]))
        self.assertRedirects(response, reverse('medico_cita_detalle', args=[cita.pk]))

        cita.refresh_from_db()
        self.assertEqual(cita.estado.nombre, 'atendida')

        acciones = list(CitaLog.objects.filter(cita=cita).order_by('fecha_creacion').values_list('accion', flat=True))
        self.assertEqual(acciones, ['no atendida', 'atendida'])

    def test_no_se_puede_corregir_una_cita_de_otro_medico_via_vista(self):
        self.client.login(username='9800000001', password='ClaveSegura123')
        cita = self._cita(self.estado_no_atendida, self.fecha_pasada, medico=self.otro_medico)
        response = self.client.post(reverse('medico_marcar_atendida', args=[cita.pk]))
        self.assertEqual(response.status_code, 404)
        cita.refresh_from_db()
        self.assertEqual(cita.estado.nombre, 'no atendida')


# --- Fase 6b: agenda acotada a confirmadas (HU-25) e historial del médico ---
# (HU-27) --------------------------------------------------------------------

class MedicoAgendaHistorialTests(TestCase):
    """Decisión 3: la agenda es la lista de trabajo (solo confirmadas); todo
    lo demás vive en el historial nuevo, espejando mis_citas/mis_citas_historial
    del paciente."""

    def setUp(self):
        especialidad = Especialidad.objects.get(nombre='Pediatría')
        self.medico = crear_medico_de_prueba('9810000001', especialidad)
        self.otro_medico = crear_medico_de_prueba('9810000002', especialidad)
        self.paciente = crear_paciente_de_prueba('9810000003')
        self.fecha_futura = timezone.localdate() + timedelta(days=3)
        self.fecha_pasada = timezone.localdate() - timedelta(days=3)
        self.client.login(username='9810000001', password='ClaveSegura123')

    def _cita(self, estado_nombre, fecha, hora_inicio='08:00', hora_fin='08:30', medico=None, **extra):
        return Cita.objects.create(
            paciente=self.paciente, medico=medico or self.medico,
            estado=EstadoCita.objects.get(nombre=estado_nombre),
            fecha=fecha, hora_inicio=hora_inicio, hora_fin=hora_fin, **extra,
        )

    def test_agenda_lista_solo_confirmadas(self):
        confirmada = self._cita('confirmada', self.fecha_futura, '08:00', '08:30')
        self._cita('atendida', self.fecha_futura, '09:00', '09:30')
        self._cita('cancelada', self.fecha_futura, '10:00', '10:30')
        self._cita('no atendida', self.fecha_futura, '11:00', '11:30')

        response = self.client.get(reverse('medico_agenda'))
        ids = {evento.origen.pk for evento in response.context['eventos']}

        self.assertEqual(ids, {confirmada.pk})

    def test_las_tres_no_confirmadas_aparecen_en_el_historial(self):
        atendida = self._cita('atendida', self.fecha_pasada, '08:00', '08:30')
        cancelada = self._cita('cancelada', self.fecha_futura, '09:00', '09:30')
        no_atendida = self._cita('no atendida', self.fecha_pasada, '10:00', '10:30')

        response = self.client.get(reverse('medico_historial'))
        ids = {cita.pk for cita in response.context['citas']}

        self.assertEqual(ids, {atendida.pk, cancelada.pk, no_atendida.pk})

    def test_confirmada_por_venir_no_aparece_en_el_historial(self):
        confirmada = self._cita('confirmada', self.fecha_futura)
        response = self.client.get(reverse('medico_historial'))
        ids = {cita.pk for cita in response.context['citas']}
        self.assertNotIn(confirmada.pk, ids)

    def test_comentario_se_muestra_en_el_historial_cuando_existe(self):
        self._cita('atendida', self.fecha_pasada, comentario_medico='Todo en orden, control en un mes.')
        response = self.client.get(reverse('medico_historial'))
        self.assertContains(response, 'Todo en orden, control en un mes.')

    def test_mensajes_de_vacio_distintos_entre_agenda_e_historial(self):
        respuesta_agenda = self.client.get(reverse('medico_agenda'))
        respuesta_historial = self.client.get(reverse('medico_historial'))
        self.assertContains(respuesta_agenda, 'No tienes citas confirmadas en los próximos días.')
        self.assertContains(respuesta_historial, 'Todavía no tienes citas en tu historial.')

    def test_agenda_e_historial_no_muestran_citas_de_otro_medico(self):
        self._cita('confirmada', self.fecha_futura, '11:00', '11:30', medico=self.otro_medico)
        self._cita('atendida', self.fecha_pasada, '11:00', '11:30', medico=self.otro_medico)

        respuesta_agenda = self.client.get(reverse('medico_agenda'))
        respuesta_historial = self.client.get(reverse('medico_historial'))

        self.assertEqual(len(respuesta_agenda.context['eventos']), 0)
        self.assertEqual(len(respuesta_historial.context['citas']), 0)

    def test_agenda_y_historial_ignoran_un_id_de_otro_medico_en_la_url(self):
        """Ninguna de las dos vistas lee parámetros de la URL para decidir el
        alcance (siempre es request.user.medico) — pasar el id de otro médico
        no tiene ningún efecto."""
        propia = self._cita('confirmada', self.fecha_futura)
        respuesta_agenda = self.client.get(reverse('medico_agenda'), {'medico': self.otro_medico.pk})
        respuesta_historial = self.client.get(reverse('medico_historial'), {'medico': self.otro_medico.pk})

        ids_agenda = {evento.origen.pk for evento in respuesta_agenda.context['eventos']}
        self.assertEqual(ids_agenda, {propia.pk})
        self.assertEqual(len(respuesta_historial.context['citas']), 0)


class MedicoHistorialSeguridadTests(TestCase):
    def setUp(self):
        especialidad = Especialidad.objects.create(nombre='Especialidad seguridad historial', duracion_cita_min=30)
        self.medico = crear_medico_de_prueba('9850000001', especialidad)
        self.paciente = crear_paciente_de_prueba('9850000002')
        self.admin = crear_admin_de_prueba('9850000003')

    def test_paciente_no_accede(self):
        self.client.login(username='9850000002', password='ClaveSegura123')
        response = self.client.get(reverse('medico_historial'))
        self.assertEqual(response.status_code, 403)

    def test_admin_no_accede(self):
        self.client.login(username='9850000003', password='ClaveSegura123')
        response = self.client.get(reverse('medico_historial'))
        self.assertEqual(response.status_code, 403)

    def test_anonimo_es_redirigido_a_login(self):
        response = self.client.get(reverse('medico_historial'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)

    def test_post_no_crea_ni_modifica_nada(self):
        self.client.login(username='9850000001', password='ClaveSegura123')
        citas_antes = Cita.objects.count()
        self.client.post(reverse('medico_historial'))
        self.assertEqual(Cita.objects.count(), citas_antes)


# --- Fase 6b: calendario del médico (HU-21) ----------------------------------

class MedicoCalendarioTests(TestCase):
    def setUp(self):
        especialidad = Especialidad.objects.create(nombre='Especialidad calendario médico', duracion_cita_min=30)
        self.medico = crear_medico_de_prueba('9820000001', especialidad)
        self.otro_medico = crear_medico_de_prueba('9820000002', especialidad)
        self.paciente = crear_paciente_de_prueba('9820000003')
        self.admin = crear_admin_de_prueba('9820000004')
        self.fecha = timezone.localdate() + timedelta(days=7)
        HorarioMedico.objects.create(
            medico=self.medico, dia_semana=self.fecha.weekday(), hora_inicio='08:00', hora_fin='12:00',
        )
        ExcepcionHorario.objects.create(
            medico=self.medico, fecha=self.fecha, hora_inicio='09:00', hora_fin='10:00',
        )
        self.cita = Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=EstadoCita.objects.get(nombre='confirmada'),
            fecha=self.fecha, hora_inicio='10:00', hora_fin='10:30',
        )

    def test_incluye_las_tres_capas(self):
        self.client.login(username='9820000001', password='ClaveSegura123')
        response = self.client.get(reverse('medico_calendario'), {'semana': self.fecha.isoformat()})
        eventos = [evento for dia in response.context['dias'] for evento in dia['eventos']]
        self.assertEqual({evento.tipo for evento in eventos}, {'disponibilidad', 'bloqueo', 'ocupacion'})

    def _fragmento_leyenda(self, response):
        # El modal (panel/_modal_calendario.html) trae "Disponible"/"Bloqueo"
        # como texto literal en su <script> (badgeEstado), sin importar qué
        # eventos haya: buscar en la respuesta completa daría un falso
        # positivo. Se acota a la sección de la leyenda.
        html = response.content.decode()
        inicio = html.index('id="calendario-leyenda"')
        return html[inicio:html.index('</section>', inicio)]

    def test_leyenda_deriva_de_lo_visible_e_incluye_disponible_y_bloqueo(self):
        """Fase 6b.1, tarea 5: la leyenda ya no depende de panoramico, sino
        de qué tipos de evento trae la semana. El fixture de esta clase
        tiene las tres capas, así que la leyenda debe listarlas."""
        self.client.login(username='9820000001', password='ClaveSegura123')
        response = self.client.get(reverse('medico_calendario'), {'semana': self.fecha.isoformat()})
        self.assertEqual(
            response.context['tipos_visibles'], {'disponibilidad', 'bloqueo', 'ocupacion'},
        )
        leyenda = self._fragmento_leyenda(response)
        self.assertIn('Disponible', leyenda)
        self.assertIn('Bloqueo', leyenda)

    def test_calendario_y_lista_muestran_las_mismas_citas(self):
        self.client.login(username='9820000001', password='ClaveSegura123')
        respuesta_calendario = self.client.get(reverse('medico_calendario'), {'semana': self.fecha.isoformat()})
        respuesta_agenda = self.client.get(reverse('medico_agenda'))

        ids_calendario = {
            evento.origen.pk for dia in respuesta_calendario.context['dias']
            for evento in dia['eventos'] if evento.tipo == 'ocupacion'
        }
        ids_agenda = {evento.origen.pk for evento in respuesta_agenda.context['eventos']}
        self.assertEqual(ids_calendario, ids_agenda)
        self.assertEqual(ids_calendario, {self.cita.pk})

    def test_paciente_no_accede(self):
        self.client.login(username='9820000003', password='ClaveSegura123')
        response = self.client.get(reverse('medico_calendario'))
        self.assertEqual(response.status_code, 403)

    def test_admin_no_accede(self):
        self.client.login(username='9820000004', password='ClaveSegura123')
        response = self.client.get(reverse('medico_calendario'))
        self.assertEqual(response.status_code, 403)

    def test_anonimo_es_redirigido_a_login(self):
        response = self.client.get(reverse('medico_calendario'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)

    def test_post_no_crea_ni_modifica_nada(self):
        self.client.login(username='9820000001', password='ClaveSegura123')
        citas_antes = Cita.objects.count()
        excepciones_antes = ExcepcionHorario.objects.count()
        self.client.post(reverse('medico_calendario'), {'semana': self.fecha.isoformat()})
        self.assertEqual(Cita.objects.count(), citas_antes)
        self.assertEqual(ExcepcionHorario.objects.count(), excepciones_antes)

    def test_no_ve_citas_de_otro_medico_aunque_pase_su_id_por_la_url(self):
        Cita.objects.create(
            paciente=self.paciente, medico=self.otro_medico, estado=EstadoCita.objects.get(nombre='confirmada'),
            fecha=self.fecha, hora_inicio='08:00', hora_fin='08:30',
        )
        self.client.login(username='9820000001', password='ClaveSegura123')
        response = self.client.get(
            reverse('medico_calendario'), {'medico': self.otro_medico.pk, 'semana': self.fecha.isoformat()},
        )
        eventos_ocupacion = [
            evento for dia in response.context['dias'] for evento in dia['eventos'] if evento.tipo == 'ocupacion'
        ]
        self.assertTrue(eventos_ocupacion)
        self.assertTrue(all(evento.medico.pk == self.medico.pk for evento in eventos_ocupacion))


class MedicoCalendarioQueryCountTests(TestCase):
    def setUp(self):
        self.especialidad = Especialidad.objects.create(nombre='Especialidad query médico', duracion_cita_min=30)
        self.medico = crear_medico_de_prueba('9830000001', self.especialidad)
        self.fecha = timezone.localdate() + timedelta(days=7)
        HorarioMedico.objects.create(
            medico=self.medico, dia_semana=self.fecha.weekday(), hora_inicio='08:00', hora_fin='18:00',
        )
        self.client.login(username='9830000001', password='ClaveSegura123')
        self._contador = 0

    def _crear_citas(self, cantidad):
        for _ in range(cantidad):
            i = self._contador
            self._contador += 1
            paciente = crear_paciente_de_prueba(f'9831{i:06d}')
            Cita.objects.create(
                paciente=paciente, medico=self.medico, estado=EstadoCita.objects.get(nombre='confirmada'),
                fecha=self.fecha, hora_inicio=f'{8 + i:02d}:00', hora_fin=f'{8 + i:02d}:30',
            )

    def test_numero_de_consultas_no_crece_con_la_cantidad_de_citas(self):
        self._crear_citas(2)
        with CaptureQueriesContext(connection) as pocas:
            self.client.get(reverse('medico_calendario'), {'semana': self.fecha.isoformat()})
        self._crear_citas(8)
        with CaptureQueriesContext(connection) as muchas:
            self.client.get(reverse('medico_calendario'), {'semana': self.fecha.isoformat()})
        self.assertEqual(len(pocas.captured_queries), len(muchas.captured_queries))


# --- Fase 6b: calendario del paciente (HU-22) --------------------------------

class PacienteCalendarioTests(TestCase):
    def setUp(self):
        especialidad = Especialidad.objects.create(nombre='Especialidad calendario paciente', duracion_cita_min=30)
        self.medico = crear_medico_de_prueba('9840000001', especialidad)
        self.paciente = crear_paciente_de_prueba('9840000002')
        self.otro_paciente = crear_paciente_de_prueba('9840000003')
        self.fecha = timezone.localdate() + timedelta(days=7)
        HorarioMedico.objects.create(
            medico=self.medico, dia_semana=self.fecha.weekday(), hora_inicio='08:00', hora_fin='12:00',
        )
        self.cita = Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=EstadoCita.objects.get(nombre='confirmada'),
            fecha=self.fecha, hora_inicio='08:00', hora_fin='08:30',
        )
        Cita.objects.create(
            paciente=self.otro_paciente, medico=self.medico, estado=EstadoCita.objects.get(nombre='confirmada'),
            fecha=self.fecha, hora_inicio='09:00', hora_fin='09:30',
        )
        self.client.login(username='9840000002', password='ClaveSegura123')

    def test_solo_contiene_eventos_de_tipo_ocupacion(self):
        response = self.client.get(reverse('mis_citas_calendario'), {'semana': self.fecha.isoformat()})
        eventos = [evento for dia in response.context['dias'] for evento in dia['eventos']]
        self.assertTrue(eventos)
        self.assertEqual({evento.tipo for evento in eventos}, {'ocupacion'})

    def test_solo_ve_sus_propias_citas_aunque_pase_el_id_de_otro_medico_por_la_url(self):
        response = self.client.get(
            reverse('mis_citas_calendario'),
            {'medico': self.medico.pk, 'especialidad': '999', 'semana': self.fecha.isoformat()},
        )
        eventos = [evento for dia in response.context['dias'] for evento in dia['eventos']]
        ids = {evento.origen.pk for evento in eventos}
        self.assertEqual(ids, {self.cita.pk})

    def test_la_leyenda_no_lista_disponible_ni_bloqueo(self):
        """Fase 6b.1, tarea 5: el alcance del paciente nunca produce esos
        tipos de evento (_alcance_por_rol), así que una leyenda derivada de
        lo visible los omite sola, sin condicional por rol en el parcial.

        Se acota a la sección de la leyenda: panel/_modal_calendario.html
        trae "Disponible"/"Bloqueo" como texto literal en su <script>
        (badgeEstado) sin importar qué eventos haya."""
        response = self.client.get(reverse('mis_citas_calendario'), {'semana': self.fecha.isoformat()})
        self.assertEqual(response.context['tipos_visibles'], {'ocupacion'})
        html = response.content.decode()
        inicio = html.index('id="calendario-leyenda"')
        leyenda = html[inicio:html.index('</section>', inicio)]
        self.assertNotIn('Disponible', leyenda)
        self.assertNotIn('Bloqueo', leyenda)

    def test_anonimo_es_redirigido_a_login(self):
        self.client.logout()
        response = self.client.get(reverse('mis_citas_calendario'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)

    def test_post_no_crea_ni_modifica_nada(self):
        citas_antes = Cita.objects.count()
        self.client.post(reverse('mis_citas_calendario'), {'semana': self.fecha.isoformat()})
        self.assertEqual(Cita.objects.count(), citas_antes)


class PacienteCalendarioQueryCountTests(TestCase):
    def setUp(self):
        self.especialidad = Especialidad.objects.create(nombre='Especialidad query paciente', duracion_cita_min=30)
        self.paciente = crear_paciente_de_prueba('9860000001')
        self.fecha = timezone.localdate() + timedelta(days=7)
        self.client.login(username='9860000001', password='ClaveSegura123')
        self._contador = 0

    def _crear_citas(self, cantidad):
        for _ in range(cantidad):
            i = self._contador
            self._contador += 1
            medico = crear_medico_de_prueba(f'9861{i:06d}', self.especialidad)
            Cita.objects.create(
                paciente=self.paciente, medico=medico, estado=EstadoCita.objects.get(nombre='confirmada'),
                fecha=self.fecha, hora_inicio='08:00', hora_fin='08:30',
            )

    def test_numero_de_consultas_no_crece_con_la_cantidad_de_citas(self):
        self._crear_citas(2)
        with CaptureQueriesContext(connection) as pocas:
            self.client.get(reverse('mis_citas_calendario'), {'semana': self.fecha.isoformat()})
        self._crear_citas(8)
        with CaptureQueriesContext(connection) as muchas:
            self.client.get(reverse('mis_citas_calendario'), {'semana': self.fecha.isoformat()})
        self.assertEqual(len(pocas.captured_queries), len(muchas.captured_queries))
