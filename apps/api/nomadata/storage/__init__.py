"""App metadata persistence — NomaData's own PostgreSQL.

Distinct from data-source connectors: this is where NomaData stores its OWN
artifacts (semantic models, ...). Along with connectors, this is the only place
allowed to import a database driver — but for the app DB, not user data sources.
"""
