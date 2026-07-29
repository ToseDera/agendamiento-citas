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
            'activa': forms.CheckboxInput(attrs={'class': 'h-5 w-5 rounded border-outline-variant text-primary'}),
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
