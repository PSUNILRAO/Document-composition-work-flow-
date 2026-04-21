"""
template_versions.py
────────────────────
Template versioning + approval workflow for the Template Studio.

On-disk layout (per ``doc_type``)::

    uploads/templates/
        <doc_type>.docx                  # flat mirror of the currently-published DOCX
        <doc_type>.bindings.json         # flat mirror of the currently-published bindings
        <doc_type>/
            versions.json                # version index + metadata (source of truth)
            v1/
                <doc_type>.docx
                <doc_type>.bindings.json
            v2/
                <doc_type>.docx
                ...

Runtime rendering (``engine.generate_one`` → ``docx_renderer.merge_docx`` →
``template_studio.load_bindings``) continues to read the flat files. Those
flat files always mirror whichever version is currently in the ``published``
state — publishing or rolling back copies the chosen version's files over
the flat mirror atomically.

States & transitions
────────────────────
- ``draft``      — freshly uploaded, bindings editable, not live.
- ``in_review``  — submitted for review; bindings frozen.
- ``approved``   — approved, awaiting explicit publish.
- ``published``  — currently live. At most one per ``doc_type``.
- ``rejected``   — review rejected, terminal.
- ``archived``   — previously published, superseded.

Allowed transitions::

    draft      → in_review | rejected
    in_review  → approved  | rejected | draft
    approved   → published | draft
    published  → archived                       (only via publish/rollback)
    rejected   → draft                          (revive)
    archived   → published                      (only via rollback)

The module is pure I/O + metadata bookkeeping — no Flask or rendering
concerns live here.
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from docx_renderer import UPLOAD_TEMPLATES_DIR

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# Valid states
STATE_DRAFT = "draft"
STATE_IN_REVIEW = "in_review"
STATE_APPROVED = "approved"
STATE_PUBLISHED = "published"
STATE_REJECTED = "rejected"
STATE_ARCHIVED = "archived"

ALL_STATES: frozenset[str] = frozenset({
    STATE_DRAFT, STATE_IN_REVIEW, STATE_APPROVED,
    STATE_PUBLISHED, STATE_REJECTED, STATE_ARCHIVED,
})

# User-drivable transitions. ``published`` and ``archived`` transitions are
# handled through publish_version / rollback_published, not directly.
_USER_TRANSITIONS: dict[str, frozenset[str]] = {
    STATE_DRAFT:     frozenset({STATE_IN_REVIEW, STATE_REJECTED}),
    STATE_IN_REVIEW: frozenset({STATE_APPROVED, STATE_REJECTED, STATE_DRAFT}),
    STATE_APPROVED:  frozenset({STATE_DRAFT}),
    STATE_REJECTED:  frozenset({STATE_DRAFT}),
    # Published / archived transitions are explicit (publish / rollback).
    STATE_PUBLISHED: frozenset(),
    STATE_ARCHIVED:  frozenset(),
}


# ─────────────────────────────────────────────────────────────────────────────
# Path helpers
# ─────────────────────────────────────────────────────────────────────────────
_UPLOADS_RESOLVED = UPLOAD_TEMPLATES_DIR.resolve()


class VersionError(ValueError):
    """Raised for invalid version operations (bad state transition, etc.)."""


def _safe_doc_type(doc_type: str) -> str:
    if not isinstance(doc_type, str) or not doc_type:
        raise VersionError("doc_type must be a non-empty string")
    if doc_type in (".", "..") or any(c in doc_type for c in ("/", "\\", "\x00")):
        raise VersionError(f"Invalid doc_type: {doc_type!r}")
    return doc_type


def versions_dir(doc_type: str) -> Path:
    """Absolute path to the per-doc-type versions directory.

    Refuses traversal; the returned path is always under ``UPLOAD_TEMPLATES_DIR``.
    """
    doc_type = _safe_doc_type(doc_type)
    p = (UPLOAD_TEMPLATES_DIR / doc_type).resolve()
    # Defence-in-depth: ensure we stay under the uploads/templates root.
    try:
        p.relative_to(_UPLOADS_RESOLVED)
    except ValueError as exc:  # pragma: no cover — impossible given _safe_doc_type
        raise VersionError(f"Path escapes uploads dir: {p}") from exc
    return p


def versions_index_path(doc_type: str) -> Path:
    return versions_dir(doc_type) / "versions.json"


def version_dir(doc_type: str, version: int) -> Path:
    if not isinstance(version, int) or version < 1:
        raise VersionError(f"version must be a positive int, got {version!r}")
    return versions_dir(doc_type) / f"v{version}"


def version_docx_path(doc_type: str, version: int) -> Path:
    return version_dir(doc_type, version) / f"{doc_type}.docx"


def version_bindings_path(doc_type: str, version: int) -> Path:
    return version_dir(doc_type, version) / f"{doc_type}.bindings.json"


def flat_docx_path(doc_type: str) -> Path:
    return UPLOAD_TEMPLATES_DIR / f"{_safe_doc_type(doc_type)}.docx"


def flat_bindings_path(doc_type: str) -> Path:
    return UPLOAD_TEMPLATES_DIR / f"{_safe_doc_type(doc_type)}.bindings.json"


# ─────────────────────────────────────────────────────────────────────────────
# Versions index (versions.json) read/write
# ─────────────────────────────────────────────────────────────────────────────
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _empty_index() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "doc_type":       None,
        "latest":         0,
        "published":      None,
        "versions":       [],
    }


def _normalize_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Trim arbitrary dict to the version-entry shape we persist."""
    history = entry.get("history") or []
    if not isinstance(history, list):
        history = []
    return {
        "version":        int(entry.get("version") or 0),
        "status":         str(entry.get("status") or STATE_DRAFT),
        "created_at":     str(entry.get("created_at") or _now()),
        "updated_at":     str(entry.get("updated_at") or entry.get("created_at") or _now()),
        "uploaded_by":    str(entry.get("uploaded_by") or ""),
        "notes":          str(entry.get("notes") or ""),
        "parent_version": entry.get("parent_version"),
        "template_name":  str(entry.get("template_name") or ""),
        "history":        [
            {
                "at":   str(h.get("at") or _now()),
                "from": str(h.get("from") or ""),
                "to":   str(h.get("to") or ""),
                "by":   str(h.get("by") or ""),
            }
            for h in history if isinstance(h, dict)
        ],
    }


def _read_index(doc_type: str) -> dict[str, Any]:
    """Read versions.json for a doc_type. Does NOT auto-migrate — use
    :func:`ensure_index` for that."""
    path = versions_index_path(doc_type)
    if not path.is_file():
        return _empty_index()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Bad versions.json at %s: %s", path, exc)
        return _empty_index()
    out = _empty_index()
    out["schema_version"] = int(data.get("schema_version") or SCHEMA_VERSION)
    out["doc_type"]       = data.get("doc_type")
    out["latest"]         = int(data.get("latest") or 0)
    out["published"]      = data.get("published") if isinstance(data.get("published"), int) else None
    versions = data.get("versions") or []
    if isinstance(versions, list):
        out["versions"] = sorted(
            (_normalize_entry(v) for v in versions if isinstance(v, dict)),
            key=lambda v: v["version"],
        )
    return out


def _write_index(doc_type: str, index: dict[str, Any]) -> None:
    path = versions_index_path(doc_type)
    path.parent.mkdir(parents=True, exist_ok=True)
    index = dict(index)
    index["doc_type"] = doc_type
    index["versions"] = sorted(
        (_normalize_entry(v) for v in index.get("versions", [])),
        key=lambda v: v["version"],
    )
    index["latest"] = max((v["version"] for v in index["versions"]), default=0)
    # Derive published pointer from the authoritative per-version status.
    pub = [v["version"] for v in index["versions"] if v["status"] == STATE_PUBLISHED]
    index["published"] = pub[0] if pub else None
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, sort_keys=True)
    tmp.replace(path)


# ─────────────────────────────────────────────────────────────────────────────
# Migration: flat files → v1/published
# ─────────────────────────────────────────────────────────────────────────────
def _migrate_flat_to_v1(doc_type: str) -> dict[str, Any] | None:
    """If flat DOCX exists for ``doc_type`` but no versions.json yet, bootstrap
    ``v1`` as ``published`` and copy the flat DOCX (and bindings, if any) into it.

    Returns the new index, or ``None`` if no flat template was present.
    """
    flat = flat_docx_path(doc_type)
    if not flat.is_file():
        return None

    v_dir = version_dir(doc_type, 1)
    v_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(flat, version_docx_path(doc_type, 1))
    flat_bind = flat_bindings_path(doc_type)
    if flat_bind.is_file():
        shutil.copy2(flat_bind, version_bindings_path(doc_type, 1))

    now = _now()
    entry = _normalize_entry({
        "version":        1,
        "status":         STATE_PUBLISHED,
        "created_at":     now,
        "updated_at":     now,
        "uploaded_by":    "migration",
        "notes":          "Bootstrapped from pre-versioning flat template.",
        "parent_version": None,
        "template_name":  flat.name,
        "history": [{"at": now, "from": "", "to": STATE_PUBLISHED, "by": "migration"}],
    })
    index = _empty_index()
    index["versions"] = [entry]
    _write_index(doc_type, index)
    log.info("template_versions: migrated flat %s to v1 published", doc_type)
    return _read_index(doc_type)


def ensure_index(doc_type: str) -> dict[str, Any]:
    """Return the versions index, creating it (and auto-migrating flat templates
    if needed) on first call."""
    path = versions_index_path(doc_type)
    if path.is_file():
        return _read_index(doc_type)
    migrated = _migrate_flat_to_v1(doc_type)
    if migrated is not None:
        return migrated
    return _empty_index()


# ─────────────────────────────────────────────────────────────────────────────
# Read helpers used by the Studio UI
# ─────────────────────────────────────────────────────────────────────────────
def list_versions(doc_type: str) -> dict[str, Any]:
    """Return ``{"versions": [...], "published": <int|None>, "latest": <int>}``.
    Always safe to call — creates the index / migrates on first call.
    """
    idx = ensure_index(doc_type)
    return {
        "versions":  idx["versions"],
        "published": idx["published"],
        "latest":    idx["latest"],
    }


def get_version(doc_type: str, version: int) -> dict[str, Any] | None:
    idx = ensure_index(doc_type)
    for v in idx["versions"]:
        if v["version"] == version:
            return v
    return None


def has_versioned_template(doc_type: str, version: int) -> bool:
    return version_docx_path(doc_type, version).is_file()


# ─────────────────────────────────────────────────────────────────────────────
# Mutations
# ─────────────────────────────────────────────────────────────────────────────
def _append_history(entry: dict[str, Any], prev: str, new: str, by: str) -> None:
    entry["history"].append({
        "at":   _now(),
        "from": prev,
        "to":   new,
        "by":   by or "",
    })
    entry["updated_at"] = _now()


def add_version_from_bytes(
    doc_type: str,
    docx_bytes: bytes,
    template_name: str,
    uploaded_by: str = "",
    notes: str = "",
    parent_version: int | None = None,
) -> dict[str, Any]:
    """Persist a new draft version from raw DOCX bytes.

    If this is the very first version for ``doc_type`` (no flat template and
    no versions.json), the new version is **auto-published** (v1) so that the
    runtime immediately has something to render. Subsequent uploads land as
    ``draft`` and do not affect the live flat files until explicitly published.

    Returns the new version entry.
    """
    idx = ensure_index(doc_type)
    first_ever = not idx["versions"] and not flat_docx_path(doc_type).is_file()

    next_version = idx["latest"] + 1
    v_dir = version_dir(doc_type, next_version)
    v_dir.mkdir(parents=True, exist_ok=True)
    dest = version_docx_path(doc_type, next_version)
    with open(dest, "wb") as f:
        f.write(docx_bytes)

    # Seed bindings from parent (or published) version if one exists.
    seed_from = parent_version if parent_version else idx["published"]
    if seed_from:
        src = version_bindings_path(doc_type, seed_from)
        if src.is_file():
            shutil.copy2(src, version_bindings_path(doc_type, next_version))

    now = _now()
    status = STATE_PUBLISHED if first_ever else STATE_DRAFT
    entry = _normalize_entry({
        "version":        next_version,
        "status":         status,
        "created_at":     now,
        "updated_at":     now,
        "uploaded_by":    uploaded_by,
        "notes":          notes,
        "parent_version": parent_version,
        "template_name":  template_name,
        "history":        [{"at": now, "from": "", "to": status, "by": uploaded_by or ""}],
    })
    idx["versions"].append(entry)
    _write_index(doc_type, idx)

    # If auto-published (first ever), mirror files to the flat paths so the
    # runtime sees them.
    if first_ever:
        _mirror_version_to_flat(doc_type, next_version)

    return entry


def set_version_bindings(
    doc_type: str, version: int, manifest: dict[str, Any],
) -> dict[str, Any]:
    """Write a bindings manifest into ``v<version>/``. If ``version`` is the
    currently-published one, also mirror to the flat bindings file.

    Edits are allowed on:

    * ``draft`` — the canonical editable state.
    * ``published`` **iff no newer version exists** — treated as an
      in-place hotfix on the currently-live template. As soon as a newer
      draft is created, the published version becomes read-only (edits
      must happen on the new draft).

    All other states (``in_review`` / ``approved`` / ``rejected`` /
    ``archived`` / ``published`` when superseded by a newer version) are
    refused. Callers must transition back to ``draft`` first.
    """
    entry = get_version(doc_type, version)
    if entry is None:
        raise VersionError(f"version {version} does not exist for {doc_type}")
    idx = _read_index(doc_type)
    latest = idx.get("latest") or 0
    status = entry["status"]
    editable = (
        status == STATE_DRAFT
        or (status == STATE_PUBLISHED and version == latest)
    )
    if not editable:
        raise VersionError(
            f"cannot edit bindings of v{version} in state {status!r}; "
            "move it back to 'draft' or create a new version first"
        )

    path = version_bindings_path(doc_type, version)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
    tmp.replace(path)

    idx = ensure_index(doc_type)
    if idx["published"] == version:
        # Edge case: a published version should normally be immutable, but the
        # migration path creates v1 as published; keep flat mirror in sync for
        # safety.
        shutil.copy2(path, flat_bindings_path(doc_type))

    # Bump updated_at.
    for v in idx["versions"]:
        if v["version"] == version:
            v["updated_at"] = _now()
            break
    _write_index(doc_type, idx)
    return entry


def transition_status(
    doc_type: str, version: int, new_status: str, by: str = "",
) -> dict[str, Any]:
    """Apply a user-driven state transition. Does NOT touch files.

    Publishing / rollback are separate operations (``publish_version`` /
    ``rollback_published``) because they have file-system side effects.
    """
    if new_status not in ALL_STATES:
        raise VersionError(f"unknown status {new_status!r}")
    idx = ensure_index(doc_type)
    target = None
    for v in idx["versions"]:
        if v["version"] == version:
            target = v
            break
    if target is None:
        raise VersionError(f"version {version} does not exist for {doc_type}")

    allowed = _USER_TRANSITIONS.get(target["status"], frozenset())
    if new_status not in allowed:
        raise VersionError(
            f"cannot transition v{version} from {target['status']!r} "
            f"to {new_status!r} directly (allowed: {sorted(allowed) or 'none'})"
        )

    prev = target["status"]
    target["status"] = new_status
    _append_history(target, prev, new_status, by)
    _write_index(doc_type, idx)
    return target


def _mirror_version_to_flat(doc_type: str, version: int) -> None:
    """Copy v<version>'s DOCX (and bindings, if any) over the flat paths."""
    src_docx = version_docx_path(doc_type, version)
    if not src_docx.is_file():
        raise VersionError(
            f"cannot mirror v{version} to flat: DOCX missing at {src_docx}"
        )
    shutil.copy2(src_docx, flat_docx_path(doc_type))
    src_bindings = version_bindings_path(doc_type, version)
    if src_bindings.is_file():
        shutil.copy2(src_bindings, flat_bindings_path(doc_type))
    else:
        # No bindings for this version — clear the flat mirror so the runtime
        # doesn't leak stale bindings from a previously-published version.
        flat = flat_bindings_path(doc_type)
        if flat.is_file():
            flat.unlink()


def publish_version(doc_type: str, version: int, by: str = "") -> dict[str, Any]:
    """Promote ``version`` to ``published``. If another version is currently
    published, it is transitioned to ``archived``. Mirrors the chosen version's
    DOCX + bindings to the flat paths atomically.

    Only ``approved`` or ``archived`` versions may be published directly (via
    approval or rollback, respectively). ``draft`` / ``in_review`` / ``rejected``
    must go through the normal workflow first.
    """
    idx = ensure_index(doc_type)
    target = None
    for v in idx["versions"]:
        if v["version"] == version:
            target = v
            break
    if target is None:
        raise VersionError(f"version {version} does not exist for {doc_type}")

    if target["status"] not in (STATE_APPROVED, STATE_ARCHIVED):
        raise VersionError(
            f"cannot publish v{version} from state {target['status']!r}; "
            "must be 'approved' (normal flow) or 'archived' (rollback)"
        )

    # Archive any currently-published version first.
    for v in idx["versions"]:
        if v["status"] == STATE_PUBLISHED and v["version"] != version:
            _append_history(v, STATE_PUBLISHED, STATE_ARCHIVED, by or "publish")
            v["status"] = STATE_ARCHIVED

    prev = target["status"]
    target["status"] = STATE_PUBLISHED
    _append_history(target, prev, STATE_PUBLISHED, by)
    _write_index(doc_type, idx)

    _mirror_version_to_flat(doc_type, version)
    return target


def rollback_published(doc_type: str, by: str = "") -> dict[str, Any]:
    """Re-publish the most recently-archived version that was previously the
    published one. Archives the current published version.

    Raises ``VersionError`` if there is no published version or nothing to
    roll back to.
    """
    idx = ensure_index(doc_type)
    current_pub = None
    for v in idx["versions"]:
        if v["status"] == STATE_PUBLISHED:
            current_pub = v
            break
    if current_pub is None:
        raise VersionError("no currently-published version to roll back from")

    # Candidate = most recent archived version (by version number) that comes
    # before the currently-published one and whose history shows it was
    # previously published. Practical proxy: most recently-updated archived
    # whose version < current_pub.
    candidates = [
        v for v in idx["versions"]
        if v["status"] == STATE_ARCHIVED and v["version"] < current_pub["version"]
        and any(h.get("to") == STATE_PUBLISHED for h in v["history"])
    ]
    if not candidates:
        raise VersionError("no prior published version to roll back to")
    candidates.sort(key=lambda v: v["updated_at"], reverse=True)
    target = candidates[0]

    # Archive current, publish candidate.
    _append_history(current_pub, STATE_PUBLISHED, STATE_ARCHIVED, by or "rollback")
    current_pub["status"] = STATE_ARCHIVED
    _append_history(target, STATE_ARCHIVED, STATE_PUBLISHED, by or "rollback")
    target["status"] = STATE_PUBLISHED
    _write_index(doc_type, idx)

    _mirror_version_to_flat(doc_type, target["version"])
    return target


# ─────────────────────────────────────────────────────────────────────────────
# Read bindings for a specific version (for Studio preview / diff)
# ─────────────────────────────────────────────────────────────────────────────
def read_version_bindings(doc_type: str, version: int) -> dict[str, Any]:
    """Return the bindings manifest for a given version (empty dict if none).
    Does not normalise against the schema — call sites should pass this
    through ``template_studio.load_bindings``-style normalisation if they need
    guaranteed shape.
    """
    path = version_bindings_path(doc_type, version)
    if not path.is_file():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Bad bindings file %s: %s", path, exc)
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# Metadata diff between two versions
# ─────────────────────────────────────────────────────────────────────────────
def _placeholder_snapshot(doc_type: str, version: int) -> dict[str, Any]:
    """Best-effort structured summary of a version's placeholders + bindings,
    used for diffs. Returns an empty snapshot if the DOCX can't be parsed."""
    out = {
        "scalar_placeholders": [],
        "repeating_sections":  [],
        "bindings":            read_version_bindings(doc_type, version),
    }
    docx_path = version_docx_path(doc_type, version)
    if not docx_path.is_file():
        return out
    # Late import to avoid a circular dependency at module import time.
    try:
        from template_studio import _collect_template_source  # type: ignore
        from jinja2 import Environment, meta, nodes
    except Exception as exc:  # pragma: no cover
        log.debug("placeholder snapshot skipped: %s", exc)
        return out

    try:
        source = _collect_template_source(docx_path)
        ast = Environment().parse(source)
    except Exception as exc:
        log.debug("placeholder snapshot parse failed for v%s: %s", version, exc)
        return out

    reps: list[dict[str, Any]] = []
    vars_inside_loops: set[str] = set()
    for fn in ast.find_all(nodes.For):
        iter_name = fn.iter.name if isinstance(fn.iter, nodes.Name) else None
        loop_var = fn.target.name if isinstance(fn.target, nodes.Name) else ""
        inner: list[str] = []
        for stmt in fn.body:
            for n in stmt.find_all(nodes.Getattr):
                if isinstance(n.node, nodes.Name) and n.node.name == loop_var:
                    if n.attr not in inner:
                        inner.append(n.attr)
        if iter_name:
            reps.append({
                "iter_source":  iter_name,
                "loop_var":     loop_var,
                "inner_fields": inner,
            })
        for child in fn.iter_child_nodes():
            for n in child.find_all(nodes.Name):
                vars_inside_loops.add(n.name)
        if loop_var:
            vars_inside_loops.add(loop_var)

    iter_sources = {rs["iter_source"] for rs in reps}
    scalars = sorted(
        v for v in meta.find_undeclared_variables(ast)
        if v not in iter_sources and v not in vars_inside_loops
    )
    out["scalar_placeholders"] = scalars
    out["repeating_sections"]  = reps
    return out


def diff_versions(doc_type: str, version_a: int, version_b: int) -> dict[str, Any]:
    """Return a simple metadata diff between two versions.

    Shape::

        {
          "a": <version>, "b": <version>,
          "scalar_placeholders":   {"added":[...], "removed":[...], "common":[...]},
          "repeating_sections":    {"added":[...], "removed":[...], "common":[...]},
          "bindings_scalars":      {"added":[...], "removed":[...], "changed":[...]},
          "bindings_repeating":    {"added":[...], "removed":[...], "changed":[...]}
        }
    """
    a = _placeholder_snapshot(doc_type, version_a)
    b = _placeholder_snapshot(doc_type, version_b)

    def _set_diff(lst_a: Iterable[str], lst_b: Iterable[str]) -> dict[str, list[str]]:
        sa, sb = set(lst_a), set(lst_b)
        return {
            "added":   sorted(sb - sa),
            "removed": sorted(sa - sb),
            "common":  sorted(sa & sb),
        }

    def _bind_scalar_diff(ba: dict, bb: dict) -> dict[str, Any]:
        ba = ba.get("scalars") or {} if isinstance(ba, dict) else {}
        bb = bb.get("scalars") or {} if isinstance(bb, dict) else {}
        added   = sorted(k for k in bb if k not in ba)
        removed = sorted(k for k in ba if k not in bb)
        changed = sorted(k for k in ba if k in bb and ba[k] != bb[k])
        return {"added": added, "removed": removed, "changed": changed}

    def _bind_repeating_diff(ba: dict, bb: dict) -> dict[str, Any]:
        ra = ba.get("repeating") or {} if isinstance(ba, dict) else {}
        rb = bb.get("repeating") or {} if isinstance(bb, dict) else {}
        added   = sorted(k for k in rb if k not in ra)
        removed = sorted(k for k in ra if k not in rb)
        changed = sorted(
            k for k in ra if k in rb and json.dumps(ra[k], sort_keys=True)
            != json.dumps(rb[k], sort_keys=True)
        )
        return {"added": added, "removed": removed, "changed": changed}

    return {
        "a":                     version_a,
        "b":                     version_b,
        "scalar_placeholders":   _set_diff(a["scalar_placeholders"],
                                           b["scalar_placeholders"]),
        "repeating_sections":    _set_diff(
            [rs["iter_source"] for rs in a["repeating_sections"]],
            [rs["iter_source"] for rs in b["repeating_sections"]],
        ),
        "bindings_scalars":      _bind_scalar_diff(a["bindings"], b["bindings"]),
        "bindings_repeating":    _bind_repeating_diff(a["bindings"], b["bindings"]),
    }
