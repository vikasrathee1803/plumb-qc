"""Check discovery seam.

Importing plumb.checks imports every check module, which runs their
register_check decorators and populates the registry. The runner imports
this package once; adding a new check family means adding its module to
this import list, nothing else in the engine changes.
"""

from plumb.checks import (  # noqa: F401 - imported for registration side effects
    ai_review,
    sql_assertions,
    sql_custom,
    sql_meta,
    sql_performance,
    sql_regression,
    sql_static,
    tableau_static,
)
