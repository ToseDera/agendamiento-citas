-- Esquema de referencia para el diagrama entidad-relación (DOC-01).
-- Corresponde al estado real de los modelos tras la fase 6a.1 (no es DDL
-- generado automáticamente: se mantiene a mano, así que puede volver a
-- desincronizarse si el modelo cambia sin actualizar este archivo).

CREATE TABLE tipo_documento (
    id      SMALLSERIAL PRIMARY KEY,
    nombre  VARCHAR(50) NOT NULL UNIQUE,
    codigo  VARCHAR(10) NOT NULL UNIQUE
);

-- usuario = AUTH_USER_MODEL (usuarios.Usuario, hereda de
-- django.contrib.auth.AbstractUser): columnas propias de Django (password,
-- last_login, is_superuser, username, first_name, last_name, is_staff,
-- is_active, date_joined) + las que agrega este proyecto. username es la
-- cédula (decisión de negocio, no un tipo de columna distinto) y es el
-- identificador de login.
CREATE TABLE usuario (
    id                  BIGSERIAL PRIMARY KEY,
    password            VARCHAR(128) NOT NULL,
    last_login          TIMESTAMP,
    is_superuser        BOOLEAN NOT NULL DEFAULT FALSE,
    username            VARCHAR(150) NOT NULL UNIQUE,
    first_name          VARCHAR(150) NOT NULL DEFAULT '',
    last_name           VARCHAR(150) NOT NULL DEFAULT '',
    is_staff            BOOLEAN NOT NULL DEFAULT FALSE,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    date_joined         TIMESTAMP NOT NULL DEFAULT NOW(),
    tipo_documento_id   SMALLINT NOT NULL REFERENCES tipo_documento(id),
    numero_documento    VARCHAR(25) NOT NULL UNIQUE,
    fecha_nacimiento    DATE NOT NULL,
    telefono            VARCHAR(15) NOT NULL DEFAULT '',
    email               VARCHAR(254) NOT NULL UNIQUE
);

-- Roles: NO es una tabla propia "rol" con una columna usuario.rol_id — eso
-- nunca existió en el modelo real. El rol es la pertenencia a un Group de
-- django.contrib.auth ("Administrador", "Medico", "Paciente", sembrados en
-- usuarios/migrations/0002_catalogos_y_grupos_iniciales.py), a través de las
-- tablas que Django ya gestiona. Se documentan acá porque el proyecto
-- depende de ellas activamente para decidir el rol de cada usuario
-- (usuarios/decorators.py).
CREATE TABLE auth_group (
    id      SERIAL PRIMARY KEY,
    name    VARCHAR(150) NOT NULL UNIQUE
);

CREATE TABLE auth_user_groups (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES usuario(id) ON DELETE CASCADE,
    group_id    INTEGER NOT NULL REFERENCES auth_group(id) ON DELETE CASCADE,
    UNIQUE (user_id, group_id)
);

CREATE TABLE especialidad (
    id                  SMALLSERIAL PRIMARY KEY,
    nombre              VARCHAR(60) NOT NULL UNIQUE,
    descripcion         VARCHAR(255) NOT NULL DEFAULT '',
    duracion_cita_min   SMALLINT NOT NULL DEFAULT 30,
    activa              BOOLEAN NOT NULL DEFAULT TRUE,
    fecha_creacion      TIMESTAMP NOT NULL DEFAULT NOW(),
    fecha_actualizacion TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Catálogo de 4 filas: confirmada, cancelada, atendida (sembradas en
-- citas/migrations/0005_estados_cita_iniciales.py) y no atendida (agregada
-- en citas/migrations/0006_estado_no_atendida.py, fase 6a.1, HU-17: cierre
-- automático de citas confirmadas cuya hora de fin ya pasó).
CREATE TABLE estado_cita (
    id      SMALLSERIAL PRIMARY KEY,
    nombre  VARCHAR(20) NOT NULL UNIQUE
);

CREATE TABLE medico (
    id                  BIGSERIAL PRIMARY KEY,
    usuario_id          BIGINT NOT NULL UNIQUE REFERENCES usuario(id),
    especialidad_id     SMALLINT NOT NULL REFERENCES especialidad(id),
    registro_medico     VARCHAR(30) NOT NULL DEFAULT '',
    activo              BOOLEAN NOT NULL DEFAULT TRUE,
    fecha_creacion      TIMESTAMP NOT NULL DEFAULT NOW(),
    fecha_actualizacion TIMESTAMP NOT NULL DEFAULT NOW()
);
-- No hay ningún índice sobre especialidad_id en las migraciones reales: la
-- versión anterior de este documento listaba un idx_medico_especialidad que
-- nunca existió en la base.

CREATE TABLE horario_medico (
    id              BIGSERIAL PRIMARY KEY,
    medico_id       BIGINT NOT NULL REFERENCES medico(id) ON DELETE CASCADE,
    dia_semana      SMALLINT NOT NULL, -- 0=lunes..6=domingo; validado por choices en Django, SIN CheckConstraint en la base
    hora_inicio     TIME NOT NULL,
    hora_fin        TIME NOT NULL,
    fecha_creacion      TIMESTAMP NOT NULL DEFAULT NOW(),
    fecha_actualizacion TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_horario_medico_fin_mayor_inicio CHECK (hora_fin > hora_inicio),
    CONSTRAINT uq_horario_medico_dia_hora_inicio UNIQUE (medico_id, dia_semana, hora_inicio)
);

-- La duplicidad (mismo médico + fecha + hora_inicio + hora_fin) se valida en
-- ExcepcionHorario.clean(), a nivel aplicación. No hay ninguna
-- UniqueConstraint sobre esta tabla en las migraciones reales — a
-- diferencia de lo que decía la versión anterior de este documento.
CREATE TABLE excepcion_horario (
    id              BIGSERIAL PRIMARY KEY,
    medico_id       BIGINT NOT NULL REFERENCES medico(id) ON DELETE CASCADE,
    fecha           DATE NOT NULL,
    hora_inicio     TIME,
    hora_fin        TIME,
    motivo          VARCHAR(120) NOT NULL DEFAULT '',
    fecha_creacion  TIMESTAMP NOT NULL DEFAULT NOW()
);

-- ocupa_slot se deriva de estado en Cita.save() (False solo cuando
-- estado=cancelada; "no atendida" también ocupa el slot, igual que
-- confirmada y atendida — no libera el turno). Es la columna que sostiene
-- la restricción de abajo.
CREATE TABLE cita (
    id                  BIGSERIAL PRIMARY KEY,
    paciente_id         BIGINT NOT NULL REFERENCES usuario(id),
    medico_id           BIGINT NOT NULL REFERENCES medico(id),
    estado_cita_id      SMALLINT NOT NULL REFERENCES estado_cita(id),
    fecha               DATE NOT NULL,
    hora_inicio         TIME NOT NULL,
    hora_fin            TIME NOT NULL,
    motivo_consulta     VARCHAR(500) NOT NULL DEFAULT '',
    comentario_medico   TEXT NOT NULL DEFAULT '',
    ocupa_slot          BOOLEAN NOT NULL DEFAULT TRUE,
    fecha_creacion      TIMESTAMP NOT NULL DEFAULT NOW(),
    fecha_actualizacion TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_cita_fin_mayor_inicio CHECK (hora_fin > hora_inicio)
);

-- No es una UNIQUE constraint de tabla (esas no admiten WHERE en Postgres):
-- es un índice único PARCIAL, solo sobre las citas que ocupan el turno. Una
-- cita cancelada (ocupa_slot=False) no cuenta para esta restricción — así
-- es como el sistema permite reagendar sobre un slot liberado por una
-- cancelación. La versión anterior de este documento la mostraba como una
-- UNIQUE simple, lo cual habría impedido esa reutilización.
CREATE UNIQUE INDEX uq_cita_medico_slot_activo ON cita (medico_id, fecha, hora_inicio) WHERE ocupa_slot;

-- realizado_por_id admite NULL desde
-- citas/migrations/0007_citalog_realizado_por_nullable.py (fase 6a.1): nulo
-- significa que la transición la hizo un proceso automático
-- (citas.services.cerrar_citas_vencidas, el cierre de citas vencidas de
-- HU-17), no una persona autenticada.
CREATE TABLE cita_log (
    id                  BIGSERIAL PRIMARY KEY,
    cita_id             BIGINT NOT NULL REFERENCES cita(id),
    estado_cita_id      SMALLINT NOT NULL REFERENCES estado_cita(id),
    accion              VARCHAR(20) NOT NULL,
    realizado_por_id    BIGINT REFERENCES usuario(id),
    detalle             VARCHAR(255) NOT NULL DEFAULT '',
    fecha_creacion      TIMESTAMP NOT NULL DEFAULT NOW()
);

-- NO IMPLEMENTADA TODAVÍA. La app "notificaciones" está en INSTALLED_APPS
-- pero notificaciones/models.py está vacío (sin modelos, sin migraciones):
-- esta tabla es diseño futuro, no el esquema actual. Se deja comentada a
-- propósito para que no se cuele como tabla real en el diagrama
-- entidad-relación de DOC-01 mientras siga sin implementarse.
-- CREATE TABLE notificacion (
--     id              BIGSERIAL PRIMARY KEY,
--     usuario_id      BIGINT NOT NULL REFERENCES usuario(id) ON DELETE CASCADE,
--     cita_id         BIGINT REFERENCES cita(id) ON DELETE SET NULL,
--     titulo          VARCHAR(100) NOT NULL,
--     mensaje         VARCHAR(500) NOT NULL,
--     leida           BOOLEAN NOT NULL DEFAULT FALSE,
--     fecha_creacion  TIMESTAMP NOT NULL DEFAULT NOW()
-- );

CREATE INDEX idx_cita_medico_fecha ON cita(medico_id, fecha);
CREATE INDEX idx_cita_paciente_fecha ON cita(paciente_id, fecha);
