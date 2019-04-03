from contextlib import contextmanager

import sys
import os
import hashlib


@contextmanager
def suppress_stderr():
    with open(os.devnull, "w") as devnull:
        old_stderr = sys.stderr
        sys.stderr = devnull
        try:
            yield
        finally:
            sys.__stderr__ = old_stderr

def sha256(filename):
    hash_sha256 = hashlib.sha256()

    with open(filename, 'rb') as f:
        for chunk in iter(lambda: f.read(4096), b''):
            hash_sha256.update(chunk)

    return hash_sha256.hexdigest()
