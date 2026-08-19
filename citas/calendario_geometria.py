"""
Geometría del calendario: funciones puras, sin acceso a base de datos y sin
dependencias de Django. Traducen los algoritmos ya validados en el prototipo
(docs/prototipos/calendario.html) — empaquetar, limitarColumnas,
calcularVentana, dibujarCaja — que ahí viven en JS solo porque es un
prototipo. Acá son la implementación real: el layout queda dentro del
alcance de la suite de tests, y no hay una segunda copia de esta aritmética
en JavaScript que pueda desalinearse con esta.

Trabajan sobre cualquier objeto con la forma de EventoCalendario (tipo,
titulo, desplazamiento_min, inicio, fin, dia_completo, medico) sin importar
esa clase: mantiene este módulo desacoplado de citas/services.py y de
Django, para poder testearlo con objetos simples.
"""
from dataclasses import dataclass, replace

PX_POR_MIN = 1.1
# Debajo de esto (minutos de duración), la caja muestra solo el título: la
# hora y el médico se pierden visualmente, pero siguen en el atributo
# accesible de la caja y en el detalle. 35 en vez de 30 exactos porque el
# caso de 30 min (dos líneas de texto + padding) igual se siente apretado.
UMBRAL_COMPACTO_MIN = 35
# Por encima de esto, las columnas sobrantes de un cluster se agrupan en una
# caja-resumen "+N" en vez de seguir angostando cada caja hasta volverla
# ilegible.
MAX_COLUMNAS_VISIBLES = 3
# Ventana horaria mínima garantizada: aunque los datos de la semana sean
# angostos, la grilla nunca se achica más allá de esto, para que no cambie
# de forma drásticamente al navegar entre semanas.
VENTANA_MIN_INICIO = 7 * 60
VENTANA_MIN_FIN = 19 * 60
ALTO_MINIMO_PX = 18

PALETA_MEDICOS = (
    '#8E44AD', '#236296', '#1D2D50', '#B8860B',
    '#00838F', '#AD1457', '#5D4037', '#5B7A2E',
)


def color_medico(medico_pk):
    """Paleta de 8 colores asignada por índice determinista de la PK: sin
    campo nuevo, estable entre semanas y entre recargas — si el color
    cambiara dejaría de ser un identificador. La franja solo se usa en modo
    panorámico, donde nunca comparte caja con una cita (ahí solo hay
    bloqueos): la única restricción real es que los 8 se distingan entre sí
    y contra #727783 (bloqueo)."""
    return PALETA_MEDICOS[medico_pk % len(PALETA_MEDICOS)]


def _fin_min(evento):
    """Minuto-del-día en que termina el evento, derivado de la duración real
    (fin - inicio) sumada a desplazamiento_min — no de evento.fin.hour: son
    datetimes aware y desplazamiento_min ya es la fuente de verdad para el
    minuto de inicio en el resto del contrato."""
    duracion_min = int((evento.fin - evento.inicio).total_seconds() // 60)
    return evento.desplazamiento_min + duracion_min


@dataclass(frozen=True)
class Ventana:
    inicio_min: int
    fin_min: int


@dataclass(frozen=True)
class _Colocado:
    evento: object
    col: int
    cols: int
    cluster_id: int


@dataclass(frozen=True)
class _Resumen:
    ocultos: tuple
    col: int
    cols: int
    inicio_min: int
    fin_min: int
    cluster_id: int


@dataclass(frozen=True)
class CajaCalendario:
    """Envoltorio con la geometría ya calculada de un EventoCalendario (o de
    un grupo resumido, cuando se supera MAX_COLUMNAS_VISIBLES). El contrato
    EventoCalendario sigue frozen y sin campos de layout — esto es lo que
    consume el parcial de plantilla, nunca el dataclass del servicio."""
    tipo: str
    titulo: str
    evento: object = None  # None cuando es_resumen
    ocultos: tuple = ()
    # Pre-formateado como string, nunca un float crudo hacia la plantilla:
    # Django formatea números localizados con coma decimal en es-co
    # ("0,0px"), CSS inválido — todo lo que termine en un atributo style se
    # arma acá, en Python, con punto decimal garantizado.
    estilo: str = 'display:none;'
    compacta: bool = False
    capa_superior: bool = False
    es_resumen: bool = False
    color_medico: str | None = None


def calcular_ventana(eventos):
    """Mínimo/máximo real de los eventos, redondeado a la hora, con piso
    07:00-19:00. Los eventos de día completo no cuentan: uno solo forzaría
    la ventana a cubrir el día entero."""
    relevantes = [e for e in eventos if not e.dia_completo]
    if not relevantes:
        return Ventana(VENTANA_MIN_INICIO, VENTANA_MIN_FIN)

    inicio = min(e.desplazamiento_min for e in relevantes)
    fin = max(_fin_min(e) for e in relevantes)
    inicio = (inicio // 60) * 60
    fin = -(-fin // 60) * 60  # ceil a la hora sin importar float
    inicio = min(inicio, VENTANA_MIN_INICIO)
    fin = max(fin, VENTANA_MIN_FIN)
    return Ventana(inicio, fin)


def empaquetar(eventos):
    """
    Interval graph coloring greedy: agrupa eventos que se solapan en
    clusters (por orden de inicio) y asigna a cada uno la primera columna
    libre dentro de su cluster.

    Efecto conocido y aceptado, no a corregir: en una cadena de solapes
    encadenados (A con B, B con C, C con D, pero A y D no coinciden) todo el
    cluster comparte el mismo número de columnas, así que A y D quedan
    angostos aunque nunca se toquen en pantalla. Es el costo de un algoritmo
    greedy de una sola pasada.
    """
    ordenados = sorted(eventos, key=lambda e: (e.desplazamiento_min, _fin_min(e)))

    clusters = []
    actual = []
    fin_cluster = None
    for evento in ordenados:
        if actual and fin_cluster is not None and evento.desplazamiento_min >= fin_cluster:
            clusters.append(actual)
            actual = []
            fin_cluster = None
        actual.append(evento)
        fin_evento = _fin_min(evento)
        fin_cluster = fin_evento if fin_cluster is None else max(fin_cluster, fin_evento)
    if actual:
        clusters.append(actual)

    colocados = []
    for cluster_id, cluster in enumerate(clusters):
        columnas_fin = []
        asignaciones = []
        for evento in cluster:
            inicio = evento.desplazamiento_min
            col = next((i for i, fin_col in enumerate(columnas_fin) if fin_col <= inicio), None)
            if col is None:
                col = len(columnas_fin)
                columnas_fin.append(_fin_min(evento))
            else:
                columnas_fin[col] = _fin_min(evento)
            asignaciones.append((evento, col))
        cols = len(columnas_fin)
        colocados.extend(_Colocado(evento=e, col=c, cols=cols, cluster_id=cluster_id) for e, c in asignaciones)
    return colocados


def _etiqueta_resumen(ocultos):
    tipos = {e.tipo for e in ocultos}
    if len(tipos) == 1:
        tipo = next(iter(tipos))
        singular, plural = ('bloqueo', 'bloqueos') if tipo == 'bloqueo' else ('cita', 'citas')
    else:
        singular, plural = 'evento', 'eventos'
    n = len(ocultos)
    return f'+{n} {singular if n == 1 else plural}'


def limitar_columnas(colocados, max_columnas=MAX_COLUMNAS_VISIBLES):
    """Si un cluster necesita más columnas que `max_columnas` (varios
    médicos bloqueados a la misma hora, por ejemplo), las columnas sobrantes
    se agrupan en una sola caja-resumen en vez de seguir angostando cada
    caja hasta volverla ilegible. La altura de cada caja visible sigue
    representando su propia duración real: lo único que cambia es cuántas
    columnas caben lado a lado."""
    por_cluster = {}
    for colocado in colocados:
        por_cluster.setdefault(colocado.cluster_id, []).append(colocado)

    resultado = []
    for cluster_id, cluster in por_cluster.items():
        if cluster[0].cols <= max_columnas:
            resultado.extend(cluster)
            continue

        visibles = [c for c in cluster if c.col < max_columnas - 1]
        ocultos = [c for c in cluster if c.col >= max_columnas - 1]
        resultado.extend(replace(c, cols=max_columnas) for c in visibles)
        eventos_ocultos = tuple(c.evento for c in ocultos)
        resultado.append(_Resumen(
            ocultos=eventos_ocultos,
            col=max_columnas - 1,
            cols=max_columnas,
            inicio_min=min(c.evento.desplazamiento_min for c in ocultos),
            fin_min=max(_fin_min(c.evento) for c in ocultos),
            cluster_id=cluster_id,
        ))
    return resultado


def _posicionar(item, ventana, capa_superior, mostrar_medico):
    es_resumen = isinstance(item, _Resumen)
    inicio_min = item.inicio_min if es_resumen else item.evento.desplazamiento_min
    fin_min = item.fin_min if es_resumen else _fin_min(item.evento)

    inicio_visible = max(inicio_min, ventana.inicio_min)
    fin_visible = min(fin_min, ventana.fin_min)
    top_px = (inicio_visible - ventana.inicio_min) * PX_POR_MIN
    duracion = fin_visible - inicio_visible
    alto_px = max(duracion * PX_POR_MIN, ALTO_MINIMO_PX)

    ancho_pct = 100 / item.cols
    izq_pct = item.col * ancho_pct
    if capa_superior:
        # Inset para separar visualmente las cajas de esta capa entre sí.
        estilo_izquierda = f'calc({izq_pct:.4f}% + 3px)'
        estilo_ancho = f'calc({ancho_pct:.4f}% - 6px)'
    else:
        estilo_izquierda = f'{izq_pct:.4f}%'
        estilo_ancho = f'{ancho_pct:.4f}%'

    z_index = 10 if capa_superior else 1
    estilo_base = f'top:{top_px:.2f}px;height:{alto_px:.2f}px;left:{estilo_izquierda};width:{estilo_ancho};z-index:{z_index};'

    if es_resumen:
        return CajaCalendario(
            tipo='resumen', titulo=_etiqueta_resumen(item.ocultos),
            ocultos=item.ocultos, estilo=estilo_base,
            compacta=False, capa_superior=True, es_resumen=True,
        )

    evento = item.evento
    color = color_medico(evento.medico.pk) if mostrar_medico and evento.medico else None
    estilo = estilo_base + (f'border-left:4px solid {color};' if color else '')
    return CajaCalendario(
        tipo=evento.tipo, titulo=evento.titulo, evento=evento,
        estilo=estilo, compacta=duracion < UMBRAL_COMPACTO_MIN,
        capa_superior=capa_superior, color_medico=color,
    )


def generar_cajas_dia(eventos_dia, ventana, mostrar_medico=False):
    """
    Orquestador por día: dos pasadas separadas por capa (disponibilidad
    aparte del resto), para que una ocupación se dibuje encima y no lado a
    lado. Con las invariantes actuales del backend (disponibilidad nunca
    convive con nada del mismo médico) el caso vivo de la capa superior es
    bloqueo + cita del mismo médico solapados.
    """
    disponibilidad = [e for e in eventos_dia if e.tipo == 'disponibilidad']
    resto = [e for e in eventos_dia if e.tipo != 'disponibilidad']

    cajas = [_posicionar(c, ventana, False, mostrar_medico) for c in empaquetar(disponibilidad)]
    cajas += [
        _posicionar(c, ventana, True, mostrar_medico)
        for c in limitar_columnas(empaquetar(resto))
    ]
    return cajas
