"""Test setup — keep tests hermetic regardless of local config files.

Point the data-sources loader at a path that does not exist so a developer's
real ``data_sources.json`` never leaks into the test run. Must run before
``nomadata.main`` is imported (conftest is imported first by pytest).
"""

import os

os.environ["NOMADATA_DATA_SOURCES_FILE"] = "__no_data_sources_for_tests__.json"
# Keep tests hermetic: no data sources and no app DB connection.
os.environ["NOMADATA_DATABASE_URL"] = ""
