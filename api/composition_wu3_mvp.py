"""Work Unit 3 (MVP Commercial Demo) composition: PostgreSQL CRM repositories.

Keeps the accepted technical WU4/WU5/WU6 compositions untouched. This module
only builds the CRM repository set over the same fail-fast env configuration
and the shared PostgresRunner, preserving the hexagonal split (ports defined
by the WU1 contracts; psycopg stays inside adapters).
"""

from __future__ import annotations

from api.adapters.postgres_commercial_crm import (
    PostgresContactLeadRepository,
    PostgresConversationRepository,
    PostgresFollowUpRepository,
    PostgresPipelineRepository,
    PostgresPropertyRepository,
)
from api.adapters.postgres_core import ConnectionPool, PostgresRunner, load_database_configuration


class CrmRepositoryComposition:
    """Named bundle of the five CRM repositories for the local demo database."""

    def __init__(self, runner: PostgresRunner):
        self.runner = runner
        self.contacts = PostgresContactLeadRepository(runner)
        self.properties = PostgresPropertyRepository(runner)
        self.pipelines = PostgresPipelineRepository(runner)
        self.conversations = PostgresConversationRepository(runner)
        self.follow_ups = PostgresFollowUpRepository(runner)


def build_crm_composition() -> CrmRepositoryComposition:
    """Fail-fast: raises DatabaseConfigurationError when env is incomplete."""
    config = load_database_configuration()
    pool = ConnectionPool(dict(config))
    return CrmRepositoryComposition(PostgresRunner(pool))
