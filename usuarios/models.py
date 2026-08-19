from django.conf import settings
from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models


class TipoDocumento(models.Model):
    nombre = models.CharField(max_length=50, unique=True)
    codigo = models.CharField(max_length=10, unique=True)

    class Meta:
        verbose_name = 'Tipo de documento'
        verbose_name_plural = 'Tipos de documento'

    def __str__(self):
        return self.nombre


class UsuarioManager(UserManager):
    def _create_user(self, username, email, password, **extra_fields):
        tipo_documento = extra_fields.get('tipo_documento')
        if tipo_documento is not None and not isinstance(tipo_documento, TipoDocumento):
            extra_fields['tipo_documento'] = TipoDocumento.objects.get(pk=tipo_documento)
        return super()._create_user(username, email, password, **extra_fields)


class Usuario(AbstractUser):
    tipo_documento = models.ForeignKey(
        TipoDocumento, on_delete=models.PROTECT, null=False,
    )
    numero_documento = models.CharField(max_length=25)
    fecha_nacimiento = models.DateField()
    telefono = models.CharField(max_length=15, blank=True)
    email = models.EmailField(unique=True)

    REQUIRED_FIELDS = ['email', 'tipo_documento', 'fecha_nacimiento']

    objects = UsuarioManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['numero_documento'],
                name='uq_usuario_numero_documento',
            ),
        ]

    def __str__(self):
        return f'{self.get_full_name()} ({self.numero_documento})'


class Medico(models.Model):
    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='medico',
    )
    especialidad = models.ForeignKey(
        'citas.Especialidad', on_delete=models.PROTECT, related_name='medicos',
    )
    registro_medico = models.CharField(max_length=30, blank=True)
    activo = models.BooleanField(default=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Médico'
        verbose_name_plural = 'Médicos'

    def __str__(self):
        return f'{self.usuario.get_full_name()} - {self.especialidad}'


class CambioUsuarioLog(models.Model):
    """Fase 7, decisión 5: un registro por campo efectivamente modificado
    de datos personales (nunca uno por formulario enviado). Solo inserts,
    igual que citas.CitaLog — nunca se actualiza ni se borra una entrada.

    Dos limitaciones conocidas y aceptadas (fase 7.1, decisión 5), que se
    documentan en vez de resolverse:
    a. Solo registra lo que hacen las vistas de la aplicación (perfil,
       ficha del paciente, formulario de médicos). Una edición hecha desde
       el admin de Django no pasa por acá y queda sin auditar.
    b. El admin de Django es la única vía por la que se puede modificar
       `username` — la cédula — porque la aplicación nunca lo permite.
       Requiere superusuario, pero existe: es la única puerta por la que la
       regla "nadie edita el número de documento" se puede violar."""

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='cambios',
    )
    campo = models.CharField(max_length=50)
    valor_anterior = models.TextField(blank=True)
    valor_nuevo = models.TextField(blank=True)
    realizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+', null=True,
        help_text='Nulo cuando el cambio lo hizo un proceso automático, no una persona. '
                   'Cuando alguien edita sus propios datos, es esa misma persona, nunca nulo.',
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Registro de cambio de usuario'
        verbose_name_plural = 'Registros de cambio de usuario'
        ordering = ['fecha_creacion']

    def __str__(self):
        return f'{self.usuario} - {self.campo} ({self.fecha_creacion:%Y-%m-%d %H:%M})'
