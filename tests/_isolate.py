"""Import this FIRST in every test module: points HOME/USERPROFILE at a throw-away folder.

`database.db` and `gui.main_window` compute `~/NexusDownloader` when they are imported, so whichever test module
imports them first decides where the tests write. Without this, `unittest discover` (alphabetical order) let
test_appearance import them before test_nexus changed HOME, and the tests wrote into the user's REAL database
and settings.json.
"""
import os
import tempfile

if "NEXUS_TEST_HOME" not in os.environ:
    _home = tempfile.mkdtemp(prefix="nexus_home_")
    os.environ.update(NEXUS_TEST_HOME=_home, HOME=_home, USERPROFILE=_home)
HOME = os.environ["NEXUS_TEST_HOME"]
