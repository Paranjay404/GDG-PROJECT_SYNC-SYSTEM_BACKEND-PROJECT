import json
import os
from contextlib import contextmanager
from typing import Optional

import psycopg2
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI(title="Offline Sync API")

# Configure through environment variables (no hard-coded passwords).
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "database": os.getenv("DB_NAME", "sync_system"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", "1234"),
}

ALLOWED_FIELDS = {"title", "content"}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
@contextmanager
def db():
    """Yield a cursor; commit on success, rollback on error, always close."""
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        cur = conn.cursor()
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def as_dict(value):
    return json.loads(value) if isinstance(value, str) else value


def validate_changes(changes: dict):
    if not changes:
        raise HTTPException(400, "changes must not be empty")
    bad = set(changes) - ALLOWED_FIELDS
    if bad:
        raise HTTPException(400, f"Unknown field(s): {sorted(bad)}")


def require_device(cur, device_id: int):
    cur.execute("SELECT 1 FROM devices WHERE device_id = %s", (device_id,))
    if cur.fetchone() is None:
        raise HTTPException(404, "Device not found")


def lock_document(cur, document_id: int):
    """Row-lock the document so two devices can't bump the same version."""
    cur.execute(
        """
        SELECT title, content, version FROM documents
        WHERE document_id = %s FOR UPDATE
        """,
        (document_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise HTTPException(404, "Document not found")
    return row


def write_new_version(cur, document_id, device_id, base_version, server_version,
                      title, content, changes):
    new_version = server_version + 1
    cur.execute(
        """
        UPDATE documents
        SET title = %s, content = %s, version = %s,
            updated_by_device = %s, updated_at = CURRENT_TIMESTAMP
        WHERE document_id = %s
        """,
        (title, content, new_version, device_id, document_id),
    )
    cur.execute(
        """
        INSERT INTO change_log
            (document_id, device_id, base_version, new_version, changed_fields)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (document_id, device_id, base_version, new_version, json.dumps(changes)),
    )
    return new_version


# --------------------------------------------------------------------------
# request models
# --------------------------------------------------------------------------
class User(BaseModel):
    name: str
    email: str
    password: str
    phone: str


class Device(BaseModel):
    user_id: int
    device_name: str


class Document(BaseModel):
    user_id: int
    title: str
    content: str


class UpdateRequest(BaseModel):
    device_id: int
    base_version: int
    changes: dict


class SyncRequest(BaseModel):
    device_id: int
    document_id: int
    base_version: int
    changes: dict


class ConflictResolution(BaseModel):
    resolution: str                 # "client" or "server"
    value: Optional[str] = None


# --------------------------------------------------------------------------
# 1. create user
# --------------------------------------------------------------------------
@app.post("/users")
def create_user(user: User):
    with db() as cur:
        cur.execute("SELECT 1 FROM users WHERE email = %s", (user.email,))
        if cur.fetchone():
            raise HTTPException(409, "Email already registered")
        cur.execute(
            """
            INSERT INTO users(name, email, password, phone)
            VALUES (%s, %s, %s, %s) RETURNING user_id
            """,
            (user.name, user.email, user.password, user.phone),
        )
        return {"user_id": cur.fetchone()[0]}


# --------------------------------------------------------------------------
# 2. register device
# --------------------------------------------------------------------------
@app.post("/devices")
def create_device(device: Device):
    with db() as cur:
        cur.execute("SELECT 1 FROM users WHERE user_id = %s", (device.user_id,))
        if cur.fetchone() is None:
            raise HTTPException(404, "User not found")
        cur.execute(
            """
            INSERT INTO devices(user_id, device_name)
            VALUES (%s, %s) RETURNING device_id
            """,
            (device.user_id, device.device_name),
        )
        return {"device_id": cur.fetchone()[0]}


# --------------------------------------------------------------------------
# 3. create document
# --------------------------------------------------------------------------
@app.post("/documents")
def create_document(document: Document):
    with db() as cur:
        cur.execute("SELECT 1 FROM users WHERE user_id = %s", (document.user_id,))
        if cur.fetchone() is None:
            raise HTTPException(404, "User not found")
        cur.execute(
            """
            INSERT INTO documents(user_id, title, content, version)
            VALUES (%s, %s, %s, 1) RETURNING document_id
            """,
            (document.user_id, document.title, document.content),
        )
        return {"document_id": cur.fetchone()[0], "version": 1}


# --------------------------------------------------------------------------
# 4. get document
# --------------------------------------------------------------------------
@app.get("/documents/{document_id}")
def get_document(document_id: int):
    with db() as cur:
        cur.execute(
            """
            SELECT document_id, title, content, version, updated_by_device
            FROM documents WHERE document_id = %s
            """,
            (document_id,),
        )
        row = cur.fetchone()
    if row is None:
        raise HTTPException(404, "Document not found")
    return {
        "document_id": row[0],
        "title": row[1],
        "content": row[2],
        "version": row[3],
        "updated_by_device": row[4],
    }


# --------------------------------------------------------------------------
# 5. normal update (device is online and up to date)
# --------------------------------------------------------------------------
@app.put("/documents/{document_id}")
def update_document(document_id: int, request: UpdateRequest):
    validate_changes(request.changes)
    with db() as cur:
        require_device(cur, request.device_id)
        title, content, version = lock_document(cur, document_id)

        if request.base_version != version:
            return JSONResponse(
                status_code=409,
                content={"status": "OUTDATED", "server_version": version},
            )

        title = request.changes.get("title", title)
        content = request.changes.get("content", content)
        new_version = write_new_version(
            cur, document_id, request.device_id, version, version,
            title, content, request.changes,
        )
    return {"status": "ACCEPTED", "document_id": document_id, "version": new_version}


# --------------------------------------------------------------------------
# 6 / 7 / 8. offline sync  (ACCEPTED | MERGED | 409 CONFLICT)
# --------------------------------------------------------------------------
@app.post("/sync")
def sync(request: SyncRequest):
    validate_changes(request.changes)
    with db() as cur:
        require_device(cur, request.device_id)
        title, content, server_version = lock_document(cur, request.document_id)
        current = {"title": title, "content": content}

        if request.base_version > server_version:
            raise HTTPException(400, "base_version is ahead of the server")

        # 6. device is up to date -> plain accept
        if request.base_version == server_version:
            title = request.changes.get("title", title)
            content = request.changes.get("content", content)
            new_version = write_new_version(
                cur, request.document_id, request.device_id,
                server_version, server_version, title, content, request.changes,
            )
            return {
                "status": "ACCEPTED",
                "document_id": request.document_id,
                "version": new_version,
            }

        # device is behind: which fields did the server change since its base?
        cur.execute(
            """
            SELECT changed_fields FROM change_log
            WHERE document_id = %s AND new_version > %s
            ORDER BY new_version
            """,
            (request.document_id, request.base_version),
        )
        changed_by_server = set()
        for (fields,) in cur.fetchall():
            changed_by_server.update(as_dict(fields).keys())

        # a real conflict = same field changed on both sides to different values
        conflicts = {
            f: {"server_value": current[f], "client_value": v}
            for f, v in request.changes.items()
            if f in changed_by_server and current[f] != v
        }

        # 8. conflict -> save it, return 409
        if conflicts:
            for field, vals in conflicts.items():
                cur.execute(
                    """
                    SELECT conflict_id FROM conflicts
                    WHERE document_id = %s AND device_id = %s
                      AND field = %s AND status = 'OPEN'
                    """,
                    (request.document_id, request.device_id, field),
                )
                existing = cur.fetchone()
                if existing:
                    cur.execute(
                        """
                        UPDATE conflicts SET server_value = %s, client_value = %s,
                               base_version = %s WHERE conflict_id = %s
                        """,
                        (vals["server_value"], vals["client_value"],
                         request.base_version, existing[0]),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO conflicts
                            (document_id, device_id, field, server_value,
                             client_value, base_version)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (request.document_id, request.device_id, field,
                         vals["server_value"], vals["client_value"],
                         request.base_version),
                    )
            return JSONResponse(
                status_code=409,
                content={
                    "status": "CONFLICT",
                    "document_id": request.document_id,
                    "server_version": server_version,
                    "conflicts": conflicts,
                },
            )

        # 7. no overlapping fields -> merge automatically
        title = request.changes.get("title", title)
        content = request.changes.get("content", content)
        new_version = write_new_version(
            cur, request.document_id, request.device_id,
            request.base_version, server_version, title, content, request.changes,
        )
    return {
        "status": "MERGED",
        "document_id": request.document_id,
        "version": new_version,
        "data": {"title": title, "content": content},
    }


# --------------------------------------------------------------------------
# 9. change history
# --------------------------------------------------------------------------
@app.get("/documents/{document_id}/changes")
def get_changes(document_id: int, since_version: int):
    with db() as cur:
        cur.execute("SELECT 1 FROM documents WHERE document_id = %s", (document_id,))
        if cur.fetchone() is None:
            raise HTTPException(404, "Document not found")
        cur.execute(
            """
            SELECT device_id, base_version, new_version, changed_fields
            FROM change_log
            WHERE document_id = %s AND new_version > %s
            ORDER BY new_version
            """,
            (document_id, since_version),
        )
        rows = cur.fetchall()
    return {
        "document_id": document_id,
        "changes": [
            {
                "device_id": r[0],
                "base_version": r[1],
                "new_version": r[2],
                "changed_fields": as_dict(r[3]),
            }
            for r in rows
        ],
    }


# --------------------------------------------------------------------------
# 10. list conflicts
# --------------------------------------------------------------------------
@app.get("/documents/{document_id}/conflicts")
def get_conflicts(document_id: int):
    with db() as cur:
        cur.execute("SELECT 1 FROM documents WHERE document_id = %s", (document_id,))
        if cur.fetchone() is None:
            raise HTTPException(404, "Document not found")
        cur.execute(
            """
            SELECT conflict_id, field, server_value, client_value, device_id, status
            FROM conflicts WHERE document_id = %s ORDER BY conflict_id
            """,
            (document_id,),
        )
        rows = cur.fetchall()
    return {
        "document_id": document_id,
        "conflicts": [
            {
                "conflict_id": r[0],
                "field": r[1],
                "server_value": r[2],
                "client_value": r[3],
                "device_id": r[4],
                "status": r[5],
            }
            for r in rows
        ],
    }


# --------------------------------------------------------------------------
# 11. resolve conflict
# --------------------------------------------------------------------------
@app.post("/conflicts/{conflict_id}/resolve")
def resolve_conflict(conflict_id: int, request: ConflictResolution):
    if request.resolution not in ("client", "server"):
        raise HTTPException(400, "resolution must be 'client' or 'server'")

    with db() as cur:
        cur.execute(
            """
            SELECT document_id, device_id, field, client_value, status
            FROM conflicts WHERE conflict_id = %s FOR UPDATE
            """,
            (conflict_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise HTTPException(404, "Conflict not found")
        document_id, device_id, field, client_value, status = row
        if status != "OPEN":
            raise HTTPException(409, "Conflict already resolved")

        title, content, version = lock_document(cur, document_id)

        if request.resolution == "client":
            value = request.value if request.value is not None else client_value
            if field == "title":
                title = value
            else:
                content = value
            version = write_new_version(
                cur, document_id, device_id, version, version,
                title, content, {field: value},
            )
        # resolution == "server": keep current value, version unchanged

        cur.execute(
            """
            UPDATE conflicts SET status = 'RESOLVED', resolved_at = CURRENT_TIMESTAMP
            WHERE conflict_id = %s
            """,
            (conflict_id,),
        )
    return {
        "status": "RESOLVED",
        "conflict_id": conflict_id,
        "document_id": document_id,
        "version": version,
    }
