def roles(request):
    """Expone es_admin/es_medico/es_paciente a todas las plantillas, según
    los grupos de Django del usuario autenticado (Administrador/Medico/Paciente).
    """
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return {'es_admin': False, 'es_medico': False, 'es_paciente': False}

    grupos = set(user.groups.values_list('name', flat=True))
    return {
        'es_admin': 'Administrador' in grupos,
        'es_medico': 'Medico' in grupos,
        'es_paciente': 'Paciente' in grupos,
    }
