import os
import stat
from pathlib import Path


class CredentialError(ValueError):
    pass


def read_control_token(path):
    if not path:
        raise CredentialError("Browser bridge control token is not configured")
    token_file = Path(path)
    try:
        if token_file.is_symlink():
            raise CredentialError("Browser bridge control token file must not be a symlink")
        info = token_file.stat()
        if not stat.S_ISREG(info.st_mode):
            raise CredentialError("Browser bridge control token must be a regular file")
        if os.name == "posix" and stat.S_IMODE(info.st_mode) & 0o077:
            raise CredentialError("Browser bridge control token file permissions must be 0600")
        token = token_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise CredentialError("Browser bridge control token could not be read") from exc
    if len(token) < 32:
        raise CredentialError("Browser bridge control token is invalid")
    return token
