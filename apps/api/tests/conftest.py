"""Test setup — keep tests hermetic regardless of local config files.

Point the data-sources loader at a path that does not exist so a developer's
real ``data_sources.json`` never leaks into the test run. Must run before
``nomadata.main`` is imported (conftest is imported first by pytest).
"""

import os

# Keep tests hermetic: no app DB connection (so no data sources / semantic model
# are registered). Must run before nomadata.main is imported.
os.environ["NOMADATA_DATABASE_URL"] = ""
