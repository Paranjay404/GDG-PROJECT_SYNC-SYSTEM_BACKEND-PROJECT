# Offline Sync System

A backend API that keeps a user's documents in sync across multiple devices, even when a device edits while offline. Built with **FastAPI** and **PostgreSQL**.

Every document carries a `version` number. A device that comes back online tells the server which version it started from (`base_version`), and the server decides what to do:

| Situation | Result |
|-----------|--------|
| Device is up to date | `ACCEPTED` – version + 1 |
| Device is behind, but changed different fields than the server | `MERGED` – changes combined automatically |
| Device is behind and changed the same field as the server | `409 CONFLICT` – saved so it can be resolved later |

## Features

- User and device registration
- Create and read documents with versioning
- Offline sync with automatic merging of non-overlapping changes
- Field-level conflict detection (`title`, `content`)
- Full change history per document
- Conflict listing and resolution (keep the client's value or the server's)
- Row locking so two devices can never create the same version

## Tech Stack

- Python 3.10+
- FastAPI + Uvicorn
- PostgreSQL + psycopg2

## Project Structure

```
├── main.py             # FastAPI app with all endpoints
├── schema.sql          # Database and table creation
├── requirements.txt    # Python dependencies
├── test_flow.py        # Runs every example from APIs.txt against the server
├── APIs.txt            # API specification with sample inputs/outputs
└── README.md
```

## Getting Started

**1. Create the database**

```bash
psql -U postgres -f schema.sql
```

**2. Install dependencies**

```bash
pip install -r requirements.txt
```

**3. Configure the database password** (defaults to `1234`)

```bash
# Windows PowerShell
$env:DB_PASSWORD = "your_password"

# macOS / Linux
export DB_PASSWORD=your_password
```

Optional variables: `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`.

**4. Run the server**

```bash
uvicorn main:app --reload
```

Interactive docs are available at <http://127.0.0.1:8000/docs>.

**5. Run the test script** (in a second terminal)

```bash
python test_flow.py
```

## API Overview

| # | Method | Endpoint | Description |
|---|--------|----------|-------------|
| 1 | POST | `/users` | Create a user |
| 2 | POST | `/devices` | Register a device for a user |
| 3 | POST | `/documents` | Create a document (version 1) |
| 4 | GET | `/documents/{id}` | Get a document |
| 5 | PUT | `/documents/{id}` | Normal update from an up-to-date device |
| 6–8 | POST | `/sync` | Offline sync – accepted, merged or conflict |
| 9 | GET | `/documents/{id}/changes?since_version=N` | Change history |
| 10 | GET | `/documents/{id}/conflicts` | List conflicts for a document |
| 11 | POST | `/conflicts/{id}/resolve` | Resolve a conflict |

See [`APIs.txt`](APIs.txt) for full request and response examples.

### Example: a conflict

The server is at version 6 and the phone has already changed the title. The laptop was offline at version 5 and also changes the title:

```http
POST /sync
{
  "device_id": 1,
  "document_id": 1,
  "base_version": 5,
  "changes": { "title": "Laptop Title" }
}
```

```http
HTTP 409
{
  "status": "CONFLICT",
  "document_id": 1,
  "server_version": 6,
  "conflicts": {
    "title": { "server_value": "Phone Title", "client_value": "Laptop Title" }
  }
}
```

Resolve it with `POST /conflicts/{conflict_id}/resolve` and `{"resolution": "client", "value": "Laptop Title"}`.

## Database Schema

| Table | Purpose |
|-------|---------|
| `users` | Account details |
| `devices` | Devices belonging to a user |
| `documents` | Current title, content and version |
| `change_log` | Every accepted change with base and new version |
| `conflicts` | Detected conflicts and their status (`OPEN` / `RESOLVED`) |

## Future Improvements

- Password hashing and token-based authentication
- Check that a device belongs to the document's owner
- Text-level merging inside a field instead of whole-field conflicts
- Document deletion and sharing between users
- Automated test suite and Docker setup
