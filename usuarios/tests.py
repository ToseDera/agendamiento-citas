from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from .models import TipoDocumento, Usuario


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
