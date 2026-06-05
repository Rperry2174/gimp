#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
#   Unit tests for the AI Filter plug-in's error handling in call_openai_edit.
#
#   These tests run standalone (no GIMP runtime required) by monkey-patching the
#   GIMP-dependent imports before importing the module under test.

import importlib.util
import io
import json
import os
import sys
import types
import unittest
import unittest.mock
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# Stub out GIMP's GI bindings so the module can be imported without a running
# GIMP session.
# ---------------------------------------------------------------------------

def _make_stub_module(name):
    mod = types.ModuleType(name)
    return mod


gi_mod = _make_stub_module('gi')
gi_mod.require_version = lambda *a, **kw: None


class _StubPlugIn:
    """Minimal stand-in for Gimp.PlugIn so the class body can be parsed."""
    __gtype__ = None


gimp_mod = _make_stub_module('gi.repository.Gimp')
gimp_mod.PlugIn = _StubPlugIn
gimp_mod.main = lambda *a, **kw: None
# Stub out every Gimp.* name the module references at import time.
for _name in ('PDBStatusType', 'ProcedureSensitivityMask', 'RunMode',
              'ImageProcedure', 'Choice', 'DrawableFilter',
              'Layer', 'context_push', 'context_pop', 'displays_flush',
              'message', 'file_save', 'file_load_layer'):
    setattr(gimp_mod, _name, type(_name, (), {
        '__getattr__': lambda self, k: None,
    })())

gegl_mod = _make_stub_module('gi.repository.Gegl')
gegl_mod.init = lambda *a: None

gobject_mod = _make_stub_module('gi.repository.GObject')
gobject_mod.ParamFlags = type('ParamFlags', (), {'READWRITE': 0})()

glib_mod = _make_stub_module('gi.repository.GLib')
glib_mod.dgettext = lambda domain, msg: msg
glib_mod.Error = Exception

gio_mod = _make_stub_module('gi.repository.Gio')

repo_mod = types.ModuleType('gi.repository')
repo_mod.Gimp = gimp_mod
repo_mod.Gegl = gegl_mod
repo_mod.GObject = gobject_mod
repo_mod.GLib = glib_mod
repo_mod.Gio = gio_mod

gi_mod.repository = repo_mod

sys.modules.setdefault('gi', gi_mod)
sys.modules.setdefault('gi.repository', repo_mod)
sys.modules.setdefault('gi.repository.Gimp', gimp_mod)
sys.modules.setdefault('gi.repository.Gegl', gegl_mod)
sys.modules.setdefault('gi.repository.GObject', gobject_mod)
sys.modules.setdefault('gi.repository.GLib', glib_mod)
sys.modules.setdefault('gi.repository.Gio', gio_mod)

# ---------------------------------------------------------------------------
# Import the plug-in module.
# ---------------------------------------------------------------------------

_PLUGIN_PATH = os.path.join(
    os.path.dirname(__file__), '..', 'ai-filter.py')

spec = importlib.util.spec_from_file_location('ai_filter', _PLUGIN_PATH)
ai_filter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ai_filter)

AiFilterError = ai_filter.AiFilterError
call_openai_edit = ai_filter.call_openai_edit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeHTTPResponse:
    """Minimal stand-in for http.client.HTTPResponse / urllib response."""
    def __init__(self, body_bytes, status=200):
        self._body = body_bytes
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def _make_http_error(code, body_bytes):
    resp = FakeHTTPResponse(body_bytes, code)
    err = urllib.error.HTTPError(
        url='https://example.com', code=code,
        msg='Error', hdrs=None, fp=io.BytesIO(body_bytes))
    return err


# ---------------------------------------------------------------------------
# Helpers for patching network I/O without touching the filesystem.
# build_multipart opens the PNG file, so we stub it out everywhere.
# ---------------------------------------------------------------------------

_FAKE_MULTIPART = (b'--boundary\r\nContent-Disposition: form-data\r\n\r\n', 'multipart/form-data; boundary=boundary')


def _call(api_key, urlopen_side_effect=None, urlopen_return=None):
    """Invoke call_openai_edit with all file/network I/O patched."""
    with unittest.mock.patch.object(ai_filter, 'build_multipart',
                                    return_value=_FAKE_MULTIPART):
        with unittest.mock.patch('urllib.request.urlopen',
                                 side_effect=urlopen_side_effect,
                                 return_value=urlopen_return):
            return call_openai_edit(api_key, '/fake.png', 'prompt', 'auto')


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCallOpenaiEditErrorHandling(unittest.TestCase):

    # -- HTTP errors ----------------------------------------------------------

    def test_http_error_extracts_openai_message(self):
        """HTTP error with a well-formed OpenAI JSON body surfaces the message."""
        body = json.dumps({
            'error': {
                'message': 'Incorrect API key provided',
                'type': 'invalid_request_error',
                'code': 'invalid_api_key',
            }
        }).encode()
        err = _make_http_error(401, body)

        with self.assertRaises(AiFilterError) as ctx:
            _call('bad-key', urlopen_side_effect=err)

        msg = str(ctx.exception)
        self.assertIn('401', msg)
        self.assertIn('Incorrect API key provided', msg,
                      "Human-readable API message must appear in the error")
        self.assertNotIn('"error"', msg,
                         "Raw JSON keys must not appear in the error message")

    def test_http_error_fallback_when_body_not_json(self):
        """HTTP error with a non-JSON body still raises AiFilterError (no traceback)."""
        err = _make_http_error(503, b'Service Unavailable')

        with self.assertRaises(AiFilterError) as ctx:
            _call('key', urlopen_side_effect=err)

        self.assertIn('503', str(ctx.exception))

    def test_http_error_fallback_when_body_missing_message(self):
        """HTTP error with JSON that lacks error.message falls back to HTTP code."""
        body = json.dumps({'status': 'error'}).encode()
        err = _make_http_error(500, body)

        with self.assertRaises(AiFilterError) as ctx:
            _call('key', urlopen_side_effect=err)

        self.assertIn('500', str(ctx.exception))

    # -- Network / OS errors --------------------------------------------------

    def test_url_error_is_wrapped(self):
        err = urllib.error.URLError(reason='Name or service not known')

        with self.assertRaises(AiFilterError) as ctx:
            _call('key', urlopen_side_effect=err)

        self.assertIn('network error', str(ctx.exception))

    def test_timeout_is_wrapped(self):
        with self.assertRaises(AiFilterError) as ctx:
            _call('key', urlopen_side_effect=TimeoutError('timed out'))

        self.assertIn('request failed', str(ctx.exception))

    # -- Malformed successful responses ---------------------------------------

    def test_non_json_success_response_raises_ai_filter_error(self):
        """A 200 OK response with non-JSON body raises AiFilterError, not ValueError."""
        fake_resp = FakeHTTPResponse(b'<html>Not JSON</html>')

        with self.assertRaises(AiFilterError) as ctx:
            _call('key', urlopen_return=fake_resp)

        self.assertIn('invalid JSON', str(ctx.exception))

    def test_missing_data_field_raises_ai_filter_error(self):
        """A 200 response with no 'data' field raises AiFilterError."""
        body = json.dumps({'status': 'ok'}).encode()
        fake_resp = FakeHTTPResponse(body)

        with self.assertRaises(AiFilterError) as ctx:
            _call('key', urlopen_return=fake_resp)

        self.assertIn('image data', str(ctx.exception))

    def test_corrupt_base64_raises_ai_filter_error(self):
        """A response with malformed base64 raises AiFilterError, not binascii.Error."""
        body = json.dumps({'data': [{'b64_json': '!!!not-valid-base64!!!'}]}).encode()
        fake_resp = FakeHTTPResponse(body)

        with self.assertRaises(AiFilterError) as ctx:
            _call('key', urlopen_return=fake_resp)

        self.assertIn('decode', str(ctx.exception))

    def test_valid_response_returns_bytes(self):
        """A well-formed response returns the decoded image bytes."""
        import base64 as _b64
        png_bytes = b'\x89PNG fake'
        body = json.dumps(
            {'data': [{'b64_json': _b64.b64encode(png_bytes).decode()}]}
        ).encode()
        fake_resp = FakeHTTPResponse(body)

        result = _call('key', urlopen_return=fake_resp)

        self.assertEqual(result, png_bytes)


if __name__ == '__main__':
    unittest.main()
