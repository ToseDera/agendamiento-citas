# MediClick

Sistema de agendación de citas médicas. Backend en Django 6, base de datos PostgreSQL.

## Requisitos

- Python 3.13+ (se desarrolló con 3.14)
- PostgreSQL corriendo localmente (por ejemplo con [DBngin](https://dbngin.com/))
- Git

## 1. Clonar el repositorio

```bash
git clone <url-del-repo>
cd proyectoPython
```

## 2. Crear y activar el entorno virtual

El `venv/` no viaja en el repo (está en `.gitignore`), cada quien crea el suyo.

**Windows (PowerShell / Git Bash):**

```bash
python -m venv venv
source venv/Scripts/activate   # Git Bash
# o: venv\Scripts\Activate.ps1  # PowerShell
```

**macOS / Linux:**

```bash
python -m venv venv
source venv/bin/activate
```

## 3. Instalar dependencias

```bash
pip install -r requirements.txt
```

## 4. Variables de entorno

El archivo `.env` tampoco viaja en el repo. Copia `.env.example` como `.env` en la raíz del proyecto (junto a `manage.py`) y ajusta usuario/contraseña a tu instalación local de PostgreSQL:

```bash
cp .env.example .env   # Windows (Git Bash) / macOS / Linux
```

```env
SECRET_KEY=pon-aqui-una-clave-secreta-cualquiera-para-desarrollo
DB_NAME=agendamientoCitas
DB_USER=postgres
DB_PASSWORD=tu_password
DB_HOST=localhost
DB_PORT=5432
```

La base de datos (`DB_NAME`) debe existir antes de migrar. Créala desde tu cliente de PostgreSQL (pgAdmin, DBngin, `psql`, etc.) o con:

```bash
python -c "
import os, psycopg2
from dotenv import load_dotenv
load_dotenv()
conn = psycopg2.connect(dbname='postgres', user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'), host=os.getenv('DB_HOST','localhost'), port=os.getenv('DB_PORT','5432'))
conn.autocommit = True
cur = conn.cursor()
cur.execute(f'CREATE DATABASE \"{os.getenv(\"DB_NAME\")}\"')
"
```

## 5. Migraciones

```bash
python manage.py migrate
```

Esto crea las tablas y, además, siembra automáticamente (vía migración de datos):

- Catálogo `TipoDocumento`: Cédula de Ciudadanía (CC), Tarjeta de Identidad (TI), Cédula de Extranjería (CE), Permiso Especial (PPT), Pasaporte (PA).
- Grupos de Django: `Administrador`, `Medico`, `Paciente`.

## 6. Crear un superusuario

### Opción rápida (recomendada para desarrollo): `seed_dev`

```bash
python manage.py seed_dev
```

Comando idempotente (correrlo varias veces no duplica nada) que solo funciona con `DEBUG=True`. Crea:

| Rol                                                              | Cédula (username) | Contraseña     |
| ----------------------------------------------------------------- | ------------------- | --------------- |
| Administrador (superusuario, grupo `Administrador`)             | `9999999999`      | `admin123`    |
| Paciente de prueba 1 (grupo `Paciente`)                         | `8888888888`      | `paciente123` |
| Paciente de prueba 2 (grupo `Paciente`)                         | `8888888880`      | `paciente123` |
| Médico de prueba (grupo `Medico`, Medicina general)            | `7777777777`      | `medico123`   |
| Médico de prueba (grupo `Medico`, Odontología)                 | `7777777771`      | `medico123`   |
| Médico de prueba (grupo `Medico`, Pediatría)                   | `7777777772`      | `medico123`   |

Hay un médico de prueba por cada especialidad sembrada (`citas/migrations/0003_especialidades_iniciales.py`); si agregás una especialidad nueva, sumale también su médico en `usuarios/management/commands/seed_dev.py` (lista `MEDICOS`).

Usa `python manage.py seed_dev --sin-extra` si solo quieres el admin, sin los pacientes ni los médicos de prueba.

### Opción manual: `createsuperuser`

```bash
python manage.py createsuperuser
```

Te pedirá, entre otros, dos campos que no son obvios en consola:

- **Tipo documento**: pide el **id** del `TipoDocumento`, no el nombre. Consulta los ids reales (no asumas un orden fijo) con:
  ```bash
  python manage.py shell -c "from usuarios.models import TipoDocumento; [print(t.pk, t.codigo, t.nombre) for t in TipoDocumento.objects.all()]"
  ```
- **Fecha de nacimiento**: formato `YYYY-MM-DD` (ej. `1990-05-10`).

Con `createsuperuser` el usuario queda como superusuario de Django, pero **no** entra automáticamente al grupo `Administrador` (que es lo que controla el acceso al panel `/panel/...` de la app) — únelo manualmente desde `/admin/` o por shell si necesitas ver el panel.

## 7. Correr el servidor

```bash
python manage.py runserver
```

- App: http://127.0.0.1:8000/
- Admin: http://127.0.0.1:8000/admin/
- Login: http://127.0.0.1:8000/cuentas/login/
- Registro: http://127.0.0.1:8000/registro/

El registro público de usuarios siempre crea el usuario en el grupo **Paciente** (server-side, no seleccionable desde el formulario). El `username` del usuario es su número de documento (cédula).

## 8. Correr los tests

El proyecto tiene 210 tests (`citas` + `usuarios`). Casi todo el tiempo de una
corrida sin acelerar se va en PBKDF2 hasheando las contraseñas de los
usuarios que los tests crean — no en lógica de negocio — así que hay formas
mucho más rápidas de correrlos día a día sin perder cobertura. Tres formas,
según el momento:

### Durante el desarrollo: una clase o un módulo

Cuando estás iterando sobre algo puntual, corré solo lo que te importa:

```bash
python manage.py test citas.tests.CerrarCitasVencidasTests
python manage.py test usuarios.tests
```

### Corrida rápida completa: con aceleración

Para correr los 210 tests seguido sin esperar varios minutos:

```bash
python manage.py test --settings=config.settings_test
```

- `--settings=config.settings_test` usa un hasher de contraseñas rápido
  (MD5) solo para esta corrida — ver `config/settings_test.py`. Nunca toca
  `config/settings.py`: la app en desarrollo/producción sigue usando PBKDF2
  siempre. Es la optimización que más rinde, por lejos: ~10-18s para los
  210 tests (contra varios minutos con el hasher real).
- **No sumes `--parallel` a esto.** Se midió la combinación (fase 6b): con
  la suite ya en 10-18s gracias al hasher rápido, el costo de levantar
  procesos y crear una base de datos de test por proceso supera lo que
  ahorra paralelizar — la misma corrida con `--parallel` tomó ~24-25s,
  más lenta que sin él. `--parallel` sí vale la pena combinado con el
  hasher *normal* (PBKDF2), donde el cuello de botella es otro; combinado
  con el hasher rápido, no.

### Corrida de cierre de fase: completa, tal como se reporta

Antes de dar por cerrada una fase (o para el número que va en un reporte de
fase), corré la suite completa sin acelerar nada:

```bash
python manage.py test
```

Es la única corrida que debería citarse como "resultado de la suite": mide
el caso real (hasher de producción, sin paralelizar), no un número
optimista.

### `--keepdb`: con dos advertencias

`--keepdb` reutiliza la base de test entre corridas en vez de recrearla
desde cero. Dos advertencias reales de este proyecto antes de usarla:

- **No sirve si la fase agrega migraciones nuevas**: correría contra un
  esquema viejo.
- **No la reutilices en corridas consecutivas**: `usuarios.tests.
  MigracionTiposDocumentoTests` (el único test que usa `TransactionTestCase`,
  porque mueve migraciones reales hacia atrás y hacia adelante con
  `MigrationExecutor`) deja el catálogo sembrado (`TipoDocumento`,
  `Especialidad`, etc.) corrupto en la base preservada después de correr.
  Reutilizar esa misma base en una segunda corrida con `--keepdb` hace
  fallar ~190 de los 210 tests con `DoesNotExist` — no es un bug de esos
  tests, es la base preservada la que quedó mal. Si usás `--keepdb`, que
  sea para una sola corrida; después dejá que la siguiente la recree
  (`python manage.py test --noinput`, sin `--keepdb`).

## Reiniciar el entorno desde cero

Cuando necesites dejar la base de datos limpia (por ejemplo tras un cambio grande de modelos), el flujo completo es:

```bash
# 1. Recrear la BD (ver el bloque de creación de BD en el paso 4; agrega un DROP DATABASE antes del CREATE)
python -c "
import os, psycopg2
from dotenv import load_dotenv
load_dotenv()
db_name = os.getenv('DB_NAME')
conn = psycopg2.connect(dbname='postgres', user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'), host=os.getenv('DB_HOST','localhost'), port=os.getenv('DB_PORT','5432'))
conn.autocommit = True
cur = conn.cursor()
cur.execute(f'DROP DATABASE IF EXISTS \"{db_name}\" WITH (FORCE)')
cur.execute(f'CREATE DATABASE \"{db_name}\"')
"

# 2. Migrar (crea tablas + catálogos + grupos + especialidades iniciales)
python manage.py migrate

# 3. Sembrar datos de desarrollo (admin + pacientes + médicos de prueba, ver sección 6)
python manage.py seed_dev

# 4. Levantar el servidor
python manage.py runserver
```

Después de estos 4 pasos el entorno queda listo para probar login, registro y el panel de gestión (`/panel/`) con las credenciales de la tabla del paso 6.

## Estructura relevante

```
config/            # settings, urls, wsgi/asgi
usuarios/          # modelo de usuario custom (AUTH_USER_MODEL), auth, registro, panel de médicos, seed_dev
citas/             # especialidades, médicos (horario), panel de especialidades
notificaciones/    # (pendiente de implementar)
templates/         # base.html + registration/{login,registro}.html + panel/
static/            # estáticos (imágenes, etc.)
docs/esquema_citas_v1.sql   # referencia de diseño de BD, NO ejecutar directo
```
