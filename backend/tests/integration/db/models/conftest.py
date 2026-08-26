"""Register storage and lifespan fixtures shared by migrated DB integration tests."""

from ._ingestion_atomicity_shared import model as model
from ._ingestion_atomicity_shared import storage as storage
from ._main_lifespan_shared import _local_storage as _local_storage
