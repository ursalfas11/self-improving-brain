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
# the session markers too: run from inside an agent, the tests must behave as in CI (a test that means
# "an agent runs this command" sets BRAIN_AGENT itself)
for key in ("BRAIN_VAULT", "BRAIN_HOST", "BRAIN_AGENT", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID",
            "CLAUDE_CODE_SSE_PORT"):
    os.environ.pop(key, None)
# run from a git hook (pre-commit, pre-push), git sets GIT_INDEX_FILE, GIT_DIR and friends: every git call a test makes
# in its own throwaway repo would then act on the repo that runs the hook
for key in [k for k in os.environ if k.startswith("GIT_")]:
    os.environ.pop(key, None)
