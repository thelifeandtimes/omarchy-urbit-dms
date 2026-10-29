"""Local plugin instance identity; optional experimental profiles never use default secrets."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat


CANONICAL_ID = "urbit.dms"
STATE_NAME = "omarchy-urbit-dms"
LABEL = "Omarchy Urbit DMs session"


@dataclass(frozen=True)
class Instance:
    plugin_id: str
    profile: str
    state_name: str
    label: str


def read_object(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('Plugin instance metadata must be a regular file')
        data = stream.read(16385)
    if len(data) > 16384:
        raise ValueError('Plugin instance metadata is too large')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate plugin instance field')
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=pairs)


def load_instance(root):
    root = Path(root)
    try:
        config = read_object(root / 'application-profile.json')
    except FileNotFoundError:
        config = {'version': 1, 'profile': 'default'}
    if (not isinstance(config, dict) or set(config) != {'version', 'profile'}
            or type(config['version']) is not int or config['version'] != 1
            or not isinstance(config['profile'], str)
            or not re.fullmatch(r'[a-z][a-z0-9-]{0,31}', config['profile'])):
        raise ValueError('Invalid plugin application profile')
    profile = config['profile']
    suffix = '' if profile == 'default' else '.' + profile
    plugin_id = CANONICAL_ID + suffix
    if read_object(root / 'manifest.json').get('id') != plugin_id:
        raise ValueError('Plugin manifest and application profile do not match')
    label = LABEL + ('' if profile == 'default' else ' (' + profile + ')')
    return Instance(plugin_id, profile, STATE_NAME + suffix, label)


INSTANCE = load_instance(Path(__file__).resolve().parent)
