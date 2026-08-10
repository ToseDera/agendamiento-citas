# pyrefly: ignore [missing-import]
from django import forms

from usuarios.forms import INPUT_CLASS

from .models import Especialidad


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
