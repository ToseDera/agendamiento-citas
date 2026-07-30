"""
Utilidad de testing: parsea el HTML real que devuelve una vista y construye
el dict de datos "tal como los enviaría un navegador" para hacer el POST de
vuelta. Pensada para tests de ida y vuelta (round-trip) que detectan
desalineaciones entre lo que el template produce y lo que el backend espera
(names renombrados, values con formato distinto al asumido, opciones de
<select> que no coinciden, etc.).

Asume que, dentro de una misma página, cada nombre de campo es único (no hay
dos <form> distintos reusando el mismo name) — cierto en las plantillas de
este proyecto.
"""
from html.parser import HTMLParser


class _FormHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.valores = {}
        self.selects = {}
        self._select_actual = None
        self._select_opciones = []
        self._textarea_actual = None
        self._textarea_buffer = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)

        if tag == 'input':
            name = attrs.get('name')
            if not name:
                return
            tipo = attrs.get('type', 'text')
            if tipo in ('checkbox', 'radio'):
                if 'checked' in attrs:
                    self.valores[name] = attrs.get('value', 'on')
            else:
                self.valores[name] = attrs.get('value', '')

        elif tag == 'select':
            self._select_actual = attrs.get('name')
            self._select_opciones = []

        elif tag == 'option':
            valor = attrs.get('value', '')
            if self._select_actual is not None:
                self._select_opciones.append(valor)
                if 'selected' in attrs:
                    self.valores[self._select_actual] = valor

        elif tag == 'textarea':
            self._textarea_actual = attrs.get('name')
            self._textarea_buffer = []
            if self._textarea_actual:
                self.valores[self._textarea_actual] = ''

    def handle_endtag(self, tag):
        if tag == 'select':
            if self._select_actual:
                self.selects[self._select_actual] = list(self._select_opciones)
                if self._select_actual not in self.valores and self._select_opciones:
                    self.valores[self._select_actual] = self._select_opciones[0]
            self._select_actual = None
            self._select_opciones = []
        elif tag == 'textarea':
            if self._textarea_actual:
                self.valores[self._textarea_actual] = ''.join(self._textarea_buffer)
            self._textarea_actual = None

    def handle_data(self, data):
        if self._textarea_actual is not None:
            self._textarea_buffer.append(data)


def datos_formulario_reales(html):
    """
    Devuelve {name: value} a partir del HTML: valor de cada <input> (los
    ocultos incluidos), la <option> marcada "selected" de cada <select> (o la
    primera si ninguna lo está, igual que un navegador), el contenido de cada
    <textarea>, y solo incluye checkboxes/radios que estén "checked".
    """
    parser = _FormHTMLParser()
    parser.feed(html)
    return parser.valores


def opciones_reales_de_select(html, name):
    """
    Devuelve los values no vacíos de las <option> del <select name="name">,
    en el orden en que el HTML las presenta. Útil para simular que un
    usuario elige explícitamente una opción real, en vez de reenviar el
    placeholder en blanco que algunos <select> traen seleccionado por
    defecto (ModelChoiceField con empty_label, o un ChoiceField sin default).
    """
    parser = _FormHTMLParser()
    parser.feed(html)
    return [valor for valor in parser.selects.get(name, []) if valor]
