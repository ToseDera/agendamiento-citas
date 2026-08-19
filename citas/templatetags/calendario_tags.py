from django import template

from citas.calendario_geometria import color_medico as _color_medico

register = template.Library()


@register.filter
def color_medico(medico_pk):
    return _color_medico(medico_pk)
