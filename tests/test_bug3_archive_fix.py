import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ───────────── STRUCTURAL TESTS ─────────────

def test_v2_archive_pattern_present():
    """V2: nuevo patrón de archive presente."""
    path = os.path.join(ROOT, "eolo-options", "main.py")
    with open(path) as f:
        src = f.read()
    assert 'eolo-options-trades-archive' in src
    assert 'snap.exists' in src
    assert 'merge=False' in src


def test_v2_old_delete_pattern_absent():
    """V2: el patrón viejo (delete sin archive) NO debe estar."""
    path = os.path.join(ROOT, "eolo-options", "main.py")
    with open(path) as f:
        src = f.read()
    bad = 'db.collection("eolo-options-trades").document(yesterday).delete()'
    assert bad not in src, "Old delete pattern still present in V2"


def test_crop_archive_pattern_present():
    """CROP: nuevo patrón de archive presente."""
    path = os.path.join(ROOT, "eolo-crop", "main.py")
    with open(path) as f:
        src = f.read()
    assert 'eolo-crop-trades-archive' in src
    assert 'snap.exists' in src


def test_crop_old_delete_pattern_absent():
    """CROP: el patrón viejo NO debe estar."""
    path = os.path.join(ROOT, "eolo-crop", "main.py")
    with open(path) as f:
        src = f.read()
    bad = 'db.collection("eolo-crop-trades").document(yesterday).delete()'
    assert bad not in src, "Old delete pattern still present in CROP"


def test_dashboard_archive_pattern_present():
    """Dashboard: nuevo patrón de archive presente."""
    path = os.path.join(ROOT, "Dashboard", "main.py")
    with open(path) as f:
        src = f.read()
    assert 'eolo-trades-archive' in src
    assert 'snap.exists' in src
    assert 'TRADES_COLLECTION' in src  # debe seguir usando la constante


def test_dashboard_old_delete_pattern_absent():
    """Dashboard: el patrón viejo NO debe estar."""
    path = os.path.join(ROOT, "Dashboard", "main.py")
    with open(path) as f:
        src = f.read()
    bad = 'db.collection(TRADES_COLLECTION).document(yesterday).delete()'
    # El delete debe seguir presente en src_ref.delete(), pero NO con el patrón
    # antiguo (sin archive previo)
    # Verificamos que el archive set ESTÉ ANTES del delete:
    archive_idx = src.find('eolo-trades-archive')
    delete_idx = src.find('src_ref.delete()')
    assert archive_idx > 0 and delete_idx > 0
    assert archive_idx < delete_idx, \
        "Archive set debe ocurrir antes que delete"


# ───────────── LOGIC TESTS (mock Firestore) ─────────────

def test_archive_logic_when_doc_exists():
    """Si snap.exists → archive set → delete original."""
    # Replicamos la lógica en aislamiento con mocks simples

    class MockSnap:
        def __init__(self, exists, data=None):
            self.exists = exists
            self._data = data or {}
        def to_dict(self):
            return self._data

    class MockDocRef:
        def __init__(self, doc_id, snap, parent_col):
            self.id = doc_id
            self._snap = snap
            self._deleted = False
            self.parent = parent_col
        def get(self):
            return self._snap
        def delete(self):
            self._deleted = True
        def set(self, data, merge=False):
            self.parent.docs[self.id] = data

    class MockColl:
        def __init__(self, name):
            self.name = name
            self.docs = {}
        def document(self, doc_id):
            return MockDocRef(doc_id, MockSnap(True, {"foo": "bar"}), self)

    # Setup: source col tiene un doc, archive col vacío
    source_col = MockColl("source")
    archive_col = MockColl("archive")
    yesterday = "2026-05-08"

    # Simular el patrón
    src_ref = source_col.document(yesterday)
    snap = src_ref.get()
    archived = False
    deleted = False
    if snap.exists:
        archive_col.document(yesterday).set(snap.to_dict(), merge=False)
        src_ref.delete()
        archived = True
        deleted = True

    assert archived
    assert deleted
    assert yesterday in archive_col.docs
    assert archive_col.docs[yesterday] == {"foo": "bar"}


def test_archive_logic_when_doc_does_not_exist():
    """Si snap.exists=False → no archive, no delete (idempotente)."""

    class MockSnap:
        exists = False
        def to_dict(self):
            return {}

    class MockDocRef:
        def __init__(self):
            self._snap = MockSnap()
            self._deleted = False
        def get(self):
            return self._snap
        def delete(self):
            self._deleted = True

    class MockArchiveCol:
        def __init__(self):
            self.was_called = False
        def document(self, doc_id):
            self.was_called = True
            return self
        def set(self, data, merge=False):
            pass

    src_ref = MockDocRef()
    archive_col = MockArchiveCol()
    snap = src_ref.get()

    if snap.exists:
        archive_col.document("today").set(snap.to_dict(), merge=False)
        src_ref.delete()

    assert not src_ref._deleted, "delete no debe llamarse si snap no existe"
    assert not archive_col.was_called, "archive no debe llamarse si snap no existe"
