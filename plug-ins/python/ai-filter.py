#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
#   AI Filter - AI image transform plug-ins for GIMP
#
#   "AI Filter" is an umbrella category under Filters. Each style is its own
#   menu entry (Filters > AI Filter > ...). A style sends the flattened image
#   plus a built-in prompt to OpenAI's image-edit API and brings the result
#   back as a new, non-destructive layer. If the AI call cannot be made
#   (no API key, offline, timeout, error response), it falls back to a local
#   GEGL "glow" so the filter always does something.
#
#   To add a new style: add one entry to STYLES below. Nothing else changes.
#
#   This program is free software: you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 3 of the License, or
#   (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program.  If not, see <https://www.gnu.org/licenses/>.

import gi
gi.require_version('Gimp', '3.0')
from gi.repository import Gimp
gi.require_version('Gegl', '0.4')
from gi.repository import Gegl
from gi.repository import GObject
from gi.repository import GLib
from gi.repository import Gio

import base64
import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid

def N_(message): return message
def _(message): return GLib.dgettext(None, message)

OPENAI_URL = 'https://api.openai.com/v1/images/edits'
OPENAI_MODEL = 'gpt-image-1'
HTTP_TIMEOUT = 180
MENU_PATH = '<Image>/Filters/AI Filter'
ENV_FILE_NAME = 'ai-filter.env'
SOURCE_ENV_FILE_PARTS = ('plug-ins', 'python', ENV_FILE_NAME)

# The catalog of AI Filter styles. Each key is a unique PDB procedure name;
# add a new entry here to add a new menu item under Filters > AI Filter.
STYLES = {
    'python-fu-ai-filter-baby-boardroom': {
        'label': N_("Baby _Boardroom..."),
        'blurb': N_("Turn everyone into babies running a serious board meeting"),
        'prompt': (
            "Turn every person into a chubby adorable BABY wearing a tiny "
            "tailored business suit, but keep each person's recognizable face "
            "and hairstyle on the baby. They are conducting an extremely "
            "serious, high-stakes board meeting with furrowed brows. Oversized "
            "conference room makes them look tiny. Hilarious and absurd, "
            "photorealistic."
        ),
    },
}


class AiFilterError(Exception):
    """Raised when the AI path cannot complete; triggers the local fallback."""


# ---------------------------------------------------------------------------
# API key discovery
# ---------------------------------------------------------------------------

def is_gimp_repo_root(path):
    return (os.path.isfile(os.path.join(path, 'meson.build')) and
            os.path.isdir(os.path.join(path, 'app')) and
            os.path.isdir(os.path.join(path, 'plug-ins')))


def find_gimp_repo_roots():
    """Find likely source checkouts from the current process context."""
    roots = []
    starts = [
        os.environ.get('GIMP_SOURCE_DIR'),
        os.getcwd(),
        os.path.dirname(os.path.abspath(__file__)),
    ]

    for start in starts:
        if not start:
            continue

        current = os.path.abspath(start)
        while True:
            if is_gimp_repo_root(current):
                if current not in roots:
                    roots.append(current)

            sibling = os.path.join(os.path.dirname(current), 'gimp')
            if is_gimp_repo_root(sibling) and sibling not in roots:
                roots.append(sibling)

            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent

    return roots


def iter_openai_env_files():
    """Yield the one supported env-file location in install and source trees."""
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ENV_FILE_NAME),
    ]
    for root in find_gimp_repo_roots():
        candidates.append(os.path.join(root, *SOURCE_ENV_FILE_PARTS))

    seen = set()
    for path in candidates:
        if path not in seen:
            seen.add(path)
            yield path


def unquote_env_value(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        quote = value[0]
        value = value[1:-1]
        if quote == '"':
            value = bytes(value, 'utf-8').decode('unicode_escape')
    else:
        value = value.split('#', 1)[0].strip()

    return value


def read_openai_api_key(path):
    try:
        with open(path, 'r', encoding='utf-8') as env_file:
            for line in env_file:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue

                if line.startswith('export '):
                    line = line[len('export '):].lstrip()

                name, separator, value = line.partition('=')
                if separator and name.strip() == 'OPENAI_API_KEY':
                    return unquote_env_value(value)
    except (OSError, UnicodeDecodeError):
        pass

    return None


def get_openai_api_key():
    api_key = os.environ.get('OPENAI_API_KEY')
    if api_key:
        return api_key

    for path in iter_openai_env_files():
        api_key = read_openai_api_key(path)
        if api_key:
            os.environ['OPENAI_API_KEY'] = api_key
            return api_key

    return None


# ---------------------------------------------------------------------------
# Handing the picture to the AI
# ---------------------------------------------------------------------------

def export_flattened_png(image):
    """Duplicate + flatten the image and save it to a temporary PNG."""
    dup = image.duplicate()
    dup.flatten()

    fd, path = tempfile.mkstemp(suffix='.png', prefix='aifilter-in-')
    os.close(fd)

    ok = Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, dup,
                        Gio.File.new_for_path(path), None)
    dup.delete()

    if not ok:
        raise AiFilterError(_("could not export the image to PNG"))
    return path


def build_multipart(fields, file_field, file_path, file_name, file_mime):
    """Build a multipart/form-data body using only the standard library."""
    boundary = '----AiFilterBoundary' + uuid.uuid4().hex
    crlf = b'\r\n'
    parts = []

    for name, value in fields.items():
        parts.append(b'--' + boundary.encode())
        parts.append(('Content-Disposition: form-data; name="%s"' % name).encode())
        parts.append(b'')
        parts.append(str(value).encode())

    with open(file_path, 'rb') as handle:
        file_data = handle.read()

    parts.append(b'--' + boundary.encode())
    parts.append(('Content-Disposition: form-data; name="%s"; filename="%s"'
                  % (file_field, file_name)).encode())
    parts.append(('Content-Type: %s' % file_mime).encode())
    parts.append(b'')
    parts.append(file_data)

    parts.append(b'--' + boundary.encode() + b'--')
    parts.append(b'')

    body = crlf.join(parts)
    content_type = 'multipart/form-data; boundary=%s' % boundary
    return body, content_type


def call_openai_edit(api_key, png_path, prompt, size):
    """POST the image + prompt to OpenAI and return the decoded result bytes."""
    fields = {
        'model': OPENAI_MODEL,
        'prompt': prompt,
        'n': '1',
    }
    if size and size != 'auto':
        fields['size'] = size

    body, content_type = build_multipart(fields, 'image', png_path,
                                         'image.png', 'image/png')

    request = urllib.request.Request(OPENAI_URL, data=body, method='POST')
    request.add_header('Authorization', 'Bearer ' + api_key)
    request.add_header('Content-Type', content_type)

    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            payload = json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as error:
        detail = ''
        try:
            detail = error.read().decode('utf-8', 'replace')
        except Exception:
            pass
        raise AiFilterError(_("API returned HTTP %d") % error.code +
                            ((': ' + detail[:200]) if detail else ''))
    except urllib.error.URLError as error:
        raise AiFilterError(_("network error: %s") % error.reason)
    except (TimeoutError, OSError) as error:
        raise AiFilterError(_("request failed: %s") % error)

    data = payload.get('data')
    if not data or 'b64_json' not in data[0]:
        raise AiFilterError(_("response did not contain image data"))

    return base64.b64decode(data[0]['b64_json'])


def call_openai_edit_with_progress(api_key, png_path, prompt, size):
    """Run call_openai_edit on a worker thread, pulsing GIMP's progress bar.

    The OpenAI request is a blocking call that can take a long time. Doing it
    inline freezes the UI with no feedback, so we run it on a background thread
    and keep the progress bar animating (pulsing) on the main thread until the
    request finishes. This is the only visible "something is happening" signal
    the user gets while the AI is working.
    """
    result = {}

    def worker():
        try:
            result['bytes'] = call_openai_edit(api_key, png_path, prompt, size)
        except BaseException as error:  # propagated to the caller's thread
            result['error'] = error

    thread = threading.Thread(target=worker, name='ai-filter-openai')
    thread.daemon = True
    thread.start()

    while thread.is_alive():
        Gimp.progress_pulse()
        time.sleep(0.12)
        thread.join(0.0)

    if 'error' in result:
        raise result['error']

    return result.get('bytes')


# ---------------------------------------------------------------------------
# Bringing the result back
# ---------------------------------------------------------------------------

def write_temp_png(data):
    fd, path = tempfile.mkstemp(suffix='.png', prefix='aifilter-out-')
    with os.fdopen(fd, 'wb') as handle:
        handle.write(data)
    return path


def insert_result_layer(image, result_bytes, layer_name):
    """Load the AI result and insert it as a new top layer."""
    path = write_temp_png(result_bytes)
    try:
        layer = Gimp.file_load_layer(Gimp.RunMode.NONINTERACTIVE, image,
                                     Gio.File.new_for_path(path))
        layer.set_name(layer_name)

        # The layer must belong to the image before it can be scaled.
        image.insert_layer(layer, None, 0)

        width = image.get_width()
        height = image.get_height()
        if layer.get_width() != width or layer.get_height() != height:
            layer.scale(width, height, False)
        layer.set_offsets(0, 0)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Graceful fallback: local GEGL glow
# ---------------------------------------------------------------------------

def apply_local_glow(image, strength, layer_name):
    """Snapshot the composite into a new layer and apply a GEGL glow."""
    dup = image.duplicate()
    dup.flatten()
    flat = dup.get_layers()[0]
    layer = Gimp.Layer.new_from_drawable(flat, image)
    dup.delete()

    layer.set_name(layer_name)
    image.insert_layer(layer, None, 0)

    brightness = max(0.05, min(1.0, 0.1 + 0.6 * (strength / 100.0)))

    try:
        glow = Gimp.DrawableFilter.new(layer, 'gegl:softglow', '')
        config = glow.get_config()
        config.set_property('glow-radius', 10.0)
        config.set_property('brightness', brightness)
        config.set_property('sharpness', 0.85)
        layer.merge_filter(glow)
    except Exception:
        bloom = Gimp.DrawableFilter.new(layer, 'gegl:bloom', '')
        config = bloom.get_config()
        try:
            config.set_property('strength', brightness * 100.0)
        except Exception:
            pass
        layer.merge_filter(bloom)


# ---------------------------------------------------------------------------
# Run callback (shared by every style)
# ---------------------------------------------------------------------------

def run_ai_filter(procedure, run_mode, image, drawables, config, data):
    style = STYLES.get(procedure.get_name())
    if style is None:
        return procedure.new_return_values(Gimp.PDBStatusType.CALLING_ERROR,
                                           GLib.Error())
    prompt = style['prompt']
    layer_label = _(style['label']).replace('_', '').rstrip('.').strip()

    if run_mode == Gimp.RunMode.INTERACTIVE:
        gi.require_version('GimpUi', '3.0')
        from gi.repository import GimpUi

        GimpUi.init('python-fu-ai-filter')

        dialog = GimpUi.ProcedureDialog(procedure=procedure, config=config)
        dialog.fill(None)
        if not dialog.run():
            dialog.destroy()
            return procedure.new_return_values(Gimp.PDBStatusType.CANCEL,
                                               GLib.Error())
        dialog.destroy()

    size = config.get_property('size')
    glow_strength = config.get_property('glow-strength')

    Gegl.init(None)
    Gimp.context_push()
    image.undo_group_start()
    Gimp.progress_init(_("AI Filter: preparing image…"))

    tmp_in = None
    used_fallback = False
    fallback_reason = None

    try:
        result_bytes = None
        try:
            api_key = get_openai_api_key()
            if not api_key:
                raise AiFilterError(_("OPENAI_API_KEY is not set in the "
                                      "environment or a repo env file"))

            tmp_in = export_flattened_png(image)
            Gimp.progress_set_text(_("AI Filter: contacting AI model "
                                     "(this can take a while)…"))
            result_bytes = call_openai_edit_with_progress(api_key, tmp_in,
                                                          prompt, size)
        except AiFilterError as error:
            used_fallback = True
            fallback_reason = str(error)

        if result_bytes is not None and not used_fallback:
            Gimp.progress_set_text(_("AI Filter: adding result layer…"))
            insert_result_layer(image, result_bytes, layer_label)
        else:
            Gimp.progress_set_text(_("AI Filter: applying local glow…"))
            Gimp.message(_("AI Filter: AI request unavailable (%s). "
                           "Applying a local glow instead.") % fallback_reason)
            apply_local_glow(image, glow_strength, layer_label + _(" (local)"))

        Gimp.progress_update(1.0)
        Gimp.displays_flush()
    finally:
        Gimp.progress_end()
        image.undo_group_end()
        Gimp.context_pop()
        if tmp_in:
            try:
                os.remove(tmp_in)
            except OSError:
                pass

    return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())


# ---------------------------------------------------------------------------
# Plug-in registration
# ---------------------------------------------------------------------------

class AiFilter(Gimp.PlugIn):
    ## GimpPlugIn virtual methods ##
    def do_set_i18n(self, procname):
        return True, 'gimp30-python', None

    def do_query_procedures(self):
        return list(STYLES.keys())

    def do_create_procedure(self, name):
        style = STYLES.get(name)
        if style is None:
            return None

        procedure = Gimp.ImageProcedure.new(self, name,
                                            Gimp.PDBProcType.PLUGIN,
                                            run_ai_filter, None)

        procedure.set_image_types("RGB*, GRAY*")
        procedure.set_sensitivity_mask(Gimp.ProcedureSensitivityMask.DRAWABLE |
                                       Gimp.ProcedureSensitivityMask.DRAWABLES)
        procedure.set_documentation(_(style['blurb']),
                                    _("Transforms the image with an AI model. "
                                      "Falls back to a local glow effect when "
                                      "the AI call is unavailable."),
                                    name)
        procedure.set_menu_label(_(style['label']))
        procedure.set_attribution("AI Filter", "AI Filter", "2026")
        # The image right-click menu mirrors the <Image> tree, so this single
        # path registers the style in both the menu bar and the right-click menu.
        procedure.add_menu_path(MENU_PATH)

        size_choice = Gimp.Choice.new()
        size_choice.add("auto",      0, _("Auto (match source)"), "")
        size_choice.add("1024x1024", 1, _("Square - 1024x1024"), "")
        size_choice.add("1536x1024", 2, _("Landscape - 1536x1024"), "")
        size_choice.add("1024x1536", 3, _("Portrait - 1024x1536"), "")
        procedure.add_choice_argument(
            "size", _("Output _size"), _("Size of the AI-generated image"),
            size_choice, "auto", GObject.ParamFlags.READWRITE)

        procedure.add_double_argument(
            "glow-strength", _("Fallback glow _strength"),
            _("Strength of the local glow used when the AI call fails"),
            0.0, 100.0, 40.0, GObject.ParamFlags.READWRITE)

        return procedure


Gimp.main(AiFilter.__gtype__, sys.argv)
