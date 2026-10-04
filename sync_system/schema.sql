-- Run with:  psql -U postgres -f schema.sql
-- Creates the database and all tables used by the Offline Sync API.

CREATE DATABASE sync_system;
\c sync_system

CREATE TABLE users (
    user_id   SERIAL PRIMARY KEY,
    name      VARCHAR(100) NOT NULL,
    email     VARCHAR(150) NOT NULL UNIQUE,
    password  VARCHAR(255) NOT NULL,          -- stored as salted hash, never plain text
    phone     VARCHAR(20)
);

CREATE TABLE devices (
    device_id    SERIAL PRIMARY KEY,
    user_id      INT NOT NULL REFERENCES users(user_id),
    device_name  VARCHAR(100) NOT NULL
);

CREATE TABLE documents (
    document_id        SERIAL PRIMARY KEY,
    user_id            INT NOT NULL REFERENCES users(user_id),
    title              VARCHAR(200),
    content            TEXT,
    version            INT NOT NULL DEFAULT 1,
    updated_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_by_device  INT REFERENCES devices(device_id)
);

CREATE TABLE change_log (
    change_id       SERIAL PRIMARY KEY,
    document_id     INT NOT NULL REFERENCES documents(document_id),
    device_id       INT NOT NULL REFERENCES devices(device_id),
    base_version    INT NOT NULL,
    new_version     INT NOT NULL,
    changed_fields  JSONB NOT NULL,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Needed for APIs 10 and 11 (GET conflicts / RESOLVE conflict)
CREATE TABLE conflicts (
    conflict_id   SERIAL PRIMARY KEY,
    document_id   INT NOT NULL REFERENCES documents(document_id),
    device_id     INT NOT NULL REFERENCES devices(device_id),
    field         VARCHAR(50) NOT NULL,
    server_value  TEXT,
    client_value  TEXT,
    base_version  INT NOT NULL,
    status        VARCHAR(10) NOT NULL DEFAULT 'OPEN',   -- OPEN | RESOLVED
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at   TIMESTAMP
);

CREATE INDEX idx_change_log_doc_ver ON change_log(document_id, new_version);
CREATE INDEX idx_conflicts_doc      ON conflicts(document_id);
