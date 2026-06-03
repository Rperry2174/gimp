# -*- coding: utf-8 -*-
#
#   Shared OpenAI image-edit client for the AI Filter plug-in and tests.
#
#   This program is free software: you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 3 of the License, or
#   (at your option) any later version.

import base64
import json
import mimetypes
import os
import uuid
import urllib.error
import urllib.request

OPENAI_URL = 'https://api.openai.com/v1/images/edits'
OPENAI_MODEL = 'gpt-image-1'
HTTP_TIMEOUT = 180


class AiFilterError(Exception):
    """Raised when the OpenAI image-edit request cannot complete."""


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


def guess_image_mime(file_path):
    mime, _ = mimetypes.guess_type(file_path)
    if mime and mime.startswith('image/'):
        return mime
    return 'image/png'


def call_openai_edit(api_key, image_path, prompt, size='auto'):
    """POST the image + prompt to OpenAI and return the decoded result bytes."""
    fields = {
        'model': OPENAI_MODEL,
        'prompt': prompt,
        'n': '1',
    }
    if size and size != 'auto':
        fields['size'] = size

    file_name = os.path.basename(image_path)
    file_mime = guess_image_mime(image_path)
    body, content_type = build_multipart(fields, 'image', image_path,
                                         file_name, file_mime)

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
        raise AiFilterError('API returned HTTP %d' % error.code +
                            ((': ' + detail[:200]) if detail else ''))
    except urllib.error.URLError as error:
        raise AiFilterError('network error: %s' % error.reason)
    except (TimeoutError, OSError) as error:
        raise AiFilterError('request failed: %s' % error)

    data = payload.get('data')
    if not data or 'b64_json' not in data[0]:
        raise AiFilterError('response did not contain image data')

    return base64.b64decode(data[0]['b64_json'])
