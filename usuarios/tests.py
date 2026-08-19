from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser, Group
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse

from datetime import timedelta

from django.utils import timezone

from citas.models import Cita, EstadoCita, Especialidad, HorarioMedico

from .context_processors import roles
from .forms import RegistroForm
from .htmlform_utils import datos_formulario_reales, opciones_reales_de_select
from .models import CambioUsuarioLog, Medico, TipoDocumento, Usuario


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


def crear_paciente(numero_documento, **kwargs):
    tipo_documento = TipoDocumento.objects.get(nombre='Cédula de Ciudadanía (CC)')
    usuario = crear_usuario(numero_documento, tipo_documento, **kwargs)
    usuario.groups.add(Group.objects.get(name='Paciente'))
    return usuario


def crear_admin(numero_documento):
    usuario = crear_paciente(numero_documento)
    usuario.groups.set([Group.objects.get(name='Administrador')])
    return usuario


def crear_medico(numero_documento, especialidad):
    usuario = crear_paciente(numero_documento)
    usuario.groups.set([Group.objects.get(name='Medico')])
    return Medico.objects.create(usuario=usuario, especialidad=especialidad)


class RegistroTests(TestCase):
    def setUp(self):
        self.tipo_documento = TipoDocumento.objects.get(nombre='Cédula de Ciudadanía (CC)')

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
        # setdefault (no update): si el caller pasa confirmar_numero_documento
        # explícito (para simular un desajuste), no lo pisamos con el mismo
        # valor de numero_documento ya resuelto arriba.
        datos.setdefault('confirmar_numero_documento', datos['numero_documento'])
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
            f'<option value="{self.tipo_documento.pk}" data-codigo="CC">Cédula de Ciudadanía (CC)</option>',
            html=True,
        )

    def test_select_tipo_documento_incluye_data_codigo_por_opcion(self):
        response = self.client.get(reverse('registro'))
        for tipo in TipoDocumento.objects.all():
            self.assertContains(
                response,
                f'<option value="{tipo.pk}" data-codigo="{tipo.codigo}">{tipo.nombre}</option>',
                html=True,
            )

    def test_data_codigo_no_cambia_si_se_renombra_el_tipo_documento(self):
        tipo = TipoDocumento.objects.get(codigo='CC')
        tipo.nombre = 'Cédula (nombre cambiado desde el admin)'
        tipo.save(update_fields=['nombre'])

        response = self.client.get(reverse('registro'))
        self.assertContains(
            response,
            f'<option value="{tipo.pk}" data-codigo="CC">{tipo.nombre}</option>',
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
            'confirmar_numero_documento': '1000777777',
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

    def test_confirmacion_de_cedula_que_no_coincide_no_crea_usuario_y_repuebla(self):
        datos = self.datos_registro(
            numero_documento='1000444444', confirmar_numero_documento='1000444445',
            correo='mismatch@example.com',
        )
        response = self.client.post(reverse('registro'), datos)

        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context['form'], 'confirmar_numero_documento',
            'El número de documento no coincide con el ingresado arriba.',
        )
        self.assertFalse(Usuario.objects.filter(numero_documento='1000444444').exists())
        self.assertContains(response, 'value="1000444444"')
        self.assertContains(response, 'value="1000444445"')

    def test_confirmacion_de_cedula_que_coincide_crea_el_usuario(self):
        datos = self.datos_registro(numero_documento='1000555444', correo='coincide@example.com')
        response = self.client.post(reverse('registro'), datos)

        self.assertRedirects(response, reverse('login'))
        usuario = Usuario.objects.get(numero_documento='1000555444')
        self.assertTrue(usuario.groups.filter(name='Paciente').exists())

    def test_confirmacion_de_cedula_no_se_persiste_en_el_modelo(self):
        nombres_de_campos = [campo.name for campo in Usuario._meta.get_fields()]
        self.assertNotIn('confirmar_numero_documento', nombres_de_campos)


class ValidacionNumeroDocumentoTests(TestCase):
    """La validación del número de documento depende del código del catálogo
    (CC/TI/CE/PPT/PA), no del nombre visible: renombrar un tipo desde el
    admin no debe desactivar su regla, y un tipo sin código conocido cae al
    genérico (solo dígitos, 5-16 caracteres)."""

    def datos_base(self, tipo_documento, numero_documento):
        return {
            'nombre': 'Ana', 'apellido': 'Gómez', 'fecha_nacimiento': '1990-05-10',
            'tipo_documento': tipo_documento.pk, 'numero_documento': numero_documento,
            'confirmar_numero_documento': numero_documento,
            'correo': f'{numero_documento.lower()}@example.com', 'telefono': '3001234567',
            'password1': 'ClaveSegura123', 'password2': 'ClaveSegura123',
        }

    def test_cc_exige_solo_digitos_entre_5_y_10(self):
        tipo = TipoDocumento.objects.get(codigo='CC')

        self.assertTrue(RegistroForm(data=self.datos_base(tipo, '123456')).is_valid())

        form_invalido = RegistroForm(data=self.datos_base(tipo, '12AB'))
        self.assertFalse(form_invalido.is_valid())
        self.assertIn('numero_documento', form_invalido.errors)

    def test_ti_exige_solo_digitos_entre_10_y_11(self):
        tipo = TipoDocumento.objects.get(codigo='TI')

        self.assertTrue(RegistroForm(data=self.datos_base(tipo, '1234567890')).is_valid())

        form_invalido = RegistroForm(data=self.datos_base(tipo, '123'))
        self.assertFalse(form_invalido.is_valid())
        self.assertIn('numero_documento', form_invalido.errors)

    def test_pasaporte_exige_alfanumerico_y_lo_deja_en_mayusculas(self):
        tipo = TipoDocumento.objects.get(codigo='PA')

        form = RegistroForm(data=self.datos_base(tipo, 'ab1234cd'))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['numero_documento'], 'AB1234CD')

    def test_renombrar_el_tipo_documento_no_afecta_la_validacion(self):
        tipo = TipoDocumento.objects.get(codigo='CC')
        tipo.nombre = 'Cédula (nombre cambiado desde el admin)'
        tipo.save(update_fields=['nombre'])

        form_invalido = RegistroForm(data=self.datos_base(tipo, '12'))
        self.assertFalse(form_invalido.is_valid())
        self.assertIn('numero_documento', form_invalido.errors)

        form_valido = RegistroForm(data=self.datos_base(tipo, '1234567'))
        self.assertTrue(form_valido.is_valid(), form_valido.errors)

    def test_tipo_sin_regla_especifica_usa_la_validacion_generica(self):
        tipo_generico = TipoDocumento.objects.create(nombre='Otro documento', codigo='XX')

        self.assertTrue(RegistroForm(data=self.datos_base(tipo_generico, '12345')).is_valid())

        form_invalido = RegistroForm(data=self.datos_base(tipo_generico, 'ABCD'))
        self.assertFalse(form_invalido.is_valid())
        self.assertIn('numero_documento', form_invalido.errors)


class NumeroDocumentoUnicidadGlobalTests(TestCase):
    """Fase 6c.1, decisión 1: numero_documento pasa a ser único globalmente
    (uq_usuario_numero_documento), no solo en pareja con tipo_documento. El
    username (la cédula) ya lo exigía de hecho — esta constraint hace que el
    modelo declare la verdad, no cambia el comportamiento real."""

    def setUp(self):
        self.cc = TipoDocumento.objects.get(codigo='CC')
        self.ti = TipoDocumento.objects.get(codigo='TI')

    def test_dos_usuarios_con_mismo_numero_y_distinto_tipo_documento_falla(self):
        """Documenta la limitación aceptada de la decisión 1 — un colombiano
        con CC 1234 y un extranjero con CE 1234 no pueden coexistir — para
        que quede explicado por qué existía si algún día alguien relaja esta
        constraint de vuelta a la pareja (tipo, número)."""
        crear_usuario('6600000001', self.cc)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                crear_usuario(
                    '6600000001', self.ti, username='6600000001-otro-username',
                    email='otro-6600000001@example.com',
                )

    def test_registro_publico_rechaza_numero_ya_usado_con_tipo_distinto(self):
        crear_usuario('6600000002', self.cc)
        datos = {
            'nombre': 'Ana', 'apellido': 'Gómez', 'fecha_nacimiento': '1990-05-10',
            'tipo_documento': self.ti.pk, 'numero_documento': '6600000002',
            'confirmar_numero_documento': '6600000002',
            'correo': 'nueva-6600000002@example.com', 'telefono': '',
            'password1': 'ClaveSegura123', 'password2': 'ClaveSegura123',
        }
        response = self.client.post(reverse('registro'), datos)
        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context['form'], 'numero_documento', 'Ya existe una cuenta con este número de documento.',
        )
        self.assertEqual(Usuario.objects.filter(numero_documento='6600000002').count(), 1)

    def test_registro_publico_sigue_funcionando_para_un_numero_nuevo(self):
        datos = {
            'nombre': 'Carla', 'apellido': 'Ruiz', 'fecha_nacimiento': '1990-05-10',
            'tipo_documento': self.cc.pk, 'numero_documento': '6600000003',
            'confirmar_numero_documento': '6600000003',
            'correo': 'carla.ruiz.6600000003@example.com', 'telefono': '',
            'password1': 'ClaveSegura123', 'password2': 'ClaveSegura123',
        }
        response = self.client.post(reverse('registro'), datos)
        self.assertRedirects(response, reverse('login'))
        self.assertTrue(Usuario.objects.filter(numero_documento='6600000003').exists())

    def test_alta_de_medico_rechaza_numero_ya_usado_con_tipo_distinto(self):
        crear_usuario('6600000004', self.cc)
        crear_admin('6600000005')
        self.client.login(username='6600000005', password='ClaveSegura123')
        datos = {
            'nombre': 'Marcos', 'apellido': 'Lima', 'fecha_nacimiento': '1980-03-15',
            'tipo_documento': self.ti.pk, 'numero_documento': '6600000004',
            'correo': 'marcos.lima.6600000004@example.com', 'telefono': '',
            'especialidad': Especialidad.objects.get(nombre='Medicina general').pk,
            'registro_medico': '', 'password1': 'ClaveSegura123', 'password2': 'ClaveSegura123',
        }
        response = self.client.post(reverse('panel_medico_nuevo'), datos)
        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context['form'], 'numero_documento', 'Ya existe una cuenta con este número de documento.',
        )
        self.assertEqual(Usuario.objects.filter(numero_documento='6600000004').count(), 1)
        self.assertFalse(Medico.objects.filter(usuario__numero_documento='6600000004').exists())

    def test_alta_de_medico_sigue_funcionando_para_un_numero_nuevo(self):
        crear_admin('6600000006')
        self.client.login(username='6600000006', password='ClaveSegura123')
        datos = {
            'nombre': 'Lucía', 'apellido': 'Ortiz', 'fecha_nacimiento': '1980-03-15',
            'tipo_documento': self.cc.pk, 'numero_documento': '6600000007',
            'correo': 'lucia.ortiz.6600000007@example.com', 'telefono': '',
            'especialidad': Especialidad.objects.get(nombre='Medicina general').pk,
            'registro_medico': '', 'password1': 'ClaveSegura123', 'password2': 'ClaveSegura123',
        }
        response = self.client.post(reverse('panel_medico_nuevo'), datos)
        self.assertRedirects(response, reverse('panel_medicos'))
        self.assertTrue(Medico.objects.filter(usuario__numero_documento='6600000007').exists())


class LoginTests(TestCase):
    def setUp(self):
        self.tipo_documento = TipoDocumento.objects.get(nombre='Cédula de Ciudadanía (CC)')
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
        # 'inicio' es LOGIN_REDIRECT_URL, pero a su vez reenvía por rol (ver
        # RedireccionPorRolTests): no seguimos esa segunda redirección aquí,
        # esta prueba solo cubre que el login apunte a 'inicio'.
        self.assertRedirects(response, reverse('inicio'), fetch_redirect_response=False)

    def test_login_ida_y_vuelta_con_los_campos_reales_del_html(self):
        respuesta_get = self.client.get(reverse('login'))
        html = respuesta_get.content.decode()
        datos = datos_formulario_reales(html)
        datos.update({
            'username': '1000123456',
            'password': 'ClaveSegura123',
        })

        respuesta_post = self.client.post(reverse('login'), datos)
        self.assertRedirects(respuesta_post, reverse('inicio'), fetch_redirect_response=False)


class InicioViewTests(TestCase):
    def test_usuario_anonimo_es_redirigido_a_login(self):
        response = self.client.get(reverse('inicio'))
        self.assertRedirects(response, f"{reverse('login')}?next={reverse('inicio')}")


class RedireccionPorRolTests(TestCase):
    """Fase 5, tarea 4: 'inicio' es la única fuente de la redirección por
    rol tras el login — admin al panel, médico a su agenda, paciente a sus
    citas."""

    def setUp(self):
        self.tipo_documento = TipoDocumento.objects.get(nombre='Cédula de Ciudadanía (CC)')
        self.especialidad = Especialidad.objects.get(nombre='Medicina general')

    def test_admin_es_redirigido_al_panel(self):
        admin = crear_usuario('5100000001', self.tipo_documento)
        admin.groups.add(Group.objects.get(name='Administrador'))
        self.client.login(username='5100000001', password='ClaveSegura123')

        response = self.client.get(reverse('inicio'))
        self.assertRedirects(response, reverse('panel_home'))

    def test_medico_es_redirigido_a_su_agenda(self):
        medico_usuario = crear_usuario('5100000002', self.tipo_documento)
        medico_usuario.groups.add(Group.objects.get(name='Medico'))
        Medico.objects.create(usuario=medico_usuario, especialidad=self.especialidad)
        self.client.login(username='5100000002', password='ClaveSegura123')

        response = self.client.get(reverse('inicio'))
        self.assertRedirects(response, reverse('medico_agenda'))

    def test_paciente_es_redirigido_a_mis_citas(self):
        paciente = crear_usuario('5100000003', self.tipo_documento)
        paciente.groups.add(Group.objects.get(name='Paciente'))
        self.client.login(username='5100000003', password='ClaveSegura123')

        response = self.client.get(reverse('inicio'))
        self.assertRedirects(response, reverse('mis_citas'))


class UsuarioManagerTests(TestCase):
    def test_create_superuser_resuelve_tipo_documento_pasado_como_string(self):
        tipo_documento = TipoDocumento.objects.get(nombre='Cédula de Ciudadanía (CC)')

        usuario = Usuario.objects.create_superuser(
            username='999888777',
            email='admin@mediclick.test',
            password='AdminSeguro123',
            tipo_documento=str(tipo_documento.pk),
            fecha_nacimiento='1990-01-01',
        )

        self.assertEqual(usuario.tipo_documento, tipo_documento)
        self.assertTrue(usuario.is_superuser)
        self.assertTrue(usuario.is_staff)


class RolesContextProcessorTests(TestCase):
    """El context processor de roles es la única fuente de es_admin/es_medico/es_paciente."""

    def setUp(self):
        self.factory = RequestFactory()
        self.tipo_documento = TipoDocumento.objects.get(nombre='Cédula de Ciudadanía (CC)')

    def test_usuario_anonimo_no_tiene_ningun_rol(self):
        request = self.factory.get('/')
        request.user = AnonymousUser()

        self.assertEqual(
            roles(request),
            {'es_admin': False, 'es_medico': False, 'es_paciente': False},
        )

    def test_admin_tiene_solo_es_admin(self):
        admin = crear_usuario('3100000001', self.tipo_documento)
        admin.groups.add(Group.objects.get(name='Administrador'))
        request = self.factory.get('/')
        request.user = admin

        self.assertEqual(
            roles(request),
            {'es_admin': True, 'es_medico': False, 'es_paciente': False},
        )

    def test_medico_tiene_solo_es_medico(self):
        medico = crear_usuario('3100000002', self.tipo_documento)
        medico.groups.add(Group.objects.get(name='Medico'))
        request = self.factory.get('/')
        request.user = medico

        self.assertEqual(
            roles(request),
            {'es_admin': False, 'es_medico': True, 'es_paciente': False},
        )

    def test_paciente_tiene_solo_es_paciente(self):
        paciente = crear_usuario('3100000003', self.tipo_documento)
        paciente.groups.add(Group.objects.get(name='Paciente'))
        request = self.factory.get('/')
        request.user = paciente

        self.assertEqual(
            roles(request),
            {'es_admin': False, 'es_medico': False, 'es_paciente': True},
        )


class NavegacionMovilTests(TestCase):
    """El menú móvil (details/summary en base.html) debe traer, dentro de su
    propio bloque, los mismos enlaces de navegación por rol y las acciones
    de sesión que ya existen en la barra de escritorio."""

    def setUp(self):
        self.tipo_documento = TipoDocumento.objects.get(nombre='Cédula de Ciudadanía (CC)')
        self.admin = crear_usuario('4100000001', self.tipo_documento)
        self.admin.groups.add(Group.objects.get(name='Administrador'))
        self.paciente = crear_usuario('4100000002', self.tipo_documento)
        self.paciente.groups.add(Group.objects.get(name='Paciente'))

    def fragmento_menu_movil(self, response):
        html = response.content.decode()
        inicio = html.index('<details')
        fin = html.index('</details>', inicio) + len('</details>')
        return html[inicio:fin]

    def test_admin_ve_panel_especialidades_y_medicos_en_el_menu_movil(self):
        self.client.login(username='4100000001', password='ClaveSegura123')
        response = self.client.get(reverse('panel_home'))
        menu_movil = self.fragmento_menu_movil(response)

        self.assertIn(reverse('panel_especialidades'), menu_movil)
        self.assertIn(reverse('panel_medicos'), menu_movil)
        self.assertIn('Cerrar sesión', menu_movil)

    def test_paciente_ve_agendar_y_cerrar_sesion_en_el_menu_movil_pero_no_el_panel(self):
        self.client.login(username='4100000002', password='ClaveSegura123')
        response = self.client.get(reverse('mis_citas'))
        menu_movil = self.fragmento_menu_movil(response)

        self.assertIn(reverse('agendar_especialidades'), menu_movil)
        self.assertIn('Cerrar sesión', menu_movil)
        self.assertNotIn(reverse('panel_especialidades'), menu_movil)
        self.assertNotIn(reverse('panel_medicos'), menu_movil)

    def test_anonimo_ve_iniciar_sesion_en_el_menu_movil(self):
        # Todas las rutas de la app exigen login, así que no hay una URL real
        # donde un anónimo vea el header; renderizamos base.html con un
        # request anónimo (como haría cualquier página futura sin @login_required)
        # para verificar el contrato del menú móvil en ese caso.
        factory = RequestFactory()
        request = factory.get('/agendar/')
        request.user = AnonymousUser()
        request.resolver_match = resolve('/agendar/')

        html = render_to_string('base.html', {}, request=request)
        inicio = html.index('<details')
        menu_movil = html[inicio:html.index('</details>', inicio) + len('</details>')]

        self.assertIn(reverse('login'), menu_movil)
        self.assertIn('Iniciar sesión', menu_movil)
        self.assertNotIn('Cerrar sesión', menu_movil)


class NavegacionFase6bTests(TestCase):
    """Tarea 8: enlaces nuevos por rol (perfil para los tres; calendario e
    historial para el médico; calendario para el paciente), en escritorio y
    en el menú móvil, usando la misma fuente de roles que ya existía
    (es_admin/es_medico/es_paciente)."""

    def setUp(self):
        self.tipo_documento = TipoDocumento.objects.get(nombre='Cédula de Ciudadanía (CC)')
        self.especialidad = Especialidad.objects.get(nombre='Medicina general')

    def fragmento_menu_movil(self, response):
        html = response.content.decode()
        inicio = html.index('<details')
        return html[inicio:html.index('</details>', inicio) + len('</details>')]

    def test_admin_ve_perfil_en_escritorio_y_movil(self):
        admin = crear_usuario('4300000001', self.tipo_documento)
        admin.groups.add(Group.objects.get(name='Administrador'))
        self.client.login(username='4300000001', password='ClaveSegura123')

        response = self.client.get(reverse('panel_home'))
        html = response.content.decode()

        self.assertIn(reverse('perfil'), html)
        self.assertIn(reverse('perfil'), self.fragmento_menu_movil(response))

    def test_medico_ve_calendario_historial_y_perfil_en_escritorio_y_movil(self):
        medico_usuario = crear_usuario('4300000002', self.tipo_documento)
        medico_usuario.groups.add(Group.objects.get(name='Medico'))
        Medico.objects.create(usuario=medico_usuario, especialidad=self.especialidad)
        self.client.login(username='4300000002', password='ClaveSegura123')

        response = self.client.get(reverse('medico_agenda'))
        html = response.content.decode()
        menu_movil = self.fragmento_menu_movil(response)

        for url in (reverse('medico_calendario'), reverse('medico_historial'), reverse('perfil')):
            self.assertIn(url, html)
            self.assertIn(url, menu_movil)

    def test_paciente_ve_calendario_y_perfil_en_escritorio_y_movil(self):
        paciente = crear_usuario('4300000003', self.tipo_documento)
        paciente.groups.add(Group.objects.get(name='Paciente'))
        self.client.login(username='4300000003', password='ClaveSegura123')

        response = self.client.get(reverse('mis_citas'))
        html = response.content.decode()
        menu_movil = self.fragmento_menu_movil(response)

        for url in (reverse('mis_citas_calendario'), reverse('perfil')):
            self.assertIn(url, html)
            self.assertIn(url, menu_movil)


class NombreUsuarioEnBarraTests(TestCase):
    """La barra superior debe mostrar el nombre del usuario autenticado
    (nombre y apellido si existen, usuario como respaldo), en escritorio y
    en el menú móvil, para los tres roles."""

    def setUp(self):
        self.tipo_documento = TipoDocumento.objects.get(nombre='Cédula de Ciudadanía (CC)')

    def fragmento_menu_movil(self, response):
        html = response.content.decode()
        inicio = html.index('<details')
        return html[inicio:html.index('</details>', inicio) + len('</details>')]

    def test_admin_ve_su_nombre_en_la_barra(self):
        admin = crear_usuario(
            '4200000001', self.tipo_documento, first_name='Marta', last_name='Osorio',
        )
        admin.groups.add(Group.objects.get(name='Administrador'))
        self.client.login(username='4200000001', password='ClaveSegura123')

        response = self.client.get(reverse('panel_home'))

        self.assertContains(response, 'Marta Osorio')
        self.assertIn('Marta Osorio', self.fragmento_menu_movil(response))

    def test_medico_ve_su_nombre_en_la_barra(self):
        medico_usuario = crear_usuario(
            '4200000002', self.tipo_documento, first_name='Carlos', last_name='Peña',
        )
        medico_usuario.groups.add(Group.objects.get(name='Medico'))
        especialidad = Especialidad.objects.get(nombre='Medicina general')
        Medico.objects.create(usuario=medico_usuario, especialidad=especialidad)
        self.client.login(username='4200000002', password='ClaveSegura123')

        response = self.client.get(reverse('medico_agenda'))

        self.assertContains(response, 'Carlos Peña')
        self.assertIn('Carlos Peña', self.fragmento_menu_movil(response))

    def test_paciente_ve_su_nombre_en_la_barra(self):
        paciente = crear_usuario(
            '4200000003', self.tipo_documento, first_name='Diana', last_name='Ríos',
        )
        paciente.groups.add(Group.objects.get(name='Paciente'))
        self.client.login(username='4200000003', password='ClaveSegura123')

        response = self.client.get(reverse('mis_citas'))

        self.assertContains(response, 'Diana Ríos')
        self.assertIn('Diana Ríos', self.fragmento_menu_movil(response))

    def test_usuario_sin_nombre_ni_apellido_muestra_el_username_como_respaldo(self):
        paciente = crear_usuario(
            '4200000004', self.tipo_documento, first_name='', last_name='',
        )
        paciente.groups.add(Group.objects.get(name='Paciente'))
        self.client.login(username='4200000004', password='ClaveSegura123')

        response = self.client.get(reverse('mis_citas'))

        self.assertContains(response, '4200000004')


class PanelAccesoTests(TestCase):
    """HU-10 y seguridad del panel: solo Administrador entra, nunca 200 para otros."""

    def setUp(self):
        self.tipo_documento = TipoDocumento.objects.get(nombre='Cédula de Ciudadanía (CC)')
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
        self.assertRedirects(response, reverse('mis_citas'))

    def test_paciente_recibe_pagina_403_personalizada(self):
        self.client.login(username='2222222222', password='ClaveSegura123')
        response = self.client.get(reverse('panel_home'))
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, 'No tienes permisos de administrador', status_code=403)

    def test_admin_accede_al_panel(self):
        self.client.login(username='1111111111', password='ClaveSegura123')
        response = self.client.get(reverse('panel_home'))
        self.assertEqual(response.status_code, 200)

    def test_admin_ve_los_enlaces_de_especialidades_y_medicos_en_la_navegacion(self):
        self.client.login(username='1111111111', password='ClaveSegura123')
        response = self.client.get(reverse('panel_home'))
        self.assertContains(response, reverse('panel_especialidades'))
        self.assertContains(response, reverse('panel_medicos'))

    def test_paciente_no_ve_los_enlaces_del_panel_pero_si_el_de_agendar(self):
        self.client.login(username='2222222222', password='ClaveSegura123')
        response = self.client.get(reverse('mis_citas'))
        self.assertNotContains(response, reverse('panel_especialidades'))
        self.assertNotContains(response, reverse('panel_medicos'))
        self.assertContains(response, reverse('agendar_especialidades'))

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
        self.tipo_documento = TipoDocumento.objects.get(nombre='Cédula de Ciudadanía (CC)')
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
        self.tipo_documento = TipoDocumento.objects.get(nombre='Cédula de Ciudadanía (CC)')
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

    def test_no_se_puede_reactivar_medico_con_especialidad_inactiva(self):
        """Decisión 8 (fase 6a.1): antes de esta corrección un médico podía
        reactivarse aunque su especialidad siguiera inactiva, dejando su
        usuario con is_active=True para una especialidad que ya no se
        ofrece."""
        self.medico.activo = False
        self.medico.save(update_fields=['activo'])
        self.medico_usuario.is_active = False
        self.medico_usuario.save(update_fields=['is_active'])
        self.especialidad.activa = False
        self.especialidad.save(update_fields=['activa'])

        response = self.client.post(reverse('panel_medico_toggle', args=[self.medico.pk]))
        self.assertRedirects(response, reverse('panel_medicos'))

        self.medico.refresh_from_db()
        self.medico_usuario.refresh_from_db()
        self.assertFalse(self.medico.activo)
        self.assertFalse(self.medico_usuario.is_active)

    def test_se_puede_reactivar_medico_tras_reactivar_su_especialidad(self):
        self.medico.activo = False
        self.medico.save(update_fields=['activo'])
        self.medico_usuario.is_active = False
        self.medico_usuario.save(update_fields=['is_active'])
        self.especialidad.activa = False
        self.especialidad.save(update_fields=['activa'])

        self.especialidad.activa = True
        self.especialidad.save(update_fields=['activa'])

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
            'fecha_nacimiento': '1985-06-15',
            'tipo_documento': self.tipo_documento.pk,
            'especialidad': otra_especialidad.pk,
            'registro_medico': 'RM-999',
        })
        self.assertRedirects(response, reverse('panel_medicos'))

        self.medico_usuario.refresh_from_db()
        self.medico.refresh_from_db()
        self.assertEqual(self.medico_usuario.last_name, 'Diaz Actualizada')
        self.assertEqual(self.medico.especialidad, otra_especialidad)
        self.assertEqual(self.medico.registro_medico, 'RM-999')


class PanelMedicoDocumentoTests(TestCase):
    """Fase 7, decisión 4: tipo de documento editable en el formulario de
    médicos, número de documento visible en solo lectura — nunca un campo
    del formulario, así que ningún POST puede tocarlo."""

    def setUp(self):
        self.cc = TipoDocumento.objects.get(codigo='CC')
        self.ti = TipoDocumento.objects.get(codigo='TI')
        self.especialidad = Especialidad.objects.get(nombre='Medicina general')
        self.admin = crear_usuario('1111111112', self.cc)
        self.admin.groups.add(Group.objects.get(name='Administrador'))
        self.client.login(username='1111111112', password='ClaveSegura123')

        self.medico_usuario = crear_usuario(
            '4000000010', self.cc, first_name='Laura', last_name='Diaz',
        )
        self.medico_usuario.groups.add(Group.objects.get(name='Medico'))
        self.medico = Medico.objects.create(usuario=self.medico_usuario, especialidad=self.especialidad)

    def datos_post(self, **overrides):
        datos = {
            'nombre': 'Laura', 'apellido': 'Diaz', 'correo': self.medico_usuario.email,
            'telefono': '3001234567', 'fecha_nacimiento': '1985-06-15', 'tipo_documento': self.ti.pk,
            'especialidad': self.especialidad.pk, 'registro_medico': '',
        }
        datos.update(overrides)
        return datos

    def test_el_numero_de_documento_se_muestra_en_solo_lectura(self):
        response = self.client.get(reverse('panel_medico_editar', args=[self.medico.pk]))
        self.assertContains(response, self.medico_usuario.numero_documento)
        self.assertNotContains(response, 'name="numero_documento"')

    def test_editar_el_tipo_de_documento_lo_cambia(self):
        response = self.client.post(reverse('panel_medico_editar', args=[self.medico.pk]), self.datos_post())
        self.assertRedirects(response, reverse('panel_medicos'))

        self.medico_usuario.refresh_from_db()
        self.assertEqual(self.medico_usuario.tipo_documento, self.ti)

    def test_un_post_con_numero_de_documento_no_lo_cambia(self):
        numero_original = self.medico_usuario.numero_documento
        self.client.post(
            reverse('panel_medico_editar', args=[self.medico.pk]),
            self.datos_post(numero_documento='999999999'),
        )

        self.medico_usuario.refresh_from_db()
        self.assertEqual(self.medico_usuario.numero_documento, numero_original)

    def test_el_username_no_cambia_al_editar_el_tipo_de_documento(self):
        username_original = self.medico_usuario.username
        self.client.post(reverse('panel_medico_editar', args=[self.medico.pk]), self.datos_post())

        self.medico_usuario.refresh_from_db()
        self.assertEqual(self.medico_usuario.username, username_original)
        self.assertEqual(self.medico_usuario.username, self.medico_usuario.numero_documento)

    def test_ida_y_vuelta_con_los_campos_reales_del_html(self):
        respuesta_get = self.client.get(reverse('panel_medico_editar', args=[self.medico.pk]))
        html = respuesta_get.content.decode()
        datos = datos_formulario_reales(html)
        datos['tipo_documento'] = self.ti.pk
        datos['telefono'] = '3007778899'

        respuesta_post = self.client.post(reverse('panel_medico_editar', args=[self.medico.pk]), datos)
        self.assertRedirects(respuesta_post, reverse('panel_medicos'))

        self.medico_usuario.refresh_from_db()
        self.assertEqual(self.medico_usuario.tipo_documento, self.ti)
        self.assertEqual(self.medico_usuario.telefono, '3007778899')

    def test_editar_el_correo_del_medico_genera_una_entrada_de_auditoria(self):
        self.client.post(
            reverse('panel_medico_editar', args=[self.medico.pk]),
            self.datos_post(correo='nuevo-correo-medico@example.com'),
        )

        cambio = CambioUsuarioLog.objects.get(usuario=self.medico_usuario, campo='email')
        self.assertEqual(cambio.valor_anterior, self.medico_usuario.email)
        self.assertEqual(cambio.valor_nuevo, 'nuevo-correo-medico@example.com')
        self.assertEqual(cambio.realizado_por, self.admin)

    def test_ninguna_entrada_de_numero_de_documento_se_genera_al_editar_medico(self):
        self.client.post(
            reverse('panel_medico_editar', args=[self.medico.pk]),
            self.datos_post(numero_documento='999999999', tipo_documento=self.ti.pk),
        )
        self.assertFalse(CambioUsuarioLog.objects.filter(campo='numero_documento').exists())
        self.assertFalse(CambioUsuarioLog.objects.filter(campo='username').exists())

    def test_cambiar_el_tipo_se_rechaza_si_el_numero_no_corresponde(self):
        """El médico de setUp tiene numero_documento '4000000010' (10
        dígitos) — válido para CC/TI, inválido para CE (6-8 dígitos)."""
        ce = TipoDocumento.objects.get(codigo='CE')
        response = self.client.post(
            reverse('panel_medico_editar', args=[self.medico.pk]),
            self.datos_post(tipo_documento=ce.pk),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context['form'], 'tipo_documento',
            'La Cédula de Extranjería debe tener entre 6 y 8 dígitos. El número de documento no se '
            'puede editar: si no corresponde a este tipo, hay que crear la cuenta de nuevo.',
        )
        self.medico_usuario.refresh_from_db()
        self.assertEqual(self.medico_usuario.tipo_documento, self.cc)
        self.assertFalse(CambioUsuarioLog.objects.filter(usuario=self.medico_usuario, campo='tipo_documento').exists())

    def test_cambiar_el_tipo_funciona_si_el_numero_si_corresponde_y_queda_auditado(self):
        response = self.client.post(reverse('panel_medico_editar', args=[self.medico.pk]), self.datos_post())
        self.assertRedirects(response, reverse('panel_medicos'))

        self.medico_usuario.refresh_from_db()
        self.assertEqual(self.medico_usuario.tipo_documento, self.ti)
        cambio = CambioUsuarioLog.objects.get(usuario=self.medico_usuario, campo='tipo_documento')
        self.assertEqual(cambio.valor_anterior, self.cc.nombre)
        self.assertEqual(cambio.valor_nuevo, self.ti.nombre)
        self.assertEqual(cambio.realizado_por, self.admin)

    def test_editar_la_fecha_de_nacimiento_del_medico_genera_una_entrada_de_auditoria(self):
        response = self.client.post(
            reverse('panel_medico_editar', args=[self.medico.pk]),
            self.datos_post(fecha_nacimiento='1979-11-20'),
        )
        self.assertRedirects(response, reverse('panel_medicos'))

        self.medico_usuario.refresh_from_db()
        self.assertEqual(str(self.medico_usuario.fecha_nacimiento), '1979-11-20')
        cambio = CambioUsuarioLog.objects.get(usuario=self.medico_usuario, campo='fecha_nacimiento')
        self.assertEqual(cambio.valor_nuevo, '1979-11-20')
        self.assertEqual(cambio.realizado_por, self.admin)


class PanelMedicoHorarioTests(TestCase):
    """HU-14: horario recurrente del médico, gestionado desde el panel."""

    def setUp(self):
        self.tipo_documento = TipoDocumento.objects.get(nombre='Cédula de Ciudadanía (CC)')
        self.especialidad = Especialidad.objects.get(nombre='Medicina general')
        self.admin = crear_usuario('1111111111', self.tipo_documento)
        self.admin.groups.add(Group.objects.get(name='Administrador'))
        self.client.login(username='1111111111', password='ClaveSegura123')

        medico_usuario = crear_usuario('6000000001', self.tipo_documento)
        self.medico = Medico.objects.create(usuario=medico_usuario, especialidad=self.especialidad)
        HorarioMedico.objects.create(medico=self.medico, dia_semana=0, hora_inicio='08:00', hora_fin='12:00')

    def test_hu14_tabla_muestra_el_nombre_del_dia(self):
        """Fase 6b.2, tarea 1: la plantilla leía `horario.dia`, que no existe
        en el modelo (el campo es `dia_semana`) — Django resuelve un atributo
        inexistente como cadena vacía en vez de fallar, así que la columna
        quedaba en blanco sin que ningún test lo notara."""
        response = self.client.get(reverse('panel_medico_horarios', args=[self.medico.pk]))
        self.assertContains(response, 'Lunes')

    def test_hu14_agregar_bloque_solapado_muestra_error_de_form(self):
        url = reverse('panel_medico_horarios', args=[self.medico.pk])
        response = self.client.post(url, {'dia_semana': 0, 'hora_inicio': '09:00', 'hora_fin': '11:00'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.medico.horarios.count(), 1)
        self.assertContains(response, 'se solapa')

    def test_hu14_mensaje_de_solapamiento_aparece_una_sola_vez(self):
        """Fase 6b.2, tarea 2: HorarioMedicoForm.clean() llamaba a
        instance.clean() a mano Y ModelForm._post_clean() lo vuelve a llamar
        automáticamente vía instance.full_clean() — la misma ValidationError
        se agregaba dos veces al form."""
        url = reverse('panel_medico_horarios', args=[self.medico.pk])
        response = self.client.post(url, {'dia_semana': 0, 'hora_inicio': '09:00', 'hora_fin': '11:00'})
        self.assertEqual(response.content.decode().count('se solapa'), 1)

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

    def test_hu14_tabla_ordena_por_dia_y_hora_inicio(self):
        """Fase 6b.2, tarea 3: verificado que Meta.ordering ya produce este
        orden (ver reporte); este test lo deja como regresión explícita, con
        un fixture creado deliberadamente en desorden."""
        HorarioMedico.objects.create(medico=self.medico, dia_semana=3, hora_inicio='08:00', hora_fin='10:00')
        HorarioMedico.objects.create(medico=self.medico, dia_semana=0, hora_inicio='14:00', hora_fin='16:00')
        HorarioMedico.objects.create(medico=self.medico, dia_semana=4, hora_inicio='08:00', hora_fin='10:00')
        HorarioMedico.objects.create(medico=self.medico, dia_semana=1, hora_inicio='08:00', hora_fin='10:00')
        HorarioMedico.objects.create(medico=self.medico, dia_semana=2, hora_inicio='08:00', hora_fin='10:00')

        response = self.client.get(reverse('panel_medico_horarios', args=[self.medico.pk]))
        pares = [(h.dia_semana, h.hora_inicio) for h in response.context['horarios']]
        self.assertEqual(pares, sorted(pares))
        self.assertEqual(len(pares), 6)


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

    def test_seed_dev_crea_un_medico_por_especialidad_y_un_segundo_paciente(self):
        call_command('seed_dev', stdout=StringIO())

        segundo_paciente = Usuario.objects.get(username='8888888880')
        self.assertTrue(segundo_paciente.groups.filter(name='Paciente').exists())

        for cedula, nombre_especialidad in (
            ('7777777777', 'Medicina general'),
            ('7777777771', 'Odontología'),
            ('7777777772', 'Pediatría'),
        ):
            medico_usuario = Usuario.objects.get(username=cedula)
            medico = Medico.objects.get(usuario=medico_usuario)
            self.assertEqual(medico.especialidad.nombre, nombre_especialidad)

    def test_seed_dev_crea_horario_de_lunes_a_viernes_para_cada_medico(self):
        call_command('seed_dev', stdout=StringIO())

        for cedula in ('7777777777', '7777777771', '7777777772'):
            medico = Medico.objects.get(usuario__username=cedula)
            bloques = HorarioMedico.objects.filter(medico=medico)
            self.assertEqual(bloques.count(), 10)  # 5 días x (mañana + tarde)
            self.assertEqual(set(bloques.values_list('dia_semana', flat=True)), {0, 1, 2, 3, 4})

    def test_seed_dev_es_idempotente(self):
        call_command('seed_dev', stdout=StringIO())
        call_command('seed_dev', stdout=StringIO())

        self.assertEqual(Usuario.objects.filter(username='9999999999').count(), 1)
        medico = Medico.objects.get(usuario__username='7777777777')
        self.assertEqual(HorarioMedico.objects.filter(medico=medico).count(), 10)

    def test_seed_dev_reestablece_password_de_cuentas_ya_existentes(self):
        """Regresión: una cuenta sembrada con una contraseña que ya no
        coincide con la documentada (manipulada a mano, o sembrada por una
        corrida vieja del comando) debe volver a funcionar con la contraseña
        del README después de correr seed_dev de nuevo."""
        call_command('seed_dev', stdout=StringIO())

        paciente = Usuario.objects.get(username='8888888888')
        paciente.set_password('otra-clave-cualquiera')
        paciente.save(update_fields=['password'])

        call_command('seed_dev', stdout=StringIO())

        paciente.refresh_from_db()
        self.assertTrue(paciente.check_password('paciente123'))

    def test_seed_dev_sin_extra_solo_crea_admin(self):
        call_command('seed_dev', '--sin-extra', stdout=StringIO())

        self.assertTrue(Usuario.objects.filter(username='9999999999').exists())
        for cedula in ('8888888888', '8888888880', '7777777777', '7777777771', '7777777772'):
            self.assertFalse(Usuario.objects.filter(username=cedula).exists())

    @override_settings(DEBUG=False)
    def test_seed_dev_rechaza_ejecucion_si_debug_false(self):
        with self.assertRaises(CommandError):
            call_command('seed_dev', stdout=StringIO())
        self.assertFalse(Usuario.objects.filter(username='9999999999').exists())


class PerfilTests(TestCase):
    """HU-23 (fase 7, decisión 7: revierte el solo-lectura de la fase 6b).
    Perfil editable — correo, teléfono y fecha de nacimiento — para los tres
    roles. Nombre, apellido, tipo y número de documento siguen sin ningún
    control de edición, ni aunque se los envíe por POST (decisión 1)."""

    def setUp(self):
        self.tipo_documento = TipoDocumento.objects.get(nombre='Cédula de Ciudadanía (CC)')
        self.especialidad = Especialidad.objects.get(nombre='Pediatría')

    def datos_post(self, **overrides):
        datos = {
            'correo': 'nuevo-correo@example.com',
            'telefono': '3009998877',
            'fecha_nacimiento': '1991-02-03',
        }
        datos.update(overrides)
        return datos

    def test_el_formulario_solo_expone_correo_telefono_y_fecha_nacimiento(self):
        """El test de la fase 6b verificaba la AUSENCIA total de controles
        de edición; la decisión 7 de esta fase invierte ese sentido — ahora
        verifica que el único <form> real (aparte del de "Cerrar sesión" de
        base.html, presente en toda la app autenticada) exponga solo estos
        tres campos, y ningún otro."""
        paciente = crear_usuario('9900000001', self.tipo_documento, first_name='Diana', last_name='Ríos')
        paciente.groups.add(Group.objects.get(name='Paciente'))
        self.client.login(username='9900000001', password='ClaveSegura123')

        response = self.client.get(reverse('perfil'))
        html = response.content.decode()
        contenido = html[html.index('<main'):html.index('</main>') + len('</main>')]

        self.assertIn('name="correo"', contenido)
        self.assertIn('name="telefono"', contenido)
        self.assertIn('name="fecha_nacimiento"', contenido)
        self.assertNotIn('name="nombre"', contenido)
        self.assertNotIn('name="apellido"', contenido)
        self.assertNotIn('name="tipo_documento"', contenido)
        self.assertNotIn('name="numero_documento"', contenido)

    def test_el_administrador_ve_ademas_nombre_apellido_y_tipo_de_documento_editables(self):
        """Fase 7.1, decisión 3: la única asimetría es el administrador —
        ve además nombre, apellido y tipo de documento como campos
        editables. numero_documento sigue sin ser un campo, ni para él."""
        admin = crear_usuario('9900000021', self.tipo_documento, first_name='Marta', last_name='Osorio')
        admin.groups.add(Group.objects.get(name='Administrador'))
        self.client.login(username='9900000021', password='ClaveSegura123')

        response = self.client.get(reverse('perfil'))
        html = response.content.decode()
        contenido = html[html.index('<main'):html.index('</main>') + len('</main>')]

        self.assertIn('name="correo"', contenido)
        self.assertIn('name="telefono"', contenido)
        self.assertIn('name="fecha_nacimiento"', contenido)
        self.assertIn('name="nombre"', contenido)
        self.assertIn('name="apellido"', contenido)
        self.assertIn('name="tipo_documento"', contenido)
        self.assertNotIn('name="numero_documento"', contenido)

    def test_muestra_los_datos_propios_del_usuario_autenticado(self):
        paciente = crear_usuario(
            '9900000002', self.tipo_documento, first_name='Diana', last_name='Ríos',
            telefono='3001234567', fecha_nacimiento='1992-05-10',
        )
        paciente.groups.add(Group.objects.get(name='Paciente'))
        self.client.login(username='9900000002', password='ClaveSegura123')

        response = self.client.get(reverse('perfil'))

        self.assertContains(response, 'Diana Ríos')
        self.assertContains(response, '9900000002')
        self.assertContains(response, paciente.email)
        self.assertContains(response, '3001234567')

    def test_medico_ve_especialidad_y_registro_profesional(self):
        medico_usuario = crear_usuario('9900000003', self.tipo_documento, first_name='Carlos', last_name='Peña')
        medico_usuario.groups.add(Group.objects.get(name='Medico'))
        Medico.objects.create(usuario=medico_usuario, especialidad=self.especialidad, registro_medico='RM-12345')
        self.client.login(username='9900000003', password='ClaveSegura123')

        response = self.client.get(reverse('perfil'))

        self.assertContains(response, self.especialidad.nombre)
        self.assertContains(response, 'RM-12345')

    def test_paciente_no_ve_especialidad_ni_registro_profesional(self):
        paciente = crear_usuario('9900000004', self.tipo_documento, first_name='Ana', last_name='Gómez')
        paciente.groups.add(Group.objects.get(name='Paciente'))
        self.client.login(username='9900000004', password='ClaveSegura123')

        response = self.client.get(reverse('perfil'))

        self.assertNotContains(response, 'Registro profesional')

    def test_no_hay_forma_de_pedir_el_perfil_de_otro_usuario_por_la_url(self):
        """La vista no acepta ningún identificador por URL/querystring: siempre
        es request.user, así que no hay parámetro que manipular."""
        paciente = crear_usuario('9900000005', self.tipo_documento, first_name='Ana', last_name='Gómez')
        paciente.groups.add(Group.objects.get(name='Paciente'))
        otro = crear_usuario('9900000006', self.tipo_documento, first_name='Luis', last_name='Nombre')
        otro.groups.add(Group.objects.get(name='Paciente'))
        self.client.login(username='9900000005', password='ClaveSegura123')

        response = self.client.get(reverse('perfil'), {'usuario': otro.pk, 'id': otro.pk})

        self.assertContains(response, 'Ana Gómez')
        self.assertNotContains(response, 'Luis Nombre')

    def test_un_post_con_el_id_de_otro_usuario_solo_edita_lo_propio(self):
        """La vista sigue sin leer ningún identificador del POST — sale
        siempre de request.user — así que un id/usuario/pk ajeno en el
        cuerpo del POST no tiene ningún efecto."""
        paciente = crear_usuario('9900000008', self.tipo_documento)
        paciente.groups.add(Group.objects.get(name='Paciente'))
        otro = crear_usuario('9900000009', self.tipo_documento)
        otro.groups.add(Group.objects.get(name='Paciente'))
        self.client.login(username='9900000008', password='ClaveSegura123')

        self.client.post(reverse('perfil'), self.datos_post(usuario=otro.pk, id=otro.pk, pk=otro.pk))

        paciente.refresh_from_db()
        otro.refresh_from_db()
        self.assertEqual(paciente.email, 'nuevo-correo@example.com')
        self.assertEqual(otro.email, '9900000009@example.com')

    def test_un_post_no_cambia_nombre_apellido_tipo_ni_numero_de_documento(self):
        paciente = crear_usuario('9900000007', self.tipo_documento, first_name='Ana', last_name='Gómez')
        paciente.groups.add(Group.objects.get(name='Paciente'))
        ti = TipoDocumento.objects.get(codigo='TI')
        self.client.login(username='9900000007', password='ClaveSegura123')

        self.client.post(reverse('perfil'), self.datos_post(
            nombre='Otro nombre', apellido='Otro apellido',
            tipo_documento=ti.pk, numero_documento='000000',
        ))

        paciente.refresh_from_db()
        self.assertEqual(paciente.first_name, 'Ana')
        self.assertEqual(paciente.last_name, 'Gómez')
        self.assertEqual(paciente.tipo_documento, self.tipo_documento)
        self.assertEqual(paciente.numero_documento, '9900000007')
        self.assertEqual(paciente.username, '9900000007')

    def test_medico_no_puede_cambiar_su_propio_nombre_ni_tipo_de_documento_desde_el_perfil(self):
        ti = TipoDocumento.objects.get(codigo='TI')
        medico_usuario = crear_usuario('9900000010', self.tipo_documento, first_name='Carlos', last_name='Peña')
        medico_usuario.groups.add(Group.objects.get(name='Medico'))
        Medico.objects.create(usuario=medico_usuario, especialidad=self.especialidad)
        self.client.login(username='9900000010', password='ClaveSegura123')

        self.client.post(reverse('perfil'), self.datos_post(nombre='Otro', tipo_documento=ti.pk))

        medico_usuario.refresh_from_db()
        self.assertEqual(medico_usuario.first_name, 'Carlos')
        self.assertEqual(medico_usuario.tipo_documento, self.tipo_documento)

    def test_admin_puede_cambiar_su_propio_nombre_y_tipo_de_documento_desde_el_perfil(self):
        """Fase 7.1, decisión 3: reemplaza a la regla anterior ("si un
        administrador necesita cambiar su nombre, lo hace otro
        administrador"), que con un solo administrador en el sistema no
        tenía salida. El administrador ya edita estos mismos datos de
        terceros, así que sobre sí mismo se comporta igual — auditado con
        él mismo en realizado_por, no distinto de cualquier otra edición."""
        ti = TipoDocumento.objects.get(codigo='TI')
        admin = crear_usuario('9900000011', self.tipo_documento, first_name='Marta', last_name='Osorio')
        admin.groups.add(Group.objects.get(name='Administrador'))
        self.client.login(username='9900000011', password='ClaveSegura123')

        response = self.client.post(reverse('perfil'), self.datos_post(
            nombre='Marta Nueva', apellido='Osorio Nueva', tipo_documento=ti.pk,
        ))
        self.assertRedirects(response, reverse('perfil'))

        admin.refresh_from_db()
        self.assertEqual(admin.first_name, 'Marta Nueva')
        self.assertEqual(admin.last_name, 'Osorio Nueva')
        self.assertEqual(admin.tipo_documento, ti)

        cambio_nombre = CambioUsuarioLog.objects.get(usuario=admin, campo='first_name')
        self.assertEqual(cambio_nombre.realizado_por, admin)
        cambio_tipo = CambioUsuarioLog.objects.get(usuario=admin, campo='tipo_documento')
        self.assertEqual(cambio_tipo.realizado_por, admin)

    def test_admin_no_puede_cambiar_su_propio_numero_de_documento_por_ningun_camino(self):
        admin = crear_usuario('9900000022', self.tipo_documento, first_name='Marta', last_name='Osorio')
        admin.groups.add(Group.objects.get(name='Administrador'))
        self.client.login(username='9900000022', password='ClaveSegura123')

        response = self.client.post(reverse('perfil'), self.datos_post(
            nombre='Marta', apellido='Osorio', tipo_documento=self.tipo_documento.pk,
            numero_documento='000000',
        ))
        self.assertRedirects(response, reverse('perfil'))

        admin.refresh_from_db()
        self.assertEqual(admin.numero_documento, '9900000022')
        self.assertEqual(admin.username, '9900000022')
        self.assertFalse(CambioUsuarioLog.objects.filter(campo='numero_documento').exists())
        self.assertFalse(CambioUsuarioLog.objects.filter(campo='username').exists())

    def test_admin_no_puede_cambiar_su_propio_tipo_de_documento_si_el_numero_no_corresponde(self):
        ti = TipoDocumento.objects.get(codigo='TI')
        admin = crear_usuario('123456', self.tipo_documento, first_name='Marta', last_name='Osorio')
        admin.groups.add(Group.objects.get(name='Administrador'))
        self.client.login(username='123456', password='ClaveSegura123')

        response = self.client.post(reverse('perfil'), self.datos_post(
            nombre='Marta', apellido='Osorio', tipo_documento=ti.pk,
        ))

        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context['form'], 'tipo_documento',
            'La Tarjeta de Identidad debe tener entre 10 y 11 dígitos. El número de documento no se '
            'puede editar: si no corresponde a este tipo, hay que crear la cuenta de nuevo.',
        )
        admin.refresh_from_db()
        self.assertEqual(admin.tipo_documento, self.tipo_documento)
        self.assertFalse(CambioUsuarioLog.objects.filter(usuario=admin, campo='tipo_documento').exists())

    def test_admin_puede_cambiar_su_propio_tipo_de_documento_si_el_numero_si_corresponde(self):
        ce = TipoDocumento.objects.get(codigo='CE')
        admin = crear_usuario('1234567', self.tipo_documento, first_name='Marta', last_name='Osorio')
        admin.groups.add(Group.objects.get(name='Administrador'))
        self.client.login(username='1234567', password='ClaveSegura123')

        response = self.client.post(reverse('perfil'), self.datos_post(
            nombre='Marta', apellido='Osorio', tipo_documento=ce.pk,
        ))
        self.assertRedirects(response, reverse('perfil'))

        admin.refresh_from_db()
        self.assertEqual(admin.tipo_documento, ce)
        cambio = CambioUsuarioLog.objects.get(usuario=admin, campo='tipo_documento')
        self.assertEqual(cambio.valor_anterior, self.tipo_documento.nombre)
        self.assertEqual(cambio.valor_nuevo, ce.nombre)
        self.assertEqual(cambio.realizado_por, admin)

    def test_correo_telefono_y_fecha_nacimiento_se_actualizan(self):
        paciente = crear_usuario('9900000012', self.tipo_documento)
        paciente.groups.add(Group.objects.get(name='Paciente'))
        self.client.login(username='9900000012', password='ClaveSegura123')

        response = self.client.post(reverse('perfil'), self.datos_post())
        self.assertRedirects(response, reverse('perfil'))

        paciente.refresh_from_db()
        self.assertEqual(paciente.email, 'nuevo-correo@example.com')
        self.assertEqual(paciente.telefono, '3009998877')
        self.assertEqual(str(paciente.fecha_nacimiento), '1991-02-03')

    def test_correo_ya_usado_por_otro_se_rechaza(self):
        crear_usuario('9900000013', self.tipo_documento, email='ocupado@example.com')
        paciente = crear_usuario('9900000014', self.tipo_documento)
        paciente.groups.add(Group.objects.get(name='Paciente'))
        self.client.login(username='9900000014', password='ClaveSegura123')

        response = self.client.post(reverse('perfil'), self.datos_post(correo='ocupado@example.com'))

        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context['form'], 'correo', 'Ya existe una cuenta registrada con este correo electrónico.',
        )
        paciente.refresh_from_db()
        self.assertNotEqual(paciente.email, 'ocupado@example.com')

    def test_validacion_fallida_repuebla_los_campos_enviados(self):
        paciente = crear_usuario('9900000015', self.tipo_documento)
        paciente.groups.add(Group.objects.get(name='Paciente'))
        self.client.login(username='9900000015', password='ClaveSegura123')

        response = self.client.post(reverse('perfil'), self.datos_post(fecha_nacimiento='2999-01-01'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="nuevo-correo@example.com"')

    def test_ida_y_vuelta_con_los_campos_reales_del_html(self):
        paciente = crear_usuario('9900000016', self.tipo_documento)
        paciente.groups.add(Group.objects.get(name='Paciente'))
        self.client.login(username='9900000016', password='ClaveSegura123')

        respuesta_get = self.client.get(reverse('perfil'))
        datos = datos_formulario_reales(respuesta_get.content.decode())
        datos.update({'correo': 'ida-vuelta@example.com', 'telefono': '3001112233'})

        respuesta_post = self.client.post(reverse('perfil'), datos)
        self.assertRedirects(respuesta_post, reverse('perfil'))

        paciente.refresh_from_db()
        self.assertEqual(paciente.email, 'ida-vuelta@example.com')
        self.assertEqual(paciente.telefono, '3001112233')

    def test_editar_el_propio_telefono_genera_auditoria_con_uno_mismo_como_realizado_por(self):
        paciente = crear_usuario('9900000017', self.tipo_documento)
        paciente.groups.add(Group.objects.get(name='Paciente'))
        self.client.login(username='9900000017', password='ClaveSegura123')

        self.client.post(reverse('perfil'), self.datos_post(telefono='3005554433'))

        cambio = CambioUsuarioLog.objects.get(usuario=paciente, campo='telefono')
        self.assertEqual(cambio.valor_nuevo, '3005554433')
        self.assertEqual(cambio.realizado_por, paciente)

    def test_enviar_sin_cambios_no_genera_ninguna_entrada(self):
        paciente = crear_usuario(
            '9900000018', self.tipo_documento, email='igual@example.com', telefono='3000000000',
            fecha_nacimiento='1990-01-01',
        )
        paciente.groups.add(Group.objects.get(name='Paciente'))
        self.client.login(username='9900000018', password='ClaveSegura123')

        self.client.post(reverse('perfil'), {
            'correo': 'igual@example.com', 'telefono': '3000000000', 'fecha_nacimiento': '1990-01-01',
        })

        self.assertEqual(CambioUsuarioLog.objects.filter(usuario=paciente).count(), 0)

    def test_cambiar_dos_campos_a_la_vez_genera_dos_entradas(self):
        paciente = crear_usuario(
            '9900000019', self.tipo_documento, email='dos-campos@example.com', telefono='3000000000',
            fecha_nacimiento='1990-01-01',
        )
        paciente.groups.add(Group.objects.get(name='Paciente'))
        self.client.login(username='9900000019', password='ClaveSegura123')

        self.client.post(reverse('perfil'), {
            'correo': 'dos-campos-nuevo@example.com', 'telefono': '3009998877', 'fecha_nacimiento': '1990-01-01',
        })

        self.assertEqual(CambioUsuarioLog.objects.filter(usuario=paciente).count(), 2)
        self.assertFalse(CambioUsuarioLog.objects.filter(usuario=paciente, campo='fecha_nacimiento').exists())

    def test_ninguna_entrada_de_numero_de_documento_se_genera(self):
        paciente = crear_usuario('9900000020', self.tipo_documento)
        paciente.groups.add(Group.objects.get(name='Paciente'))
        self.client.login(username='9900000020', password='ClaveSegura123')

        self.client.post(reverse('perfil'), self.datos_post(
            nombre='Otro', tipo_documento=self.tipo_documento.pk, numero_documento='000000',
        ))

        self.assertFalse(CambioUsuarioLog.objects.filter(campo='numero_documento').exists())
        self.assertFalse(CambioUsuarioLog.objects.filter(campo='username').exists())

    def test_anonimo_es_redirigido_a_login(self):
        response = self.client.get(reverse('perfil'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)


class MigracionTiposDocumentoTests(TransactionTestCase):
    """0002 vuelve a crear el catálogo con los nombres originales (como en
    cualquier base ya desplegada); 0005 y 0006 deben dejarlo con la
    nomenclatura y los códigos nuevos, sin duplicar filas, sin importar si
    la base ya traía los nombres viejos."""

    def test_migrar_desde_una_base_con_nombres_viejos_deja_el_catalogo_completo_y_sin_duplicados(self):
        app = 'usuarios'
        executor = MigrationExecutor(connection)

        # Retroceder a justo antes del renombrado: el catálogo queda con los
        # 3 nombres originales, como en una base ya desplegada.
        executor.migrate([(app, '0004_medico')])
        executor.loader.build_graph()

        old_apps = executor.loader.project_state((app, '0004_medico')).apps
        TipoDocumentoViejo = old_apps.get_model(app, 'TipoDocumento')
        self.assertEqual(
            set(TipoDocumentoViejo.objects.values_list('nombre', flat=True)),
            {'Cédula de ciudadanía', 'Tarjeta de identidad', 'Cédula de extranjería'},
        )

        # Migrar hacia adelante hasta la última migración de usuarios.
        executor = MigrationExecutor(connection)
        destino = executor.loader.graph.leaf_nodes(app)
        executor.migrate(destino)
        executor.loader.build_graph()

        nuevos_apps = executor.loader.project_state(destino[0]).apps
        TipoDocumentoNuevo = nuevos_apps.get_model(app, 'TipoDocumento')

        nombres = list(TipoDocumentoNuevo.objects.order_by('id').values_list('nombre', flat=True))
        self.assertEqual(len(nombres), 5)
        self.assertEqual(len(nombres), len(set(nombres)))
        self.assertEqual(
            set(nombres),
            {
                'Cédula de Ciudadanía (CC)', 'Tarjeta de Identidad (TI)',
                'Cédula de Extranjería (CE)', 'Permiso Especial (PPT)', 'Pasaporte (PA)',
            },
        )

        codigos = list(TipoDocumentoNuevo.objects.order_by('id').values_list('codigo', flat=True))
        self.assertEqual(len(codigos), len(set(codigos)))
        self.assertEqual(set(codigos), {'CC', 'TI', 'CE', 'PPT', 'PA'})


# --- Fase 6c: búsqueda y ficha de solo lectura del paciente (HU-18 a HU-20) --

class PanelPacientesBusquedaTests(TestCase):
    """HU-18: búsqueda de un paciente por número de documento, coincidencia
    exacta (decisión 2) — nunca por nombre, y sin padrón navegable."""

    def setUp(self):
        especialidad = Especialidad.objects.get(nombre='Medicina general')
        self.admin = crear_admin('9950000001')
        self.paciente = crear_paciente('9950000002', first_name='Laura', last_name='Nieto')
        self.medico = crear_medico('9950000003', especialidad)
        self.client.login(username='9950000001', password='ClaveSegura123')

    def test_encuentra_al_paciente_por_su_numero_de_documento(self):
        response = self.client.get(reverse('panel_pacientes'), {'numero_documento': '9950000002'})
        self.assertContains(response, reverse('panel_paciente_ficha', args=[self.paciente.pk]))
        self.assertContains(response, 'Laura Nieto')

    def test_numero_documento_inexistente_no_encuentra_nada(self):
        response = self.client.get(reverse('panel_pacientes'), {'numero_documento': '0000000000'})
        self.assertContains(response, 'No se encontró ningún paciente')
        self.assertIsNone(response.context['paciente'])

    def test_cedula_de_un_medico_no_devuelve_una_ficha(self):
        response = self.client.get(reverse('panel_pacientes'), {'numero_documento': '9950000003'})
        self.assertContains(response, 'No se encontró ningún paciente')

    def test_cedula_de_un_administrador_no_devuelve_una_ficha(self):
        response = self.client.get(reverse('panel_pacientes'), {'numero_documento': '9950000001'})
        self.assertContains(response, 'No se encontró ningún paciente')

    def test_sin_busqueda_no_muestra_resultado_ni_error(self):
        response = self.client.get(reverse('panel_pacientes'))
        self.assertNotContains(response, 'No se encontró')
        self.assertFalse(response.context['buscado'])

    def test_ida_y_vuelta_formulario_de_busqueda(self):
        response = self.client.get(reverse('panel_pacientes'))
        datos = datos_formulario_reales(response.content.decode())
        datos['numero_documento'] = '9950000002'
        response2 = self.client.get(reverse('panel_pacientes'), datos)
        self.assertContains(response2, reverse('panel_paciente_ficha', args=[self.paciente.pk]))

    def test_numero_documento_se_repuebla_en_el_input(self):
        response = self.client.get(reverse('panel_pacientes'), {'numero_documento': '9950000002'})
        self.assertContains(response, 'value="9950000002"')

    def test_paciente_no_accede_a_la_busqueda(self):
        self.client.logout()
        self.client.login(username='9950000002', password='ClaveSegura123')
        response = self.client.get(reverse('panel_pacientes'))
        self.assertEqual(response.status_code, 403)

    def test_medico_no_accede_a_la_busqueda(self):
        self.client.logout()
        self.client.login(username='9950000003', password='ClaveSegura123')
        response = self.client.get(reverse('panel_pacientes'))
        self.assertEqual(response.status_code, 403)

    def test_anonimo_es_redirigido_a_login(self):
        self.client.logout()
        response = self.client.get(reverse('panel_pacientes'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)

    def test_post_a_la_busqueda_no_crea_ni_modifica_nada(self):
        usuarios_antes = Usuario.objects.count()
        self.client.post(reverse('panel_pacientes'), {'numero_documento': '9950000002'})
        self.assertEqual(Usuario.objects.count(), usuarios_antes)


class PanelPacienteFichaTests(TestCase):
    """HU-18: ficha de solo lectura, con el desglose de citas por estado
    (decisión 5), resuelto en una sola consulta agregada."""

    def setUp(self):
        self.especialidad = Especialidad.objects.get(nombre='Medicina general')
        self.admin = crear_admin('9960000001')
        self.medico = crear_medico('9960000002', self.especialidad)
        self.paciente = crear_paciente(
            '9960000003', first_name='Pedro', last_name='Salas',
            email='pedro.salas@example.com', telefono='3000000000', fecha_nacimiento='1988-04-12',
        )
        self.client.login(username='9960000001', password='ClaveSegura123')

    def test_muestra_los_datos_personales(self):
        response = self.client.get(reverse('panel_paciente_ficha', args=[self.paciente.pk]))
        self.assertContains(response, 'Pedro Salas')
        self.assertContains(response, 'pedro.salas@example.com')
        self.assertContains(response, '3000000000')
        self.assertContains(response, self.paciente.numero_documento)

    def test_los_cuatro_conteos_son_cero_para_un_paciente_sin_citas(self):
        response = self.client.get(reverse('panel_paciente_ficha', args=[self.paciente.pk]))
        self.assertEqual(response.context['conteo_confirmada'], 0)
        self.assertEqual(response.context['conteo_atendida'], 0)
        self.assertEqual(response.context['conteo_cancelada'], 0)
        self.assertEqual(response.context['conteo_no_atendida'], 0)

    def test_conteo_por_estado_correcto_con_citas_en_los_cuatro_estados(self):
        fecha = timezone.localdate() - timedelta(days=1)
        for i, nombre_estado in enumerate(['confirmada', 'atendida', 'cancelada', 'no atendida']):
            Cita.objects.create(
                paciente=self.paciente, medico=self.medico, estado=EstadoCita.objects.get(nombre=nombre_estado),
                fecha=fecha, hora_inicio=f'{8 + i:02d}:00', hora_fin=f'{8 + i:02d}:30',
            )
        response = self.client.get(reverse('panel_paciente_ficha', args=[self.paciente.pk]))
        self.assertEqual(response.context['conteo_confirmada'], 1)
        self.assertEqual(response.context['conteo_atendida'], 1)
        self.assertEqual(response.context['conteo_cancelada'], 1)
        self.assertEqual(response.context['conteo_no_atendida'], 1)

    def test_motivo_de_consulta_no_aparece_en_la_ficha(self):
        Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=EstadoCita.objects.get(nombre='confirmada'),
            fecha=timezone.localdate() + timedelta(days=1), hora_inicio='08:00', hora_fin='08:30',
            motivo_consulta='Motivo secreto de la consulta',
        )
        response = self.client.get(reverse('panel_paciente_ficha', args=[self.paciente.pk]))
        self.assertNotContains(response, 'Motivo secreto de la consulta')

    def test_url_directa_con_pk_de_medico_o_admin_da_404(self):
        self.assertEqual(
            self.client.get(reverse('panel_paciente_ficha', args=[self.medico.usuario.pk])).status_code, 404,
        )
        self.assertEqual(
            self.client.get(reverse('panel_paciente_ficha', args=[self.admin.pk])).status_code, 404,
        )

    def test_paciente_no_accede_a_la_ficha(self):
        self.client.logout()
        self.client.login(username='9960000003', password='ClaveSegura123')
        response = self.client.get(reverse('panel_paciente_ficha', args=[self.paciente.pk]))
        self.assertEqual(response.status_code, 403)

    def test_medico_no_accede_a_la_ficha(self):
        self.client.logout()
        self.client.login(username='9960000002', password='ClaveSegura123')
        response = self.client.get(reverse('panel_paciente_ficha', args=[self.paciente.pk]))
        self.assertEqual(response.status_code, 403)

    def test_anonimo_es_redirigido_a_login(self):
        self.client.logout()
        response = self.client.get(reverse('panel_paciente_ficha', args=[self.paciente.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)

    def test_post_no_crea_ni_modifica_nada(self):
        citas_antes = Cita.objects.count()
        self.client.post(reverse('panel_paciente_ficha', args=[self.paciente.pk]), {})
        self.assertEqual(Cita.objects.count(), citas_antes)

    def _crear_citas(self, cantidad, offset=0):
        for i in range(cantidad):
            n = offset + i
            Cita.objects.create(
                paciente=self.paciente, medico=self.medico, estado=EstadoCita.objects.get(nombre='atendida'),
                fecha=timezone.localdate() - timedelta(days=n + 1),
                hora_inicio=f'{8 + (n % 10):02d}:00', hora_fin=f'{8 + (n % 10):02d}:30',
            )

    def test_numero_de_consultas_no_crece_con_la_cantidad_de_citas(self):
        """El conteo por estado se resuelve en una sola consulta agregada
        (contar_citas_por_estado): lo que varía acá es la cantidad de CITAS
        del paciente, no la cantidad de médicos ni de pacientes del padrón."""
        self._crear_citas(2)
        with CaptureQueriesContext(connection) as pocas:
            self.client.get(reverse('panel_paciente_ficha', args=[self.paciente.pk]))
        self._crear_citas(10, offset=2)
        with CaptureQueriesContext(connection) as muchas:
            self.client.get(reverse('panel_paciente_ficha', args=[self.paciente.pk]))
        self.assertEqual(len(pocas.captured_queries), len(muchas.captured_queries))

    def test_la_ficha_tiene_un_acceso_de_edicion(self):
        response = self.client.get(reverse('panel_paciente_ficha', args=[self.paciente.pk]))
        self.assertContains(response, reverse('panel_paciente_editar', args=[self.paciente.pk]))


class PanelPacienteEditarTests(TestCase):
    """Fase 7, tarea 4 (HU-18): el administrador edita todos los campos de
    la columna "Admin sobre otros" de la decisión 1 desde la ficha del
    paciente — nombre, apellido, correo, teléfono, fecha de nacimiento y
    tipo de documento. numero_documento nunca es un campo del formulario."""

    def setUp(self):
        self.especialidad = Especialidad.objects.get(nombre='Medicina general')
        self.cc = TipoDocumento.objects.get(codigo='CC')
        self.ti = TipoDocumento.objects.get(codigo='TI')
        self.admin = crear_admin('9965000001')
        self.medico = crear_medico('9965000002', self.especialidad)
        self.paciente = crear_paciente(
            '9965000003', first_name='Pedro', last_name='Salas',
            email='pedro.salas.9965000003@example.com', telefono='3000000000', fecha_nacimiento='1988-04-12',
        )
        self.client.login(username='9965000001', password='ClaveSegura123')

    def datos_post(self, **overrides):
        datos = {
            'nombre': 'Pedro', 'apellido': 'Salas Nuevo',
            'correo': 'pedro.nuevo.9965000003@example.com', 'telefono': '3001112233',
            'fecha_nacimiento': '1988-04-12', 'tipo_documento': self.ti.pk,
        }
        datos.update(overrides)
        return datos

    def test_edita_nombre_apellido_correo_telefono_fecha_y_tipo_documento(self):
        response = self.client.post(reverse('panel_paciente_editar', args=[self.paciente.pk]), self.datos_post())
        self.assertRedirects(response, reverse('panel_paciente_ficha', args=[self.paciente.pk]))

        self.paciente.refresh_from_db()
        self.assertEqual(self.paciente.first_name, 'Pedro')
        self.assertEqual(self.paciente.last_name, 'Salas Nuevo')
        self.assertEqual(self.paciente.email, 'pedro.nuevo.9965000003@example.com')
        self.assertEqual(self.paciente.telefono, '3001112233')
        self.assertEqual(self.paciente.tipo_documento, self.ti)

    def test_un_post_con_numero_de_documento_no_lo_cambia(self):
        numero_original = self.paciente.numero_documento
        self.client.post(
            reverse('panel_paciente_editar', args=[self.paciente.pk]),
            self.datos_post(numero_documento='999999999'),
        )
        self.paciente.refresh_from_db()
        self.assertEqual(self.paciente.numero_documento, numero_original)

    def test_el_username_no_cambia_al_editar_el_tipo_de_documento(self):
        username_original = self.paciente.username
        self.client.post(reverse('panel_paciente_editar', args=[self.paciente.pk]), self.datos_post())

        self.paciente.refresh_from_db()
        self.assertEqual(self.paciente.username, username_original)

    def test_cambiar_el_tipo_se_rechaza_si_el_numero_no_corresponde(self):
        """Fase 7.1, decisión 1: el paciente de setUp tiene numero_documento
        '9965000003' (10 dígitos) — válido para CC/TI, inválido para CE
        (6-8 dígitos)."""
        ce = TipoDocumento.objects.get(codigo='CE')
        response = self.client.post(
            reverse('panel_paciente_editar', args=[self.paciente.pk]),
            self.datos_post(tipo_documento=ce.pk),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context['form'], 'tipo_documento',
            'La Cédula de Extranjería debe tener entre 6 y 8 dígitos. El número de documento no se '
            'puede editar: si no corresponde a este tipo, hay que crear la cuenta de nuevo.',
        )
        self.paciente.refresh_from_db()
        self.assertEqual(self.paciente.tipo_documento, self.cc)
        self.assertFalse(CambioUsuarioLog.objects.filter(usuario=self.paciente, campo='tipo_documento').exists())

    def test_el_rechazo_del_tipo_repuebla_los_demas_campos_enviados(self):
        ce = TipoDocumento.objects.get(codigo='CE')
        response = self.client.post(
            reverse('panel_paciente_editar', args=[self.paciente.pk]),
            self.datos_post(tipo_documento=ce.pk, apellido='Salas Repoblado'),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="Salas Repoblado"')

    def test_cambiar_el_tipo_funciona_si_el_numero_si_corresponde_y_queda_auditado(self):
        response = self.client.post(reverse('panel_paciente_editar', args=[self.paciente.pk]), self.datos_post())
        self.assertRedirects(response, reverse('panel_paciente_ficha', args=[self.paciente.pk]))

        self.paciente.refresh_from_db()
        self.assertEqual(self.paciente.tipo_documento, self.ti)
        cambio = CambioUsuarioLog.objects.get(usuario=self.paciente, campo='tipo_documento')
        self.assertEqual(cambio.valor_anterior, self.cc.nombre)
        self.assertEqual(cambio.valor_nuevo, self.ti.nombre)
        self.assertEqual(cambio.realizado_por, self.admin)

    def test_url_directa_con_pk_de_medico_o_admin_da_404(self):
        self.assertEqual(
            self.client.get(reverse('panel_paciente_editar', args=[self.medico.usuario.pk])).status_code, 404,
        )
        self.assertEqual(
            self.client.get(reverse('panel_paciente_editar', args=[self.admin.pk])).status_code, 404,
        )

    def test_paciente_no_accede(self):
        self.client.logout()
        self.client.login(username='9965000003', password='ClaveSegura123')
        response = self.client.get(reverse('panel_paciente_editar', args=[self.paciente.pk]))
        self.assertEqual(response.status_code, 403)

    def test_medico_no_accede(self):
        self.client.logout()
        self.client.login(username='9965000002', password='ClaveSegura123')
        response = self.client.get(reverse('panel_paciente_editar', args=[self.paciente.pk]))
        self.assertEqual(response.status_code, 403)

    def test_anonimo_es_redirigido_a_login(self):
        self.client.logout()
        response = self.client.get(reverse('panel_paciente_editar', args=[self.paciente.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)

    def test_ida_y_vuelta_con_los_campos_reales_del_html(self):
        respuesta_get = self.client.get(reverse('panel_paciente_editar', args=[self.paciente.pk]))
        datos = datos_formulario_reales(respuesta_get.content.decode())
        datos.update({'telefono': '3004445566', 'apellido': 'Salas Ida Vuelta'})

        respuesta_post = self.client.post(reverse('panel_paciente_editar', args=[self.paciente.pk]), datos)
        self.assertRedirects(respuesta_post, reverse('panel_paciente_ficha', args=[self.paciente.pk]))

        self.paciente.refresh_from_db()
        self.assertEqual(self.paciente.telefono, '3004445566')
        self.assertEqual(self.paciente.last_name, 'Salas Ida Vuelta')

    def test_validacion_fallida_repuebla_los_campos_enviados(self):
        response = self.client.post(
            reverse('panel_paciente_editar', args=[self.paciente.pk]),
            self.datos_post(fecha_nacimiento='2999-01-01'),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="Salas Nuevo"')

    def test_correo_ya_usado_por_otro_se_rechaza(self):
        crear_paciente('9965000004', email='ocupado-9965000004@example.com')
        response = self.client.post(
            reverse('panel_paciente_editar', args=[self.paciente.pk]),
            self.datos_post(correo='ocupado-9965000004@example.com'),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context['form'], 'correo', 'Ya existe una cuenta registrada con este correo electrónico.',
        )

    def test_editar_el_correo_del_paciente_genera_una_entrada_con_el_administrador_como_realizado_por(self):
        correo_anterior = self.paciente.email
        self.client.post(
            reverse('panel_paciente_editar', args=[self.paciente.pk]),
            self.datos_post(correo='otro-correo-9965000003@example.com'),
        )

        cambio = CambioUsuarioLog.objects.get(usuario=self.paciente, campo='email')
        self.assertEqual(cambio.valor_anterior, correo_anterior)
        self.assertEqual(cambio.valor_nuevo, 'otro-correo-9965000003@example.com')
        self.assertEqual(cambio.realizado_por, self.admin)

    def test_enviar_sin_cambios_no_genera_ninguna_entrada(self):
        self.client.post(reverse('panel_paciente_editar', args=[self.paciente.pk]), {
            'nombre': 'Pedro', 'apellido': 'Salas',
            'correo': self.paciente.email, 'telefono': self.paciente.telefono,
            'fecha_nacimiento': '1988-04-12', 'tipo_documento': self.cc.pk,
        })
        self.assertEqual(CambioUsuarioLog.objects.filter(usuario=self.paciente).count(), 0)

    def test_cambiar_dos_campos_a_la_vez_genera_dos_entradas(self):
        self.client.post(reverse('panel_paciente_editar', args=[self.paciente.pk]), {
            'nombre': 'Pedro', 'apellido': 'Salas',
            'correo': 'dos-campos-9965000003@example.com', 'telefono': '3009990000',
            'fecha_nacimiento': '1988-04-12', 'tipo_documento': self.cc.pk,
        })
        self.assertEqual(CambioUsuarioLog.objects.filter(usuario=self.paciente).count(), 2)

    def test_ninguna_entrada_de_numero_de_documento_se_genera(self):
        self.client.post(
            reverse('panel_paciente_editar', args=[self.paciente.pk]),
            self.datos_post(numero_documento='999999999'),
        )
        self.assertFalse(CambioUsuarioLog.objects.filter(campo='numero_documento').exists())
        self.assertFalse(CambioUsuarioLog.objects.filter(campo='username').exists())


class PanelPacienteCitasTests(TestCase):
    """HU-19: pestaña de citas programadas — solo confirmadas y futuras."""

    def setUp(self):
        self.especialidad = Especialidad.objects.get(nombre='Medicina general')
        self.admin = crear_admin('9970000001')
        self.medico = crear_medico('9970000002', self.especialidad)
        self.paciente = crear_paciente('9970000003')
        self.client.login(username='9970000001', password='ClaveSegura123')

    def test_sin_citas_muestra_el_mensaje_de_vacio(self):
        response = self.client.get(reverse('panel_paciente_citas', args=[self.paciente.pk]))
        self.assertContains(response, 'El usuario no tiene citas programadas')

    def test_solo_muestra_confirmadas_futuras(self):
        futura = Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=EstadoCita.objects.get(nombre='confirmada'),
            fecha=timezone.localdate() + timedelta(days=3), hora_inicio='08:00', hora_fin='08:30',
        )
        Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=EstadoCita.objects.get(nombre='atendida'),
            fecha=timezone.localdate() - timedelta(days=3), hora_inicio='08:00', hora_fin='08:30',
        )
        Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=EstadoCita.objects.get(nombre='cancelada'),
            fecha=timezone.localdate() + timedelta(days=4), hora_inicio='09:00', hora_fin='09:30',
        )
        Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=EstadoCita.objects.get(nombre='no atendida'),
            fecha=timezone.localdate() - timedelta(days=4), hora_inicio='09:00', hora_fin='09:30',
        )
        response = self.client.get(reverse('panel_paciente_citas', args=[self.paciente.pk]))
        self.assertEqual(list(response.context['citas']), [futura])

    def test_motivo_de_consulta_no_aparece(self):
        Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=EstadoCita.objects.get(nombre='confirmada'),
            fecha=timezone.localdate() + timedelta(days=3), hora_inicio='08:00', hora_fin='08:30',
            motivo_consulta='Motivo secreto de la consulta',
        )
        response = self.client.get(reverse('panel_paciente_citas', args=[self.paciente.pk]))
        self.assertNotContains(response, 'Motivo secreto de la consulta')

    def test_url_directa_con_pk_de_medico_o_admin_da_404(self):
        self.assertEqual(
            self.client.get(reverse('panel_paciente_citas', args=[self.medico.usuario.pk])).status_code, 404,
        )
        self.assertEqual(
            self.client.get(reverse('panel_paciente_citas', args=[self.admin.pk])).status_code, 404,
        )

    def test_paciente_no_accede(self):
        self.client.logout()
        self.client.login(username='9970000003', password='ClaveSegura123')
        response = self.client.get(reverse('panel_paciente_citas', args=[self.paciente.pk]))
        self.assertEqual(response.status_code, 403)

    def test_medico_no_accede(self):
        self.client.logout()
        self.client.login(username='9970000002', password='ClaveSegura123')
        response = self.client.get(reverse('panel_paciente_citas', args=[self.paciente.pk]))
        self.assertEqual(response.status_code, 403)

    def test_anonimo_es_redirigido(self):
        self.client.logout()
        response = self.client.get(reverse('panel_paciente_citas', args=[self.paciente.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)

    def test_post_no_crea_ni_modifica_nada(self):
        citas_antes = Cita.objects.count()
        self.client.post(reverse('panel_paciente_citas', args=[self.paciente.pk]), {})
        self.assertEqual(Cita.objects.count(), citas_antes)

    def _crear_citas_confirmadas(self, cantidad, offset=0):
        for i in range(cantidad):
            n = offset + i
            Cita.objects.create(
                paciente=self.paciente, medico=self.medico, estado=EstadoCita.objects.get(nombre='confirmada'),
                fecha=timezone.localdate() + timedelta(days=n + 1), hora_inicio='08:00', hora_fin='08:30',
            )

    def test_numero_de_consultas_no_crece_con_la_cantidad_de_citas(self):
        self._crear_citas_confirmadas(2)
        with CaptureQueriesContext(connection) as pocas:
            self.client.get(reverse('panel_paciente_citas', args=[self.paciente.pk]))
        self._crear_citas_confirmadas(10, offset=2)
        with CaptureQueriesContext(connection) as muchas:
            self.client.get(reverse('panel_paciente_citas', args=[self.paciente.pk]))
        self.assertEqual(len(pocas.captured_queries), len(muchas.captured_queries))


class PanelPacienteHistorialTests(TestCase):
    """HU-20: pestaña de historial — atendidas, canceladas y no atendidas,
    más recientes primero, con el comentario del médico cuando existe."""

    def setUp(self):
        self.especialidad = Especialidad.objects.get(nombre='Medicina general')
        self.admin = crear_admin('9980000001')
        self.medico = crear_medico('9980000002', self.especialidad)
        self.paciente = crear_paciente('9980000003')
        self.client.login(username='9980000001', password='ClaveSegura123')

    def test_sin_citas_muestra_el_mensaje_de_vacio(self):
        response = self.client.get(reverse('panel_paciente_historial', args=[self.paciente.pk]))
        self.assertContains(response, 'El usuario no tiene historial de citas')

    def test_mensajes_de_vacio_son_distintos_entre_pestanas(self):
        response_historial = self.client.get(reverse('panel_paciente_historial', args=[self.paciente.pk]))
        response_citas = self.client.get(reverse('panel_paciente_citas', args=[self.paciente.pk]))
        self.assertContains(response_historial, 'El usuario no tiene historial de citas')
        self.assertContains(response_citas, 'El usuario no tiene citas programadas')
        self.assertNotContains(response_historial, 'El usuario no tiene citas programadas')
        self.assertNotContains(response_citas, 'El usuario no tiene historial de citas')

    def test_incluye_atendida_cancelada_y_no_atendida_pero_no_confirmada_futura(self):
        futura = Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=EstadoCita.objects.get(nombre='confirmada'),
            fecha=timezone.localdate() + timedelta(days=3), hora_inicio='08:00', hora_fin='08:30',
        )
        atendida = Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=EstadoCita.objects.get(nombre='atendida'),
            fecha=timezone.localdate() - timedelta(days=1), hora_inicio='08:00', hora_fin='08:30',
        )
        cancelada = Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=EstadoCita.objects.get(nombre='cancelada'),
            fecha=timezone.localdate() - timedelta(days=2), hora_inicio='09:00', hora_fin='09:30',
        )
        no_atendida = Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=EstadoCita.objects.get(nombre='no atendida'),
            fecha=timezone.localdate() - timedelta(days=3), hora_inicio='09:00', hora_fin='09:30',
        )
        response = self.client.get(reverse('panel_paciente_historial', args=[self.paciente.pk]))
        pks = {cita.pk for cita in response.context['citas']}
        self.assertEqual(pks, {atendida.pk, cancelada.pk, no_atendida.pk})
        self.assertNotIn(futura.pk, pks)

    def test_ordenadas_por_fecha_descendente(self):
        mas_vieja = Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=EstadoCita.objects.get(nombre='atendida'),
            fecha=timezone.localdate() - timedelta(days=10), hora_inicio='08:00', hora_fin='08:30',
        )
        mas_reciente = Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=EstadoCita.objects.get(nombre='atendida'),
            fecha=timezone.localdate() - timedelta(days=1), hora_inicio='08:00', hora_fin='08:30',
        )
        response = self.client.get(reverse('panel_paciente_historial', args=[self.paciente.pk]))
        citas = list(response.context['citas'])
        self.assertEqual(citas[0].pk, mas_reciente.pk)
        self.assertEqual(citas[-1].pk, mas_vieja.pk)

    def test_comentario_del_medico_se_muestra_cuando_existe(self):
        Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=EstadoCita.objects.get(nombre='atendida'),
            fecha=timezone.localdate() - timedelta(days=1), hora_inicio='08:00', hora_fin='08:30',
            comentario_medico='Paciente estable, control en 3 meses.',
        )
        response = self.client.get(reverse('panel_paciente_historial', args=[self.paciente.pk]))
        self.assertContains(response, 'Paciente estable, control en 3 meses.')

    def test_motivo_de_consulta_no_aparece(self):
        Cita.objects.create(
            paciente=self.paciente, medico=self.medico, estado=EstadoCita.objects.get(nombre='atendida'),
            fecha=timezone.localdate() - timedelta(days=1), hora_inicio='08:00', hora_fin='08:30',
            motivo_consulta='Motivo secreto de la consulta',
        )
        response = self.client.get(reverse('panel_paciente_historial', args=[self.paciente.pk]))
        self.assertNotContains(response, 'Motivo secreto de la consulta')

    def test_url_directa_con_pk_de_medico_o_admin_da_404(self):
        self.assertEqual(
            self.client.get(reverse('panel_paciente_historial', args=[self.medico.usuario.pk])).status_code, 404,
        )
        self.assertEqual(
            self.client.get(reverse('panel_paciente_historial', args=[self.admin.pk])).status_code, 404,
        )

    def test_paciente_no_accede(self):
        self.client.logout()
        self.client.login(username='9980000003', password='ClaveSegura123')
        response = self.client.get(reverse('panel_paciente_historial', args=[self.paciente.pk]))
        self.assertEqual(response.status_code, 403)

    def test_medico_no_accede(self):
        self.client.logout()
        self.client.login(username='9980000002', password='ClaveSegura123')
        response = self.client.get(reverse('panel_paciente_historial', args=[self.paciente.pk]))
        self.assertEqual(response.status_code, 403)

    def test_anonimo_es_redirigido(self):
        self.client.logout()
        response = self.client.get(reverse('panel_paciente_historial', args=[self.paciente.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)

    def test_post_no_crea_ni_modifica_nada(self):
        citas_antes = Cita.objects.count()
        self.client.post(reverse('panel_paciente_historial', args=[self.paciente.pk]), {})
        self.assertEqual(Cita.objects.count(), citas_antes)

    def _crear_citas_atendidas(self, cantidad, offset=0):
        for i in range(cantidad):
            n = offset + i
            Cita.objects.create(
                paciente=self.paciente, medico=self.medico, estado=EstadoCita.objects.get(nombre='atendida'),
                fecha=timezone.localdate() - timedelta(days=n + 1),
                hora_inicio=f'{8 + (n % 10):02d}:00', hora_fin=f'{8 + (n % 10):02d}:30',
            )

    def test_numero_de_consultas_no_crece_con_la_cantidad_de_citas(self):
        self._crear_citas_atendidas(2)
        with CaptureQueriesContext(connection) as pocas:
            self.client.get(reverse('panel_paciente_historial', args=[self.paciente.pk]))
        self._crear_citas_atendidas(10, offset=2)
        with CaptureQueriesContext(connection) as muchas:
            self.client.get(reverse('panel_paciente_historial', args=[self.paciente.pk]))
        self.assertEqual(len(pocas.captured_queries), len(muchas.captured_queries))


class PanelPacientesNavegacionTests(TestCase):
    """Tarea 7: el acceso a Pacientes entra en la navegación del
    administrador (escritorio y móvil), usando es_admin — misma fuente que
    el resto de la navegación por rol."""

    def setUp(self):
        self.admin = crear_admin('9990000001')
        self.paciente = crear_paciente('9990000002')

    def fragmento_menu_movil(self, response):
        html = response.content.decode()
        inicio = html.index('<details')
        return html[inicio:html.index('</details>', inicio) + len('</details>')]

    def test_admin_ve_pacientes_en_escritorio_y_movil(self):
        self.client.login(username='9990000001', password='ClaveSegura123')
        response = self.client.get(reverse('panel_home'))
        html = response.content.decode()
        self.assertIn(reverse('panel_pacientes'), html)
        self.assertIn(reverse('panel_pacientes'), self.fragmento_menu_movil(response))

    def test_paciente_no_ve_el_enlace_a_pacientes(self):
        self.client.login(username='9990000002', password='ClaveSegura123')
        response = self.client.get(reverse('mis_citas'))
        self.assertNotContains(response, reverse('panel_pacientes'))
