"""Translation between Roblox's v1 and v3 asset-batch wire formats.

Current Roblox clients post ``/v3/assets/batch`` instead of
``/v1/assets/batch``. The payload carries the same information, but the entry
list is wrapped in an ``{'assets': [...]}`` envelope, the asset id is nested
under ``asset`` and the type arrives as a name instead of a numeric id. The
replacer and scraper addons are written against the flat v1 list, so a v3
request is flattened on the way in and rebuilt as v3 before being forwarded
upstream, and a v3 response is unwrapped for the addon hooks while the client
still receives the untouched envelope.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Mapping

# A decoded JSON object. Batch payloads are free-form JSON from the client, so
# the values stay dynamic and are narrowed where they are actually read
JsonObject = dict[str, Any]

V1_BATCH_PATH = b'/v1/assets/batch'
V3_BATCH_PATH = b'/v3/assets/batch'
BATCH_PATHS: tuple[bytes, ...] = (V1_BATCH_PATH, V3_BATCH_PATH)

ENVELOPE_KEY = 'assets'

# Carried by a v3 entry but meaningless to the v1 addons. They stay on the
# original entry and are restored when the request is rebuilt, so leaving them
# out of the flattened copy keeps the v1 entry shape recognisable
_V3_ONLY_KEYS = frozenset({'asset', 'version', 'insertContext'})


def is_batch_request(req_first: bytes) -> bool:
    """Whether a request line targets the asset-batch endpoint, v1 or v3."""
    return any(path in req_first for path in BATCH_PATHS)


def is_v3_batch_request(req_first: bytes) -> bool:
    """Whether a request line targets the v3 asset-batch endpoint."""
    return V3_BATCH_PATH in req_first


def envelope_items(data: object) -> list[Any] | None:
    """Return a v3 envelope's entry list, or ``None`` for a v1 payload.

    Applies to requests and responses alike: both wrap their entries in the
    same ``{'assets': [...]}`` object.
    """
    if not isinstance(data, dict):
        return None
    items = cast('JsonObject', data).get(ENVELOPE_KEY)
    if not isinstance(items, list):
        return None
    return cast('list[Any]', items)


def to_v1_entries(
    items: list[Any],
    type_ids: Mapping[str, int] | None = None,
    origin: dict[int, tuple[Any, Any]] | None = None,
) -> list[Any]:
    """Flatten v3 entries into the v1 entry shape.

    The result is index-aligned with *items*: an entry that is not an object is
    passed through untouched so a malformed entry cannot shift the mapping onto
    the wrong asset. *type_ids* maps a lower-cased asset type name to its
    numeric id, supplying ``assetTypeId`` from v3's string ``assetType``.

    When *origin* is given it is filled with ``id(entry) -> (item, entry)`` for
    :func:`rebuild_v3_request`, which cannot rely on position because removal
    rules drop entries from the flattened list. The entry is kept in the value
    purely to hold a reference: were it freed, a later dict could reuse its
    ``id`` and be mistaken for it.
    """
    flattened: list[Any] = []
    for item in items:
        if not isinstance(item, dict):
            flattened.append(item)
            continue
        source = cast('JsonObject', item)
        entry: JsonObject = {
            key: value for key, value in source.items() if key not in _V3_ONLY_KEYS
        }
        asset = source.get('asset')
        if isinstance(asset, dict) and 'assetId' in asset:
            entry['assetId'] = cast('JsonObject', asset)['assetId']
        if 'assetTypeId' not in entry and type_ids:
            asset_type = source.get('assetType')
            if isinstance(asset_type, str):
                mapped = type_ids.get(asset_type.lower())
                if mapped is not None:
                    entry['assetTypeId'] = mapped
        if origin is not None:
            origin[id(entry)] = (source, entry)
        flattened.append(entry)
    return flattened


def rebuild_v3_request(
    envelope: JsonObject, entries: list[Any], origin: dict[int, tuple[Any, Any]]
) -> None:
    """Rebuild *envelope*'s asset list from the surviving flattened *entries*.

    Each surviving entry's asset id is written back onto its own v3 item, and
    the envelope is rebuilt in the order the entries now have, so a removal rule
    that dropped an entry drops the matching v3 item too. Anything the addons
    left alone keeps its original ``assetType``, ``version`` and
    ``insertContext``. An entry with no known origin is skipped rather than
    guessed at.
    """
    rebuilt: list[Any] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        flattened_entry = cast('JsonObject', entry)
        found = origin.get(id(flattened_entry))
        if found is None:
            continue
        item = cast('JsonObject', found[0])
        if 'assetId' in flattened_entry:
            asset = item.get('asset')
            if isinstance(asset, dict):
                cast('JsonObject', asset)['assetId'] = flattened_entry['assetId']
            else:
                item['asset'] = {'assetId': flattened_entry['assetId']}
        rebuilt.append(item)
    envelope[ENVELOPE_KEY] = rebuilt
