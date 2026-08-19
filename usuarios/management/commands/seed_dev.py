from django.conf import settings
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError

from citas.models import Especialidad, HorarioMedico
from usuarios.forms import RegistroMedicoForm
from usuarios.models import Medico, TipoDocumento, Usuario

ADMIN_CEDULA = '9999999999'
ADMIN_PASSWORD = 'admin123'

PACIENTE_PASSWORD = 'paciente123'
PACIENTES = [
    # (cédula, nombre, apellido, correo)
    ('8888888888', 'Paciente', 'Prueba', 'paciente.dev@mediclick.local'),
    ('8888888880', 'Paciente', 'Dos', 'paciente2.dev@mediclick.local'),
]

MEDICO_PASSWORD = 'medico123'
MEDICOS = [
    # (cédula, nombre, apellido, especialidad, registro profesional, correo)
    ('7777777777', 'Medico', 'Prueba', 'Medicina general', 'RM-DEV-001', 'medico.dev@mediclick.local'),
    ('7777777771', 'Medico', 'Odontologia', 'Odontología', 'RM-DEV-002', 'medico.odontologia.dev@mediclick.local'),
    ('7777777772', 'Medico', 'Pediatria', 'Pediatría', 'RM-DEV-003', 'medico.pediatria.dev@mediclick.local'),
]

# Lunes a viernes, mañana y tarde: suficiente para probar agendamiento sin
# armar un horario a mano en cada reset. (dia_semana, hora_inicio, hora_fin)
HORARIO_MEDICO_DEV = [
    *[(dia, '08:00', '12:00') for dia in range(5)],
    *[(dia, '14:00', '18:00') for dia in range(5)],
]


class Command(BaseCommand):
    help = (
        'Crea datos de desarrollo: un superusuario admin (grupo Administrador) '
        'y, opcionalmente, pacientes y médicos de prueba (uno por especialidad, '
        'con horario recurrente). Idempotente: si una cuenta ya existe no la '
        'duplica, pero SÍ reestablece su contraseña a la documentada en el '
        'README y completa el horario si le falta. Solo puede correr con '
        'DEBUG=True.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--sin-extra', action='store_true',
            help='No crear los pacientes ni los médicos de prueba, solo el admin.',
        )

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError('seed_dev solo puede ejecutarse con DEBUG=True.')

        tipo_documento = TipoDocumento.objects.filter(codigo='CC').first()
        if tipo_documento is None:
            raise CommandError('No existe el catálogo TipoDocumento. Corre "migrate" primero.')

        self._crear_admin(tipo_documento)

        if not options['sin_extra']:
            for cedula, nombre, apellido, correo in PACIENTES:
                self._crear_paciente(tipo_documento, cedula, nombre, apellido, correo)
            for cedula, nombre, apellido, especialidad_nombre, registro_medico, correo in MEDICOS:
                self._crear_medico(tipo_documento, cedula, nombre, apellido, especialidad_nombre, registro_medico, correo)

    def _crear_admin(self, tipo_documento):
        admin = Usuario.objects.filter(username=ADMIN_CEDULA).first()
        if admin is not None:
            self._reestablecer_password(admin, ADMIN_PASSWORD, 'Admin de desarrollo', ADMIN_CEDULA)
            return

        admin = Usuario.objects.create_superuser(
            username=ADMIN_CEDULA,
            email='admin.dev@mediclick.local',
            password=ADMIN_PASSWORD,
            tipo_documento=tipo_documento,
            numero_documento=ADMIN_CEDULA,
            fecha_nacimiento='1990-01-01',
            first_name='Admin',
            last_name='Desarrollo',
        )
        grupo_admin, _ = Group.objects.get_or_create(name='Administrador')
        admin.groups.add(grupo_admin)
        self.stdout.write(self.style.SUCCESS(f'Admin de desarrollo creado: {ADMIN_CEDULA} / {ADMIN_PASSWORD}'))

    def _crear_paciente(self, tipo_documento, cedula, nombre, apellido, correo):
        paciente = Usuario.objects.filter(username=cedula).first()
        if paciente is not None:
            self._reestablecer_password(paciente, PACIENTE_PASSWORD, 'Paciente de prueba', cedula)
            return

        paciente = Usuario.objects.create_user(
            username=cedula,
            email=correo,
            password=PACIENTE_PASSWORD,
            tipo_documento=tipo_documento,
            numero_documento=cedula,
            fecha_nacimiento='1995-06-15',
            first_name=nombre,
            last_name=apellido,
        )
        grupo_paciente, _ = Group.objects.get_or_create(name='Paciente')
        paciente.groups.add(grupo_paciente)
        self.stdout.write(self.style.SUCCESS(f'Paciente de prueba creado: {cedula} / {PACIENTE_PASSWORD}'))

    def _crear_medico(self, tipo_documento, cedula, nombre, apellido, especialidad_nombre, registro_medico, correo):
        usuario = Usuario.objects.filter(username=cedula).first()
        if usuario is not None:
            self._reestablecer_password(usuario, MEDICO_PASSWORD, 'Médico de prueba', cedula)
            medico = Medico.objects.get(usuario=usuario)
        else:
            try:
                especialidad = Especialidad.objects.get(nombre=especialidad_nombre)
            except Especialidad.DoesNotExist:
                raise CommandError(
                    f'No existe la especialidad "{especialidad_nombre}". Corre "migrate" primero.',
                )

            form = RegistroMedicoForm(data={
                'nombre': nombre,
                'apellido': apellido,
                'fecha_nacimiento': '1985-03-20',
                'tipo_documento': tipo_documento.pk,
                'numero_documento': cedula,
                'correo': correo,
                'telefono': '',
                'especialidad': especialidad.pk,
                'registro_medico': registro_medico,
                'password1': MEDICO_PASSWORD,
                'password2': MEDICO_PASSWORD,
            })
            if not form.is_valid():
                raise CommandError(f'No se pudo crear el médico de prueba ({cedula}): {form.errors.as_text()}')
            usuario = form.save()
            medico = Medico.objects.get(usuario=usuario)
            self.stdout.write(self.style.SUCCESS(
                f'Médico de prueba creado ({especialidad_nombre}): {cedula} / {MEDICO_PASSWORD}',
            ))

        self._crear_horario(medico)

    def _crear_horario(self, medico):
        creados = 0
        for dia_semana, hora_inicio, hora_fin in HORARIO_MEDICO_DEV:
            _, fue_creado = HorarioMedico.objects.get_or_create(
                medico=medico, dia_semana=dia_semana, hora_inicio=hora_inicio,
                defaults={'hora_fin': hora_fin},
            )
            creados += fue_creado
        if creados:
            self.stdout.write(self.style.SUCCESS(
                f'  Horario de prueba completado para {medico.usuario.get_full_name()} '
                f'({creados} bloque(s) nuevo(s)): lunes a viernes, 08:00-12:00 y 14:00-18:00.',
            ))

    def _reestablecer_password(self, usuario, password, etiqueta, cedula):
        usuario.set_password(password)
        usuario.save(update_fields=['password'])
        self.stdout.write(self.style.WARNING(
            f'{etiqueta} ya existía ({cedula}); contraseña reestablecida a la documentada.',
        ))
