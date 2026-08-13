from django import forms
from django.core.exceptions import ValidationError

from usuarios.forms import INPUT_CLASS, _copiar_errores_de_validacion

from .models import Cita, Especialidad, ExcepcionHorario


class EspecialidadForm(forms.ModelForm):
    class Meta:
        model = Especialidad
        fields = ('nombre', 'descripcion', 'duracion_cita_min', 'activa')
        widgets = {
            'nombre': forms.TextInput(attrs={'class': INPUT_CLASS}),
            'descripcion': forms.TextInput(attrs={'class': INPUT_CLASS}),
            'duracion_cita_min': forms.NumberInput(attrs={'class': INPUT_CLASS, 'min': 10, 'max': 120}),
            'activa': forms.CheckboxInput(attrs={'class': 'peer sr-only'}),
        }
        labels = {
            'nombre': 'Nombre',
            'descripcion': 'Descripción',
            'duracion_cita_min': 'Duración de la cita (minutos)',
            'activa': 'Activa',
        }

    def clean_nombre(self):
        nombre = self.cleaned_data['nombre']
        existe = Especialidad.objects.filter(nombre__iexact=nombre).exclude(pk=self.instance.pk)
        if existe.exists():
            raise forms.ValidationError('Ya existe una especialidad con este nombre.')
        return nombre


class ConfirmarCitaForm(forms.Form):
    fecha = forms.DateField(widget=forms.HiddenInput())
    hora_inicio = forms.TimeField(widget=forms.HiddenInput())
    motivo_consulta = forms.CharField(
        max_length=500, required=False, label='Motivo de la consulta',
        widget=forms.Textarea(attrs={'class': INPUT_CLASS, 'rows': 3}),
    )


class ComentarioMedicoForm(forms.ModelForm):
    """Decisión 5: solo el médico dueño de la cita la usa, y solo en citas
    atendidas. La vista (medico_comentario) es la que hace cumplir eso —
    este form no sabe nada de quién lo está enviando."""

    class Meta:
        model = Cita
        fields = ('comentario_medico',)
        widgets = {
            'comentario_medico': forms.Textarea(attrs={'class': INPUT_CLASS, 'rows': 5}),
        }
        labels = {'comentario_medico': 'Comentario / resumen de la consulta'}


class ExcepcionHorarioForm(forms.ModelForm):
    """Bloqueo puntual o de día completo del horario de un médico (HU sin
    número de la fase 5: el modelo ExcepcionHorario no tenía interfaz)."""

    class Meta:
        model = ExcepcionHorario
        fields = ('fecha', 'hora_inicio', 'hora_fin', 'motivo')
        widgets = {
            'fecha': forms.DateInput(attrs={'class': INPUT_CLASS, 'type': 'date'}),
            'hora_inicio': forms.TimeInput(attrs={'class': INPUT_CLASS, 'type': 'time'}),
            'hora_fin': forms.TimeInput(attrs={'class': INPUT_CLASS, 'type': 'time'}),
            'motivo': forms.TextInput(attrs={
                'class': INPUT_CLASS, 'placeholder': 'Ej. Vacaciones, incapacidad...',
            }),
        }
        labels = {
            'fecha': 'Fecha',
            'hora_inicio': 'Hora de inicio (vacío = día completo)',
            'hora_fin': 'Hora de fin (vacío = día completo)',
            'motivo': 'Motivo',
        }

    def __init__(self, *args, medico=None, **kwargs):
        self.medico = medico
        super().__init__(*args, **kwargs)
        self.fields['hora_inicio'].required = False
        self.fields['hora_fin'].required = False
        self.fields['motivo'].required = False

    def clean(self):
        cleaned_data = super().clean()
        self.instance.medico = self.medico
        self.instance.fecha = cleaned_data.get('fecha')
        self.instance.hora_inicio = cleaned_data.get('hora_inicio')
        self.instance.hora_fin = cleaned_data.get('hora_fin')
        try:
            self.instance.clean()
        except ValidationError as error:
            _copiar_errores_de_validacion(self, error)
        return cleaned_data

    def save(self, commit=True):
        self.instance.medico = self.medico
        return super().save(commit=commit)
