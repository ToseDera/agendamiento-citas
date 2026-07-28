CREATE TABLE rol (
    id          SMALLSERIAL PRIMARY KEY,
    nombre      VARCHAR(30) NOT NULL UNIQUE
);

CREATE TABLE usuario (
    id                  BIGSERIAL PRIMARY KEY,
    rol_id              SMALLINT NOT NULL REFERENCES rol(id),
    nombre              VARCHAR(50) NOT NULL,
    apellido            VARCHAR(50) NOT NULL,
    tipo_documento_id   SMALLINT NOT NULL REFERENCES tipo_documento(id),
    numero_documento    VARCHAR(25) NOT NULL,
    fecha_nacimiento    DATE NOT NULL,
    correo              VARCHAR(254) NOT NULL UNIQUE,
    telefono            VARCHAR(15),
    contrasena_hash     VARCHAR(128) NOT NULL,
    activo              BOOLEAN NOT NULL DEFAULT TRUE,
    fecha_creacion      TIMESTAMP NOT NULL DEFAULT NOW(),
    fecha_actualizacion TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (tipo_documento_id, numero_documento)
);

CREATE TABLE tipo_documento (
    id      SMALLSERIAL PRIMARY KEY,
    nombre  VARCHAR(50) NOT NULL UNIQUE
);

CREATE TABLE especialidad (
    id          SMALLSERIAL PRIMARY KEY,
    nombre      VARCHAR(60) NOT NULL UNIQUE,
    descripcion VARCHAR(255),
    duracion_cita_min SMALLINT NOT NULL DEFAULT 30,
    activa      BOOLEAN NOT NULL DEFAULT TRUE,
    fecha_creacion      TIMESTAMP NOT NULL DEFAULT NOW(),
    fecha_actualizacion TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE estado_cita (
    id      SMALLSERIAL PRIMARY KEY,
    nombre  VARCHAR(20) NOT NULL UNIQUE
);

CREATE TABLE medico (
    id                  BIGSERIAL PRIMARY KEY,
    usuario_id          BIGINT NOT NULL UNIQUE REFERENCES usuario(id),
    especialidad_id     SMALLINT NOT NULL REFERENCES especialidad(id),
    registro_medico     VARCHAR(30),
    activo              BOOLEAN NOT NULL DEFAULT TRUE,
    fecha_creacion      TIMESTAMP NOT NULL DEFAULT NOW(),
    fecha_actualizacion TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE horario_medico (
    id              BIGSERIAL PRIMARY KEY,
    medico_id       BIGINT NOT NULL REFERENCES medico(id) ON DELETE CASCADE,
    dia_semana      SMALLINT NOT NULL CHECK (dia_semana BETWEEN 0 AND 6),
    hora_inicio     TIME NOT NULL,
    hora_fin        TIME NOT NULL,
    fecha_creacion      TIMESTAMP NOT NULL DEFAULT NOW(),
    fecha_actualizacion TIMESTAMP NOT NULL DEFAULT NOW(),
    CHECK (hora_fin > hora_inicio),
    UNIQUE (medico_id, dia_semana, hora_inicio)
);

CREATE TABLE excepcion_horario (
    id              BIGSERIAL PRIMARY KEY,
    medico_id       BIGINT NOT NULL REFERENCES medico(id) ON DELETE CASCADE,
    fecha           DATE NOT NULL,
    hora_inicio     TIME,
    hora_fin        TIME,
    motivo          VARCHAR(120),
    fecha_creacion  TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (medico_id, fecha, hora_inicio)
);

CREATE TABLE cita (
    id                  BIGSERIAL PRIMARY KEY,
    paciente_id         BIGINT NOT NULL REFERENCES usuario(id),
    medico_id           BIGINT NOT NULL REFERENCES medico(id),
    estado_cita_id      SMALLINT NOT NULL REFERENCES estado_cita(id),
    fecha               DATE NOT NULL,
    hora_inicio         TIME NOT NULL,
    hora_fin            TIME NOT NULL,
    motivo_consulta     VARCHAR(500),
    comentario_medico   TEXT,
    fecha_creacion      TIMESTAMP NOT NULL DEFAULT NOW(),
    fecha_actualizacion TIMESTAMP NOT NULL DEFAULT NOW(),
    CHECK (hora_fin > hora_inicio),
    CONSTRAINT uq_medico_slot UNIQUE (medico_id, fecha, hora_inicio)
);

CREATE TABLE cita_log (
    id              BIGSERIAL PRIMARY KEY,
    cita_id         BIGINT NOT NULL REFERENCES cita(id),
    estado_cita_id  SMALLINT NOT NULL REFERENCES estado_cita(id),
    accion          VARCHAR(20) NOT NULL,
    realizado_por_id BIGINT NOT NULL REFERENCES usuario(id),
    detalle         VARCHAR(255),
    fecha_creacion  TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE notificacion (
    id              BIGSERIAL PRIMARY KEY,
    usuario_id      BIGINT NOT NULL REFERENCES usuario(id) ON DELETE CASCADE,
    cita_id         BIGINT REFERENCES cita(id) ON DELETE SET NULL,
    titulo          VARCHAR(100) NOT NULL,
    mensaje         VARCHAR(500) NOT NULL,
    leida           BOOLEAN NOT NULL DEFAULT FALSE,
    fecha_creacion  TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_cita_paciente ON cita(paciente_id, fecha);
CREATE INDEX idx_cita_medico_fecha ON cita(medico_id, fecha);
CREATE INDEX idx_medico_especialidad ON medico(especialidad_id) WHERE activo = TRUE;
CREATE INDEX idx_notificacion_usuario ON notificacion(usuario_id) WHERE leida = FALSE;
