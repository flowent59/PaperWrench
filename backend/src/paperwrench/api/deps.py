"""Shared, app-lifecycle-scoped dependencies.

Most PaperWrench endpoints (see ``api/v1/metadata.py``) deliberately build a
short-lived :class:`~paperwrench.paperless.client.PaperlessClient` per
request, because M2 had no consumer that needed anything longer-lived.

The M3 documents API is that consumer: listing a page of documents means
resolving every tag, correspondent, document type and custom field
definition it references, and doing that from scratch on every request would
refetch the same handful of reference collections for every single page a
user turns. :func:`get_metadata_registry` and :func:`get_paperless_client`
hand out the single instances created once in :mod:`paperwrench.main`'s
lifespan, so the TTL cache in :class:`~paperwrench.paperless.registry.MetadataRegistry`
actually gets to do its job.
"""

from __future__ import annotations

from fastapi import Request

from paperwrench.paperless import MetadataRegistry
from paperwrench.paperless import PaperlessClient


def get_paperless_client(request: Request) -> PaperlessClient:
    """The single, app-lifecycle ``PaperlessClient`` created at startup."""
    client: PaperlessClient = request.app.state.paperless_client
    return client


def get_metadata_registry(request: Request) -> MetadataRegistry:
    """The single, app-lifecycle ``MetadataRegistry`` created at startup."""
    registry: MetadataRegistry = request.app.state.metadata_registry
    return registry
