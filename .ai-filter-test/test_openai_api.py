#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Standalone smoke test for the AI Filter OpenAI image-edit client.
# Requires OPENAI_API_KEY in the environment and .ai-filter-test/test-image.jpg.

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / 'plug-ins' / 'python'))

from ai_filter_openai import AiFilterError, call_openai_edit  # noqa: E402

TEST_DIR = Path(__file__).resolve().parent
DEFAULT_IMAGE = TEST_DIR / 'test-image.jpg'
DEFAULT_OUTPUT = TEST_DIR / 'test-result.png'
DEFAULT_PROMPT = (
    'Turn every person into a chubby adorable BABY wearing a tiny tailored '
    'business suit, but keep each person\'s recognizable face and hairstyle. '
    'Photorealistic.'
)


def main():
    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        print('error: OPENAI_API_KEY is not set', file=sys.stderr)
        return 1

    image_path = Path(os.environ.get('AI_FILTER_TEST_IMAGE', DEFAULT_IMAGE))
    if not image_path.is_file():
        print('error: test image not found: %s' % image_path, file=sys.stderr)
        return 1

    output_path = Path(os.environ.get('AI_FILTER_TEST_OUTPUT', DEFAULT_OUTPUT))
    prompt = os.environ.get('AI_FILTER_TEST_PROMPT', DEFAULT_PROMPT)
    size = os.environ.get('AI_FILTER_TEST_SIZE', '1024x1024')

    print('Calling OpenAI image edit (%s)...' % image_path.name)
    try:
        result_bytes = call_openai_edit(api_key, str(image_path), prompt, size)
    except AiFilterError as error:
        print('error: %s' % error, file=sys.stderr)
        return 1

    output_path.write_bytes(result_bytes)
    print('Wrote %s (%d bytes)' % (output_path, len(result_bytes)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
