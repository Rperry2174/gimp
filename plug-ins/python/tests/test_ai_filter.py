#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
#   Unit tests for the AI Filter plug-in (ai-filter.py).
#
#   These tests focus on the exception-handling paths inside
#   call_openai_edit() to ensure that every kind of network/API failure
#   is converted to AiFilterError so the plug-in always falls back to
#   the local glow instead of crashing and leaving GIMP unresponsive.
#
#   The tests run without a live GIMP session by stubbing out the
#   GObject-Introspection (gi) imports before importing the module
#   under test.

import base64
import importlib
import io
import json
import os
import sys
import types
import unittest
import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Stub out every gi.repository.* import so we can load ai-filter.py
# outside of a running GIMP process.
# ---------------------------------------------------------------------------

def _make_gi_stubs():
    gi_mod = types.ModuleType('gi')
    gi_mod.require_version = lambda name, ver: None

    repo = types.ModuleType('gi.repository')
    gi_mod.repository = repo

    # Most stubs are plain MagicMocks, but Gimp needs special treatment:
    # the plug-in defines `class AiFilter(Gimp.PlugIn)` and then passes
    # `AiFilter.__gtype__` to `Gimp.main()`.  MagicMock refuses to hand
    # out dunder attributes that aren't in its whitelist, so we give
    # Gimp.PlugIn a real Python base-class that carries __gtype__.
    class _FakeGimpPlugInBase:
        __gtype__ = None

    Gimp_stub = MagicMock()
    Gimp_stub.PlugIn = _FakeGimpPlugInBase

    # Make GLib.dgettext a passthrough so _("msg") returns the real string.
    GLib_stub = MagicMock()
    GLib_stub.dgettext = lambda domain, msg: msg
    repo.GLib = GLib_stub
    sys.modules['gi.repository.GLib'] = GLib_stub

    for name in ('GimpUi', 'Gegl', 'GObject', 'Gio'):
        stub = MagicMock()
        setattr(repo, name, stub)
        sys.modules['gi.repository.' + name] = stub

    repo.Gimp = Gimp_stub
    sys.modules['gi.repository.Gimp'] = Gimp_stub

    sys.modules['gi'] = gi_mod
    sys.modules['gi.repository'] = repo


_make_gi_stubs()

# ---------------------------------------------------------------------------
# Import the module under test.
# ai-filter.py is not a package, so we load it with importlib from its path.
# ---------------------------------------------------------------------------

_PLUGIN_PATH = os.path.join(
    os.path.dirname(__file__), '..', 'ai-filter.py'
)

spec = importlib.util.spec_from_file_location('ai_filter', _PLUGIN_PATH)
ai_filter = importlib.util.module_from_spec(spec)
# Prevent Gimp.main() at the bottom from actually running.
ai_filter.__dict__.setdefault('_TEST_IMPORT', True)
with patch('sys.argv', ['ai-filter.py']):
    # Gimp.main is already a MagicMock, so calling it is a no-op.
    spec.loader.exec_module(ai_filter)

AiFilterError = ai_filter.AiFilterError
call_openai_edit = ai_filter.call_openai_edit


# ---------------------------------------------------------------------------
# Helper: build a minimal valid OpenAI JSON response body.
# ---------------------------------------------------------------------------

def _make_valid_response(png_bytes: bytes) -> bytes:
    payload = {'data': [{'b64_json': base64.b64encode(png_bytes).decode()}]}
    return json.dumps(payload).encode('utf-8')


# ---------------------------------------------------------------------------
# A tiny 1×1 red PNG (67 bytes) so we don't need an actual file on disk.
# ---------------------------------------------------------------------------

_TINY_PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADklEQVQI12P4z8BQDwADhQGAWjR9awAAAABJRU5ErkJggg=='
)


class FakeHTTPResponse:
    """Simulate a successful urllib HTTP response."""
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TestCallOpenAIEdit(unittest.TestCase):
    """Tests for call_openai_edit exception-handling paths."""

    # We write a tiny fake PNG to a temp file and point the function at it.
    def setUp(self):
        import tempfile
        fd, self.png_path = tempfile.mkstemp(suffix='.png', prefix='test-aifilter-')
        with os.fdopen(fd, 'wb') as f:
            f.write(_TINY_PNG)

    def tearDown(self):
        try:
            os.remove(self.png_path)
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Happy path: valid JSON response with b64_json
    # ------------------------------------------------------------------
    def test_happy_path_returns_bytes(self):
        body = _make_valid_response(_TINY_PNG)
        with patch('urllib.request.urlopen',
                   return_value=FakeHTTPResponse(body)):
            result = call_openai_edit('key', self.png_path, 'test prompt', 'auto')
        self.assertEqual(result, _TINY_PNG)

    # ------------------------------------------------------------------
    # Timeout / network errors (pre-existing coverage, must still work)
    # ------------------------------------------------------------------
    def test_url_error_raises_ai_filter_error(self):
        with patch('urllib.request.urlopen',
                   side_effect=urllib.error.URLError('timed out')):
            with self.assertRaises(AiFilterError):
                call_openai_edit('key', self.png_path, 'prompt', 'auto')

    def test_timeout_error_raises_ai_filter_error(self):
        with patch('urllib.request.urlopen',
                   side_effect=TimeoutError('connection timed out')):
            with self.assertRaises(AiFilterError):
                call_openai_edit('key', self.png_path, 'prompt', 'auto')

    def test_os_error_raises_ai_filter_error(self):
        with patch('urllib.request.urlopen',
                   side_effect=OSError('connection reset')):
            with self.assertRaises(AiFilterError):
                call_openai_edit('key', self.png_path, 'prompt', 'auto')

    def test_http_error_raises_ai_filter_error(self):
        http_err = urllib.error.HTTPError(
            url=None, code=504, msg='Gateway Timeout',
            hdrs=None, fp=io.BytesIO(b'gateway timeout'))
        with patch('urllib.request.urlopen', side_effect=http_err):
            with self.assertRaises(AiFilterError) as ctx:
                call_openai_edit('key', self.png_path, 'prompt', 'auto')
        self.assertIn('504', str(ctx.exception))

    # ------------------------------------------------------------------
    # THE BUG: invalid / truncated JSON body was previously uncaught.
    # This is the regression test for the fix.
    # ------------------------------------------------------------------
    def test_invalid_json_response_raises_ai_filter_error(self):
        """
        Regression test: when the server returns non-JSON bytes (e.g. a
        truncated body after a timeout, or an HTML error page), the old
        code let json.JSONDecodeError propagate uncaught, which crashed
        the plug-in and left GIMP's progress spinner running forever.
        The fix must convert this to AiFilterError so the fallback fires.
        """
        truncated_body = b'{"data": [{ "b64_json":'  # cut off mid-JSON
        with patch('urllib.request.urlopen',
                   return_value=FakeHTTPResponse(truncated_body)):
            with self.assertRaises(AiFilterError) as ctx:
                call_openai_edit('key', self.png_path, 'prompt', 'auto')
        self.assertIn('JSON', str(ctx.exception))

    def test_html_error_page_raises_ai_filter_error(self):
        """An HTML 'Bad Gateway' body is also non-JSON."""
        html_body = b'<html><body>Bad Gateway</body></html>'
        with patch('urllib.request.urlopen',
                   return_value=FakeHTTPResponse(html_body)):
            with self.assertRaises(AiFilterError):
                call_openai_edit('key', self.png_path, 'prompt', 'auto')

    def test_empty_response_body_raises_ai_filter_error(self):
        """An empty response body should also raise AiFilterError, not crash."""
        with patch('urllib.request.urlopen',
                   return_value=FakeHTTPResponse(b'')):
            with self.assertRaises(AiFilterError):
                call_openai_edit('key', self.png_path, 'prompt', 'auto')

    # ------------------------------------------------------------------
    # Unexpected exception from urlopen (e.g. SSL error on some builds)
    # ------------------------------------------------------------------
    def test_unexpected_exception_raises_ai_filter_error(self):
        """
        Any unexpected exception from urlopen (not URLError/HTTPError/OSError)
        must also be wrapped in AiFilterError so the fallback fires.
        """
        class WeirdNetworkError(Exception):
            pass

        with patch('urllib.request.urlopen',
                   side_effect=WeirdNetworkError('something exotic')):
            with self.assertRaises(AiFilterError):
                call_openai_edit('key', self.png_path, 'prompt', 'auto')

    # ------------------------------------------------------------------
    # Missing image data in an otherwise valid JSON response
    # ------------------------------------------------------------------
    def test_missing_data_field_raises_ai_filter_error(self):
        body = json.dumps({'data': []}).encode('utf-8')
        with patch('urllib.request.urlopen',
                   return_value=FakeHTTPResponse(body)):
            with self.assertRaises(AiFilterError):
                call_openai_edit('key', self.png_path, 'prompt', 'auto')

    def test_missing_b64_json_field_raises_ai_filter_error(self):
        body = json.dumps({'data': [{'url': 'http://example.com/img.png'}]}).encode('utf-8')
        with patch('urllib.request.urlopen',
                   return_value=FakeHTTPResponse(body)):
            with self.assertRaises(AiFilterError):
                call_openai_edit('key', self.png_path, 'prompt', 'auto')


if __name__ == '__main__':
    unittest.main()
