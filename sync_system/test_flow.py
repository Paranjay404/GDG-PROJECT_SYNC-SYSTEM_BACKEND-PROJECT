"""
Runs every example from APIs.txt against a running server and prints PASS/FAIL.

    1) start the server:   uvicorn main:app --reload
    2) in another terminal: python test_flow.py
"""
import sys
import uuid

import requests

BASE = "http://127.0.0.1:8000"
failures = 0


def check(name, response, expected_status, expected_body=None):
    global failures
    ok = response.status_code == expected_status
    body = response.json()
    if expected_body:
        ok = ok and all(body.get(k) == v for k, v in expected_body.items())
    print(("PASS" if ok else "FAIL"), "-", name, response.status_code, body)
    if not ok:
        failures += 1
    return body


def new_doc(user_id):
    r = requests.post(f"{BASE}/documents",
                      json={"user_id": user_id, "title": "T", "content": "C"})
    return r.json()["document_id"]


def bump_to(doc_id, device_id, version, target):
    """Make plain updates until the server is at `target`."""
    while version < target:
        requests.put(f"{BASE}/documents/{doc_id}", json={
            "device_id": device_id, "base_version": version,
            "changes": {"title": f"T{version + 1}"}})
        version += 1


# 1. users / 2. devices
email = f"rahul_{uuid.uuid4().hex[:6]}@gmail.com"
u = check("1  create user", requests.post(f"{BASE}/users", json={
    "name": "Rahul", "email": email, "password": "123456", "phone": "1111111111"}), 200)
uid = u["user_id"]
check("1b duplicate email -> 409", requests.post(f"{BASE}/users", json={
    "name": "Rahul", "email": email, "password": "x", "phone": "1"}), 409)

laptop = check("2  register laptop", requests.post(f"{BASE}/devices", json={
    "user_id": uid, "device_name": "Rahul Laptop"}), 200)["device_id"]
phone = check("2b register phone", requests.post(f"{BASE}/devices", json={
    "user_id": uid, "device_name": "Rahul Phone"}), 200)["device_id"]

# 3 / 4. documents
doc = check("3  create document", requests.post(f"{BASE}/documents", json={
    "user_id": uid, "title": "My Notes", "content": "Hello World"}), 200,
    {"version": 1})["document_id"]
check("4  get document", requests.get(f"{BASE}/documents/{doc}"), 200,
      {"title": "My Notes", "content": "Hello World", "version": 1})

# 5. normal update
check("5  normal update", requests.put(f"{BASE}/documents/{doc}", json={
    "device_id": laptop, "base_version": 1,
    "changes": {"title": "My Important Notes"}}), 200,
    {"status": "ACCEPTED", "version": 2})
check("5b stale update -> 409 OUTDATED", requests.put(f"{BASE}/documents/{doc}", json={
    "device_id": laptop, "base_version": 1, "changes": {"title": "x"}}), 409,
    {"status": "OUTDATED"})

# 6. offline sync, device up to date
check("6  sync accepted", requests.post(f"{BASE}/sync", json={
    "device_id": phone, "document_id": doc, "base_version": 2,
    "changes": {"content": "Hello India"}}), 200,
    {"status": "ACCEPTED", "version": 3})

# 7. non-conflicting offline update (server 6, laptop at 5)
d7 = new_doc(uid)
bump_to(d7, laptop, 1, 5)
requests.post(f"{BASE}/sync", json={"device_id": phone, "document_id": d7,
              "base_version": 5, "changes": {"content": "Phone Content"}})
check("7  sync merged", requests.post(f"{BASE}/sync", json={
    "device_id": laptop, "document_id": d7, "base_version": 5,
    "changes": {"title": "Laptop Title"}}), 200,
    {"status": "MERGED", "version": 7,
     "data": {"title": "Laptop Title", "content": "Phone Content"}})

# 9. change history
check("9  change history", requests.get(
    f"{BASE}/documents/{d7}/changes", params={"since_version": 5}), 200)

# 8. conflicting offline update
d8 = new_doc(uid)
bump_to(d8, laptop, 1, 5)
requests.post(f"{BASE}/sync", json={"device_id": phone, "document_id": d8,
              "base_version": 5, "changes": {"title": "Phone Title"}})
check("8  sync conflict -> 409", requests.post(f"{BASE}/sync", json={
    "device_id": laptop, "document_id": d8, "base_version": 5,
    "changes": {"title": "Laptop Title"}}), 409,
    {"status": "CONFLICT", "server_version": 6,
     "conflicts": {"title": {"server_value": "Phone Title",
                             "client_value": "Laptop Title"}}})

# 10. get conflicts
c = check("10 get conflicts", requests.get(f"{BASE}/documents/{d8}/conflicts"), 200)
cid = c["conflicts"][0]["conflict_id"]

# 11. resolve conflict (client wins)
check("11 resolve conflict", requests.post(f"{BASE}/conflicts/{cid}/resolve", json={
    "resolution": "client", "value": "Laptop Title"}), 200,
    {"status": "RESOLVED", "conflict_id": cid, "version": 7})
check("11b resolving twice -> 409", requests.post(f"{BASE}/conflicts/{cid}/resolve", json={
    "resolution": "client", "value": "x"}), 409)
check("11c doc has resolved value", requests.get(f"{BASE}/documents/{d8}"), 200,
      {"title": "Laptop Title"})

print("\nALL PASSED" if failures == 0 else f"\n{failures} FAILED")
sys.exit(1 if failures else 0)
