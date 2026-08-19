from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

ESTADO_CANCELADA = 'cancelada'


class Especialidad(models.Model):
    nombre = models.CharField(max_length=60, unique=True)
    descripcion = models.CharField(max_length=255, blank=True)
    duracion_cita_min = models.PositiveSmallIntegerField(
        default=30, validators=[MinValueValidator(10), MaxValueValidator(120)],
    )
    activa = models.BooleanField(default=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Especialidad'
        verbose_name_plural = 'Especialidades'
        ordering = ['nombre']

    def __str__(self):
        return self.nombre


DIAS_SEMANA = [
    (0, 'Lunes'),
    (1, 'Martes'),
    (2, 'Miércoles'),
    (3, 'Jueves'),
    (4, 'Viernes'),
    (5, 'Sábado'),
    (6, 'Domingo'),
]


class HorarioMedico(models.Model):
    medico = models.ForeignKey(
        'usuarios.Medico', on_delete=models.CASCADE, related_name='horarios',
    )
    dia_semana = models.PositiveSmallIntegerField(choices=DIAS_SEMANA)
    hora_inicio = models.TimeField()
    hora_fin = models.TimeField()
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Horario de médico'
        verbose_name_plural = 'Horarios de médico'
        ordering = ['dia_semana', 'hora_inicio']
        constraints = [
            models.UniqueConstraint(
                fields=['medico', 'dia_semana', 'hora_inicio'],
                name='uq_horario_medico_dia_hora_inicio',
            ),
            models.CheckConstraint(
                condition=models.Q(hora_fin__gt=models.F('hora_inicio')),
                name='ck_horario_medico_fin_mayor_inicio',
            ),
        ]

    def __str__(self):
        return f'{self.medico} - {self.get_dia_semana_display()} {self.hora_inicio}-{self.hora_fin}'

    def clean(self):
        super().clean()
        if self.hora_inicio and self.hora_fin and self.hora_fin <= self.hora_inicio:
            raise ValidationError({'hora_fin': 'La hora de fin debe ser posterior a la hora de inicio.'})

        if self.medico_id and self.dia_semana is not None and self.hora_inicio and self.hora_fin:
            solapados = HorarioMedico.objects.filter(
                medico_id=self.medico_id, dia_semana=self.dia_semana,
                hora_inicio__lt=self.hora_fin, hora_fin__gt=self.hora_inicio,
            ).exclude(pk=self.pk)
            if solapados.exists():
                raise ValidationError(
                    'Este bloque se solapa con otro horario ya registrado para el médico ese día.',
                )


class EstadoCita(models.Model):
    nombre = models.CharField(max_length=20, unique=True)

    class Meta:
        verbose_name = 'Estado de cita'
        verbose_name_plural = 'Estados de cita'

    def __str__(self):
        return self.nombre


class ExcepcionHorario(models.Model):
    medico = models.ForeignKey(
        'usuarios.Medico', on_delete=models.CASCADE, related_name='excepciones',
    )
    fecha = models.DateField()
    hora_inicio = models.TimeField(null=True, blank=True)
    hora_fin = models.TimeField(null=True, blank=True)
    motivo = models.CharField(max_length=120, blank=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Excepción de horario'
        verbose_name_plural = 'Excepciones de horario'
        ordering = ['fecha', 'hora_inicio']

    def __str__(self):
        if self.hora_inicio and self.hora_fin:
            return f'{self.medico} - {self.fecha} {self.hora_inicio}-{self.hora_fin}'
        return f'{self.medico} - {self.fecha} (día completo)'

    def clean(self):
        super().clean()
        if bool(self.hora_inicio) != bool(self.hora_fin):
            raise ValidationError(
                'Indica ambas horas (inicio y fin) o déjalas vacías para bloquear el día completo.',
            )
        if self.hora_inicio and self.hora_fin and self.hora_fin <= self.hora_inicio:
            raise ValidationError({'hora_fin': 'La hora de fin debe ser posterior a la hora de inicio.'})

        if self.medico_id and self.fecha:
            duplicada = ExcepcionHorario.objects.filter(
                medico_id=self.medico_id, fecha=self.fecha,
                hora_inicio=self.hora_inicio, hora_fin=self.hora_fin,
            ).exclude(pk=self.pk)
            if duplicada.exists():
                raise ValidationError('Ya existe una excepción idéntica para ese médico y fecha.')


class Cita(models.Model):
    """
    ocupa_slot se deriva de `estado` dentro de save() (ver abajo) y sostiene
    la UniqueConstraint parcial `uq_cita_medico_slot_activo`, que es lo que
    permite reusar el slot de una cita cancelada.

    Por eso, cualquier transición de estado DEBE pasar por
    citas.services.cambiar_estado_cita() (que llama a cita.save()) y NUNCA
    por Cita.objects.filter(...).update(estado=...) ni bulk_update(): esos
    dos bypasean save() y dejan ocupa_slot desincronizado con estado,
    rompiendo la constraint silenciosamente (la fila queda "cancelada" pero
    sigue bloqueando el slot, o viceversa).
    """

    paciente = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='citas_paciente',
    )
    medico = models.ForeignKey(
        'usuarios.Medico', on_delete=models.PROTECT, related_name='citas',
    )
    estado = models.ForeignKey(EstadoCita, on_delete=models.PROTECT, related_name='citas')
    fecha = models.DateField()
    hora_inicio = models.TimeField()
    hora_fin = models.TimeField()
    motivo_consulta = models.CharField(max_length=500, blank=True)
    comentario_medico = models.TextField(blank=True)
    ocupa_slot = models.BooleanField(
        default=True,
        help_text='Derivado del estado: False cuando la cita está cancelada, para liberar el slot.',
    )
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Cita'
        verbose_name_plural = 'Citas'
        ordering = ['fecha', 'hora_inicio']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(hora_fin__gt=models.F('hora_inicio')),
                name='ck_cita_fin_mayor_inicio',
            ),
            models.UniqueConstraint(
                fields=['medico', 'fecha', 'hora_inicio'],
                condition=models.Q(ocupa_slot=True),
                name='uq_cita_medico_slot_activo',
            ),
        ]
        indexes = [
            models.Index(fields=['medico', 'fecha'], name='idx_cita_medico_fecha'),
            models.Index(fields=['paciente', 'fecha'], name='idx_cita_paciente_fecha'),
        ]

    def __str__(self):
        return f'{self.paciente} con {self.medico} - {self.fecha} {self.hora_inicio}'

    def save(self, *args, **kwargs):
        self.ocupa_slot = self.estado.nombre != ESTADO_CANCELADA
        super().save(*args, **kwargs)


class CitaLog(models.Model):
    cita = models.ForeignKey(Cita, on_delete=models.PROTECT, related_name='logs')
    estado = models.ForeignKey(EstadoCita, on_delete=models.PROTECT, related_name='+')
    accion = models.CharField(max_length=20)
    realizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+', null=True,
        help_text='Nulo cuando la transición la hizo un proceso automático (ver citas.services.cerrar_citas_vencidas), no una persona.',
    )
    detalle = models.CharField(max_length=255, blank=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Registro de cita'
        verbose_name_plural = 'Registros de cita'
        ordering = ['fecha_creacion']

    def __str__(self):
        return f'{self.cita} - {self.accion} ({self.fecha_creacion:%Y-%m-%d %H:%M})'
