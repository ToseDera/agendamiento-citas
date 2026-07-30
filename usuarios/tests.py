from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse

from datetime import timedelta

from django.utils import timezone

from citas.models import Cita, EstadoCita, Especialidad, HorarioMedico

from .htmlform_utils import datos_formulario_reales, opciones_reales_de_select
from .models import Medico, TipoDocumento, Usuario


def crear_usuario(numero_documento, tipo_documento, **kwargs):
    defaults = {
        'username': numero_documento,
        'password': 'ClaveSegura123',
        'tipo_documento': tipo_documento,
        'numero_documento': numero_documento,
        'fecha_nacimiento': '1990-01-01',
        'email': f'{numero_documento}@example.com',
    }
    defaults.update(kwargs)
    return Usuario.objects.create_user(**defaults)


class RegistroTests(TestCase):
    def setUp(self):
        self.tipo_documento = TipoDocumento.objects.get(nombre='Cédula de ciudadanía')

    def datos_registro(self, **overrides):
        datos = {
            'nombre': 'Ana',
            'apellido': 'Gómez',
            'fecha_nacimiento': '1990-05-10',
            'tipo_documento': self.tipo_documento.pk,
            'numero_documento': '1000123456',
            'correo': 'ana.gomez@example.com',
            'telefono': '3001234567',
            'password1': 'ClaveSegura123',
            'password2': 'ClaveSegura123',
        }
        datos.update(overrides)
        return datos

    def test_registro_crea_usuario_con_username_numero_documento_y_grupo_paciente(self):
        response = self.client.post(reverse('registro'), self.datos_registro())
        self.assertRedirects(response, reverse('login'))

        usuario = Usuario.objects.get(numero_documento='1000123456')
        self.assertEqual(usuario.username, '1000123456')
        self.assertTrue(usuario.groups.filter(name='Paciente').exists())

    def test_registro_exitoso_muestra_mensaje_de_exito_en_login(self):
        response = self.client.post(reverse('registro'), self.datos_registro(), follow=True)
        self.assertContains(response, 'Cuenta creada correctamente. Ya puedes iniciar sesión.')

    def test_select_tipo_documento_se_renderiza_con_value_pk_no_codigo(self):
        response = self.client.get(reverse('registro'))
        self.assertContains(
            response,
            f'<option value="{self.tipo_documento.pk}">Cédula de ciudadanía</option>',
            html=True,
        )

    def test_registro_con_datos_tal_como_los_envia_el_navegador_crea_el_usuario(self):
        response = self.client.get(reverse('registro'))
        self.assertContains(response, 'name="tipo_documento"')

        datos_navegador = self.datos_registro(
            numero_documento='1000555555', correo='browser@example.com',
        )
        # El value real que un navegador envía es el del <option> renderizado
        # por el <select> del form, no un código como "CC".
        response = self.client.post(reverse('registro'), datos_navegador)

        self.assertRedirects(response, reverse('login'))
        usuario = Usuario.objects.get(numero_documento='1000555555')
        self.assertEqual(usuario.tipo_documento, self.tipo_documento)

    def test_registro_ignora_campo_de_rol_enviado_por_el_cliente(self):
        datos = self.datos_registro(numero_documento='1000999999', correo='otro@example.com')
        datos['groups'] = [Group.objects.get(name='Administrador').pk]
        datos['is_staff'] = True
        datos['is_superuser'] = True

        self.client.post(reverse('registro'), datos)

        usuario = Usuario.objects.get(numero_documento='1000999999')
        grupos = set(usuario.groups.values_list('name', flat=True))
        self.assertEqual(grupos, {'Paciente'})
        self.assertFalse(usuario.is_staff)
        self.assertFalse(usuario.is_superuser)

    def test_registro_con_numero_documento_repetido_no_revienta_y_muestra_error(self):
        datos = self.datos_registro()
        primera_respuesta = self.client.post(reverse('registro'), datos)
        self.assertRedirects(primera_respuesta, reverse('login'))

        datos_repetidos = self.datos_registro(correo='otro-correo@example.com')
        segunda_respuesta = self.client.post(reverse('registro'), datos_repetidos)

        self.assertEqual(segunda_respuesta.status_code, 200)
        self.assertFormError(
            segunda_respuesta.context['form'], 'numero_documento',
            'Ya existe una cuenta con este número de documento.',
        )
        self.assertEqual(
            Usuario.objects.filter(numero_documento=datos['numero_documento']).count(), 1,
        )

    def test_registro_invalido_repuebla_los_campos_menos_las_contrasenas(self):
        datos = self.datos_registro(password1='corta', password2='corta')
        response = self.client.post(reverse('registro'), datos)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="Ana"')
        self.assertContains(response, 'value="Gómez"')
        self.assertContains(response, 'value="1000123456"')
        self.assertContains(response, 'value="ana.gomez@example.com"')
        self.assertContains(response, 'value="3001234567"')
        self.assertContains(response, 'value="1990-05-10"')

        self.assertNotContains(response, 'value="corta"')

    def test_registro_ida_y_vuelta_con_los_campos_reales_del_html(self):
        respuesta_get = self.client.get(reverse('registro'))
        html = respuesta_get.content.decode()
        datos = datos_formulario_reales(html)
        # tipo_documento trae por defecto el placeholder en blanco de Django
        # (ModelChoiceField.empty_label); un usuario real sí debe elegir uno.
        datos['tipo_documento'] = opciones_reales_de_select(html, 'tipo_documento')[0]
        datos.update({
            'nombre': 'Carla',
            'apellido': 'Ruiz',
            'fecha_nacimiento': '1992-04-01',
            'numero_documento': '1000777777',
            'correo': 'carla.ruiz@example.com',
            'telefono': '3007777777',
            'password1': 'ClaveSegura123',
            'password2': 'ClaveSegura123',
        })

        respuesta_post = self.client.post(reverse('registro'), datos)
        self.assertRedirects(respuesta_post, reverse('login'))

        usuario = Usuario.objects.get(numero_documento='1000777777')
        self.assertEqual(usuario.username, '1000777777')
        self.assertTrue(usuario.groups.filter(name='Paciente').exists())


class LoginTests(TestCase):
    def setUp(self):
        self.tipo_documento = TipoDocumento.objects.get(nombre='Cédula de ciudadanía')
        self.usuario = Usuario.objects.create_user(
            username='1000123456',
            password='ClaveSegura123',
            tipo_documento=self.tipo_documento,
            numero_documento='1000123456',
            fecha_nacimiento='1990-05-10',
            email='ana.gomez@example.com',
        )

    def test_login_correcto_redirige_a_inicio(self):
        response = self.client.post(reverse('login'), {
            'username': '1000123456',
            'password': 'ClaveSegura123',
        })
        self.assertRedirects(response, reverse('inicio'))


class InicioViewTests(TestCase):
    def test_usuario_anonimo_es_redirigido_a_login(self):
        response = self.client.get(reverse('inicio'))
        self.assertRedirects(response, f"{reverse('login')}?next={reverse('inicio')}")

    def test_paciente_ve_el_enlace_para_agendar_cita_en_inicio(self):
        tipo_documento = TipoDocumento.objects.get(nombre='Cédula de ciudadanía')
        paciente = crear_usuario('9100000001', tipo_documento)
        paciente.groups.add(Group.objects.get(name='Paciente'))
        self.client.login(username='9100000001', password='ClaveSegura123')

        response = self.client.get(reverse('inicio'))

        self.assertContains(response, 'Agendar cita')
        self.assertContains(response, reverse('agendar_especialidades'))


class UsuarioManagerTests(TestCase):
    def test_create_superuser_resuelve_tipo_documento_pasado_como_string(self):
        tipo_documento = TipoDocumento.objects.get(nombre='Cédula de ciudadanía')

        usuario = Usuario.objects.create_superuser(
            username='999888777',
            email='admin@medsync.test',
            password='AdminSeguro123',
            tipo_documento=str(tipo_documento.pk),
            fecha_nacimiento='1990-01-01',
        )

        self.assertEqual(usuario.tipo_documento, tipo_documento)
        self.assertTrue(usuario.is_superuser)
        self.assertTrue(usuario.is_staff)


class PanelAccesoTests(TestCase):
    """HU-10 y seguridad del panel: solo Administrador entra, nunca 200 para otros."""

    def setUp(self):
        self.tipo_documento = TipoDocumento.objects.get(nombre='Cédula de ciudadanía')
        self.admin = crear_usuario('1111111111', self.tipo_documento)
        self.admin.groups.add(Group.objects.get(name='Administrador'))
        self.paciente = crear_usuario('2222222222', self.tipo_documento)
        self.paciente.groups.add(Group.objects.get(name='Paciente'))

    def urls_panel(self):
        return [
            reverse('panel_home'),
            reverse('panel_especialidades'),
            reverse('panel_especialidad_nueva'),
            reverse('panel_medicos'),
            reverse('panel_medico_nuevo'),
        ]

    def test_hu10_login_de_administrador_redirige_al_panel(self):
        self.client.login(username='1111111111', password='ClaveSegura123')
        response = self.client.get(reverse('inicio'))
        self.assertRedirects(response, reverse('panel_home'))

    def test_hu10_login_de_paciente_no_redirige_al_panel(self):
        self.client.login(username='2222222222', password='ClaveSegura123')
        response = self.client.get(reverse('inicio'))
        self.assertEqual(response.status_code, 200)

    def test_paciente_recibe_pagina_403_personalizada(self):
        self.client.login(username='2222222222', password='ClaveSegura123')
        response = self.client.get(reverse('panel_home'))
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, 'No tiene permisos de administrador', status_code=403)

    def test_admin_accede_al_panel(self):
        self.client.login(username='1111111111', password='ClaveSegura123')
        response = self.client.get(reverse('panel_home'))
        self.assertEqual(response.status_code, 200)

    def test_paciente_autenticado_nunca_recibe_200_en_el_panel(self):
        self.client.login(username='2222222222', password='ClaveSegura123')
        for url in self.urls_panel():
            self.assertEqual(self.client.get(url).status_code, 403, url)
            self.assertNotEqual(self.client.post(url).status_code, 200, url)

    def test_usuario_anonimo_nunca_recibe_200_en_el_panel(self):
        for url in self.urls_panel():
            self.assertNotEqual(self.client.get(url).status_code, 200, url)
            self.assertNotEqual(self.client.post(url).status_code, 200, url)


class PanelMedicoRegistroTests(TestCase):
    """HU-11: registrar médicos desde el panel del Administrador."""

    def setUp(self):
        self.tipo_documento = TipoDocumento.objects.get(nombre='Cédula de ciudadanía')
        self.especialidad = Especialidad.objects.get(nombre='Medicina general')
        self.admin = crear_usuario('1111111111', self.tipo_documento)
        self.admin.groups.add(Group.objects.get(name='Administrador'))
        self.client.login(username='1111111111', password='ClaveSegura123')

    def datos_medico(self, **overrides):
        datos = {
            'nombre': 'Carlos',
            'apellido': 'Ruiz',
            'fecha_nacimiento': '1980-03-15',
            'tipo_documento': self.tipo_documento.pk,
            'numero_documento': '3000000001',
            'correo': 'carlos.ruiz@example.com',
            'telefono': '3000000002',
            'especialidad': self.especialidad.pk,
            'registro_medico': 'RM-123',
            'password1': 'ClaveSegura123',
            'password2': 'ClaveSegura123',
        }
        datos.update(overrides)
        return datos

    def test_hu11_admin_registra_medico_exitosamente(self):
        response = self.client.post(reverse('panel_medico_nuevo'), self.datos_medico())
        self.assertRedirects(response, reverse('panel_medicos'))

        usuario = Usuario.objects.get(numero_documento='3000000001')
        self.assertEqual(usuario.username, '3000000001')
        self.assertTrue(usuario.groups.filter(name='Medico').exists())

        medico = Medico.objects.get(usuario=usuario)
        self.assertEqual(medico.especialidad, self.especialidad)

        self.client.logout()
        self.assertTrue(self.client.login(username='3000000001', password='ClaveSegura123'))

    def test_hu11_cedula_duplicada_no_crea_nada_a_medias(self):
        crear_usuario('3000000009', self.tipo_documento)
        response = self.client.post(
            reverse('panel_medico_nuevo'), self.datos_medico(numero_documento='3000000009'),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Usuario.objects.filter(numero_documento='3000000009').count(), 1)
        self.assertFalse(Medico.objects.filter(usuario__numero_documento='3000000009').exists())

    def test_hu11_campos_vacios_no_crean_usuario_ni_medico(self):
        response = self.client.post(reverse('panel_medico_nuevo'), self.datos_medico(nombre=''))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Usuario.objects.filter(numero_documento='3000000001').exists())
        self.assertFalse(Medico.objects.filter(usuario__numero_documento='3000000001').exists())

    def test_hu11_si_falla_la_creacion_del_medico_no_queda_usuario_a_medias(self):
        datos = self.datos_medico(numero_documento='3000000077', correo='falla@example.com')
        with patch('usuarios.models.Medico.objects.create', side_effect=Exception('fallo simulado')):
            with self.assertRaises(Exception):
                self.client.post(reverse('panel_medico_nuevo'), datos)
        self.assertFalse(Usuario.objects.filter(numero_documento='3000000077').exists())

    def test_hu11_ida_y_vuelta_con_los_campos_reales_del_html(self):
        respuesta_get = self.client.get(reverse('panel_medico_nuevo'))
        html = respuesta_get.content.decode()
        datos = datos_formulario_reales(html)
        # tipo_documento y especialidad traen el placeholder en blanco de
        # Django por defecto; un admin real sí debe elegir uno de cada uno.
        datos['tipo_documento'] = opciones_reales_de_select(html, 'tipo_documento')[0]
        datos['especialidad'] = opciones_reales_de_select(html, 'especialidad')[0]
        datos.update({
            'nombre': 'Marta',
            'apellido': 'Lopez',
            'fecha_nacimiento': '1982-07-20',
            'numero_documento': '3000000088',
            'correo': 'marta.lopez@example.com',
            'telefono': '3000000089',
            'registro_medico': 'RM-888',
            'password1': 'ClaveSegura123',
            'password2': 'ClaveSegura123',
        })

        respuesta_post = self.client.post(reverse('panel_medico_nuevo'), datos)
        self.assertRedirects(respuesta_post, reverse('panel_medicos'))

        usuario = Usuario.objects.get(numero_documento='3000000088')
        self.assertTrue(usuario.groups.filter(name='Medico').exists())
        self.assertTrue(Medico.objects.filter(usuario=usuario).exists())


class PanelMedicoEdicionTests(TestCase):
    """HU-12: editar/desactivar médicos."""

    def setUp(self):
        self.tipo_documento = TipoDocumento.objects.get(nombre='Cédula de ciudadanía')
        self.especialidad = Especialidad.objects.get(nombre='Medicina general')
        self.admin = crear_usuario('1111111111', self.tipo_documento)
        self.admin.groups.add(Group.objects.get(name='Administrador'))
        self.client.login(username='1111111111', password='ClaveSegura123')

        self.medico_usuario = crear_usuario(
            '4000000001', self.tipo_documento, first_name='Laura', last_name='Diaz',
        )
        self.medico_usuario.groups.add(Group.objects.get(name='Medico'))
        self.medico = Medico.objects.create(usuario=self.medico_usuario, especialidad=self.especialidad)

    def test_hu12_desactivar_medico_desactiva_usuario_y_bloquea_login(self):
        response = self.client.post(reverse('panel_medico_toggle', args=[self.medico.pk]))
        self.assertRedirects(response, reverse('panel_medicos'))

        self.medico.refresh_from_db()
        self.medico_usuario.refresh_from_db()
        self.assertFalse(self.medico.activo)
        self.assertFalse(self.medico_usuario.is_active)

        self.client.logout()
        self.assertFalse(self.client.login(username='4000000001', password='ClaveSegura123'))

    def test_hu12_no_se_puede_desactivar_medico_con_citas_confirmadas_futuras(self):
        paciente = crear_usuario('4000000002', self.tipo_documento)
        estado_confirmada = EstadoCita.objects.get(nombre='confirmada')
        Cita.objects.create(
            paciente=paciente, medico=self.medico, estado=estado_confirmada,
            fecha=timezone.localdate() + timedelta(days=3),
            hora_inicio='08:00', hora_fin='08:30',
        )

        response = self.client.post(reverse('panel_medico_toggle', args=[self.medico.pk]))
        self.assertRedirects(response, reverse('panel_medicos'))

        self.medico.refresh_from_db()
        self.medico_usuario.refresh_from_db()
        self.assertTrue(self.medico.activo)
        self.assertTrue(self.medico_usuario.is_active)

    def test_hu12_editar_datos_y_especialidad_del_medico(self):
        otra_especialidad = Especialidad.objects.get(nombre='Odontología')
        response = self.client.post(reverse('panel_medico_editar', args=[self.medico.pk]), {
            'nombre': 'Laura',
            'apellido': 'Diaz Actualizada',
            'correo': self.medico_usuario.email,
            'telefono': '3009999999',
            'especialidad': otra_especialidad.pk,
            'registro_medico': 'RM-999',
        })
        self.assertRedirects(response, reverse('panel_medicos'))

        self.medico_usuario.refresh_from_db()
        self.medico.refresh_from_db()
        self.assertEqual(self.medico_usuario.last_name, 'Diaz Actualizada')
        self.assertEqual(self.medico.especialidad, otra_especialidad)
        self.assertEqual(self.medico.registro_medico, 'RM-999')


class PanelMedicoHorarioTests(TestCase):
    """HU-14: horario recurrente del médico, gestionado desde el panel."""

    def setUp(self):
        self.tipo_documento = TipoDocumento.objects.get(nombre='Cédula de ciudadanía')
        self.especialidad = Especialidad.objects.get(nombre='Medicina general')
        self.admin = crear_usuario('1111111111', self.tipo_documento)
        self.admin.groups.add(Group.objects.get(name='Administrador'))
        self.client.login(username='1111111111', password='ClaveSegura123')

        medico_usuario = crear_usuario('6000000001', self.tipo_documento)
        self.medico = Medico.objects.create(usuario=medico_usuario, especialidad=self.especialidad)
        HorarioMedico.objects.create(medico=self.medico, dia_semana=0, hora_inicio='08:00', hora_fin='12:00')

    def test_hu14_agregar_bloque_solapado_muestra_error_de_form(self):
        url = reverse('panel_medico_horarios', args=[self.medico.pk])
        response = self.client.post(url, {'dia_semana': 0, 'hora_inicio': '09:00', 'hora_fin': '11:00'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.medico.horarios.count(), 1)
        self.assertContains(response, 'se solapa')

    def test_hu14_agregar_bloque_no_solapado_mismo_dia_funciona(self):
        url = reverse('panel_medico_horarios', args=[self.medico.pk])
        response = self.client.post(url, {'dia_semana': 0, 'hora_inicio': '14:00', 'hora_fin': '18:00'})
        self.assertRedirects(response, url)
        self.assertEqual(self.medico.horarios.count(), 2)

    def test_hu14_hora_fin_menor_o_igual_a_inicio_muestra_error(self):
        url = reverse('panel_medico_horarios', args=[self.medico.pk])
        response = self.client.post(url, {'dia_semana': 1, 'hora_inicio': '10:00', 'hora_fin': '10:00'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.medico.horarios.count(), 1)

    def test_hu14_ida_y_vuelta_con_los_campos_reales_del_html(self):
        url = reverse('panel_medico_horarios', args=[self.medico.pk])
        respuesta_get = self.client.get(url)
        html = respuesta_get.content.decode()
        datos = datos_formulario_reales(html)
        # dia_semana trae el placeholder en blanco por defecto (el campo del
        # modelo no tiene default); un admin real sí debe elegir un día.
        datos['dia_semana'] = opciones_reales_de_select(html, 'dia_semana')[0]
        datos.update({'hora_inicio': '14:00', 'hora_fin': '16:00'})

        respuesta_post = self.client.post(url, datos)
        self.assertRedirects(respuesta_post, url)
        self.assertEqual(self.medico.horarios.count(), 2)


@override_settings(DEBUG=True)
class SeedDevCommandTests(TestCase):
    def test_seed_dev_crea_admin_paciente_y_medico(self):
        call_command('seed_dev', stdout=StringIO())

        admin = Usuario.objects.get(username='9999999999')
        self.assertTrue(admin.is_superuser)
        self.assertTrue(admin.groups.filter(name='Administrador').exists())

        paciente = Usuario.objects.get(username='8888888888')
        self.assertTrue(paciente.groups.filter(name='Paciente').exists())

        medico_usuario = Usuario.objects.get(username='7777777777')
        self.assertTrue(medico_usuario.groups.filter(name='Medico').exists())
        self.assertTrue(Medico.objects.filter(usuario=medico_usuario).exists())

    def test_seed_dev_es_idempotente(self):
        call_command('seed_dev', stdout=StringIO())
        call_command('seed_dev', stdout=StringIO())

        self.assertEqual(Usuario.objects.filter(username='9999999999').count(), 1)
        self.assertEqual(Usuario.objects.filter(username='8888888888').count(), 1)
        self.assertEqual(Usuario.objects.filter(username='7777777777').count(), 1)

    def test_seed_dev_sin_extra_solo_crea_admin(self):
        call_command('seed_dev', '--sin-extra', stdout=StringIO())

        self.assertTrue(Usuario.objects.filter(username='9999999999').exists())
        self.assertFalse(Usuario.objects.filter(username='8888888888').exists())
        self.assertFalse(Usuario.objects.filter(username='7777777777').exists())

    @override_settings(DEBUG=False)
    def test_seed_dev_rechaza_ejecucion_si_debug_false(self):
        with self.assertRaises(CommandError):
            call_command('seed_dev', stdout=StringIO())
        self.assertFalse(Usuario.objects.filter(username='9999999999').exists())
