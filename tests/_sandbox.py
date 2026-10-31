"""Imported first by every test module: the whole run gets a throwaway home, so a test that forgets to set
its own can never write into yours (a test once left ~/.config/self-improving-brain/host behind)."""
import atexit
import os
import shutil
import tempfile

_home = tempfile.mkdtemp(prefix="sib-test-home-")
atexit.register(shutil.rmtree, _home, True)
os.environ["HOME"] = _home
for key, sub in (("XDG_CONFIG_HOME", ".config"), ("XDG_CACHE_HOME", ".cache"), ("XDG_DATA_HOME", ".local/share")):
    os.environ[key] = os.path.join(_home, sub)
for key in ("BRAIN_VAULT", "BRAIN_HOST"):
    os.environ.pop(key, None)
