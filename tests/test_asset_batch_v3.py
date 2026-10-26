"""Tests for the v3 asset-batch wire format.

Current Roblox clients post ``/v3/assets/batch`` with an ``{'assets': [...]}``
envelope instead of the v1 list, which silently bypassed the replacer and the
scraper entirely (upstream issue #124).
"""

import json

from fleasion.proxy.addons.asset_batch import (
    envelope_items,
    rebuild_v3_request,
    to_v1_entries,
)
from fleasion.proxy.addons.texture_stripper import TextureStripper
from fleasion.proxy.server import _is_empty_batch_body, is_asset_batch_request


def v3_entry() -> dict:
    """A real v3 entry, as captured in upstream issue #124.

    Built fresh per call: the nested ``asset`` dict is written back into, so a
    shared literal would leak a replaced id into the next test.
    """
    return {
        'requestId': '0',
        'asset': {'assetId': 17091656898},
        'version': {'stable': {}},
        'assetType': 'Animation',
        'insertContext': {'serverPlaceId': 17625359962},
    }


class _Config:
    def get_all_replacements(self):
        return {}, set(), {}, {}


def setup_function():
    TextureStripper.reset_routes()


# ── translation ───────────────────────────────────────────────────────────


def test_envelope_items_detects_v3_and_ignores_v1():
    assert envelope_items({'assets': [v3_entry()]}) is not None
    assert envelope_items({'assets': []}) == []
    assert envelope_items([{'assetId': 1}]) is None
    assert envelope_items({'nope': [1]}) is None
    assert envelope_items(None) is None


def test_to_v1_entries_flattens_nested_asset_id_and_names_the_type():
    entries = to_v1_entries([v3_entry()], {'animation': 24})

    assert entries == [
        {'requestId': '0', 'assetId': 17091656898, 'assetType': 'Animation', 'assetTypeId': 24}
    ]


def test_to_v1_entries_passes_through_malformed_entries_to_keep_alignment():
    entries = to_v1_entries(['junk', v3_entry()], {'animation': 24})

    assert entries[0] == 'junk'
    assert entries[1]['assetId'] == 17091656898


def test_to_v1_entries_keeps_an_explicit_type_id():
    entry = {'requestId': '0', 'asset': {'assetId': 5}, 'assetType': 'Image', 'assetTypeId': 63}

    assert to_v1_entries([entry], {'image': 1})[0]['assetTypeId'] == 63


def test_rebuild_v3_request_writes_replacement_back_into_the_envelope():
    envelope = {'assets': [v3_entry()]}
    origin: dict[int, tuple[object, object]] = {}
    entries = to_v1_entries(envelope['assets'], {'animation': 24}, origin)

    entries[0]['assetId'] = 999

    rebuild_v3_request(envelope, entries, origin)

    assert envelope['assets'][0]['asset'] == {'assetId': 999}
    # Everything the addons do not own is left exactly as the client sent it
    assert envelope['assets'][0]['version'] == {'stable': {}}
    assert envelope['assets'][0]['insertContext'] == {'serverPlaceId': 17625359962}
    assert envelope['assets'][0]['assetType'] == 'Animation'


def test_rebuild_v3_request_drops_removed_entries():
    envelope = {
        'assets': [
            {'requestId': '0', 'asset': {'assetId': 100}, 'assetType': 'Image'},
            {'requestId': '1', 'asset': {'assetId': 101}, 'assetType': 'Image'},
            {'requestId': '2', 'asset': {'assetId': 102}, 'assetType': 'Image'},
        ]
    }
    origin: dict[int, tuple[object, object]] = {}
    entries = to_v1_entries(envelope['assets'], {'image': 1}, origin)

    # What a removal rule leaves behind: the middle entry filtered out
    survivors = [entries[0], entries[2]]

    rebuild_v3_request(envelope, survivors, origin)

    assert [item['asset']['assetId'] for item in envelope['assets']] == [100, 102]
    assert [item['requestId'] for item in envelope['assets']] == ['0', '2']


# ── request path ──────────────────────────────────────────────────────────


def test_v3_batch_request_applies_id_replacement():
    stripper = TextureStripper(_Config())
    body = json.dumps({'assets': [v3_entry()]}).encode()

    modified, _ = stripper.process_batch_request(
        body, {}, ({17091656898: 999}, set(), {}, {}), 'batch'
    )

    assert json.loads(modified) == {
        'assets': [
            {
                'requestId': '0',
                'asset': {'assetId': 999},
                'version': {'stable': {}},
                'assetType': 'Animation',
                'insertContext': {'serverPlaceId': 17625359962},
            }
        ]
    }


def test_v3_batch_request_scraper_body_restores_original_ids_in_v1_shape():
    stripper = TextureStripper(_Config())
    body = json.dumps({'assets': [v3_entry()]}).encode()

    _, scraper_body = stripper.process_batch_request(
        body, {}, ({17091656898: 999}, set(), {}, {}), 'batch'
    )

    # The scraper indexes against the unwrapped response, so it gets the flat
    # shape with the original ids it should cache under
    assert json.loads(scraper_body) == [
        {
            'requestId': '0',
            'assetId': 17091656898,
            'assetType': 'Animation',
            'assetTypeId': 24,
        }
    ]


def test_v3_batch_request_removal_drops_the_entry_from_the_envelope():
    stripper = TextureStripper(_Config())
    body = json.dumps(
        {
            'assets': [
                {'requestId': '0', 'asset': {'assetId': 100}, 'assetType': 'Image'},
                {'requestId': '1', 'asset': {'assetId': 101}, 'assetType': 'Image'},
            ]
        }
    ).encode()

    modified, _ = stripper.process_batch_request(body, {}, ({}, {100}, {}, {}), 'batch')

    assert json.loads(modified) == {
        'assets': [{'requestId': '1', 'asset': {'assetId': 101}, 'assetType': 'Image'}]
    }


def test_v3_batch_request_without_rules_is_forwarded_untouched():
    stripper = TextureStripper(_Config())
    body = json.dumps({'assets': [v3_entry()]}).encode()

    modified, scraper_body = stripper.process_batch_request(body, {}, ({}, set(), {}, {}), 'batch')

    assert modified == body
    assert scraper_body == body


def test_malformed_batch_body_still_returns_two_bodies():
    stripper = TextureStripper(_Config())

    assert stripper.process_batch_request(b'not json', {}, ({}, set(), {}, {}), 'b') == (
        b'not json',
        b'not json',
    )
    assert stripper.process_batch_request(b'"text"', {}, ({}, set(), {}, {}), 'b') == (
        b'"text"',
        b'"text"',
    )


# ── response path ─────────────────────────────────────────────────────────


def test_v3_batch_response_registers_the_cdn_route(tmp_path):
    replacement = tmp_path / 'replacement.png'
    replacement.write_bytes(b'png')

    class _LocalConfig:
        def get_all_replacements(self):
            return {}, set(), {}, {1234: str(replacement)}

    config = _LocalConfig()
    stripper = TextureStripper(config)
    body = json.dumps(
        {'assets': [{'requestId': '0', 'asset': {'assetId': 1234}, 'assetType': 'Image'}]}
    ).encode()
    response = json.dumps(
        {
            'assets': [
                {
                    'requestId': '0',
                    'assetId': 1234,
                    'assetTypeId': 1,
                    'location': 'https://fts.rbxcdn.com/v2/sc3/deadbeef',
                }
            ]
        }
    ).encode()

    modified, _ = stripper.process_batch_request(body, {}, config.get_all_replacements(), 'batch')
    stripper.process_batch_response(modified, response, {}, 'batch')

    assert stripper.check_cdn_request('fts.rbxcdn.com', '/v2/sc3/deadbeef') == (
        'local',
        str(replacement),
    )


# ── server endpoint matching ──────────────────────────────────────────────


def test_is_asset_batch_request_matches_both_versions():
    assert is_asset_batch_request(b'POST /v1/assets/batch HTTP/1.1')
    assert is_asset_batch_request(b'POST /v3/assets/batch HTTP/1.1')
    assert not is_asset_batch_request(b'GET /v1/asset?id=1 HTTP/1.1')
    assert not is_asset_batch_request(b'POST /v2/assets/batch HTTP/1.1')


def test_is_empty_batch_body_handles_both_shapes():
    assert _is_empty_batch_body(b'[]')
    assert _is_empty_batch_body(b'  []  ')
    assert _is_empty_batch_body(b'{"assets": []}')
    assert not _is_empty_batch_body(b'[{"assetId": 1}]')
    assert not _is_empty_batch_body(b'{"assets": [{"assetId": 1}]}')
    assert not _is_empty_batch_body(b'not json')
