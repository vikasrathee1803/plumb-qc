# ADR-0011: Parse Tableau workbooks with lxml, not tableaudocumentapi

Date: 2026-06-07. Status: accepted.

The spec's stack lists "tableaudocumentapi plus lxml" for Tableau static
analysis. tableaudocumentapi 0.11 (the current release) imports
distutils, which was removed from the Python standard library in 3.12.
Plumb's pinned runtime is Python 3.11+ and the CI image is 3.12. The
library only imports at all with a setuptools distutils shim present at
runtime, which is fragile and adds a runtime dependency on setuptools.
The library is also unmaintained (last release predates Python 3.10) and
exposes a thin model: it does not surface FIXED LODs, calc formulas,
custom SQL, totals, or RLS functions, which are exactly what the T-*
catalog needs.

Decision: parse .twb and .twbx directly with lxml, which the spec already
lists for this purpose. .twbx is a zip; the contained .twb is the XML we
parse. This removes the broken dependency, keeps the shipped package
clean, and gives full access to the workbook XML the checks require.

Cause for deviating from the named package: it does not import on the
mandated Python version. This clears the "override only with cause" bar.

Reversibility: cheap. Parsing lives behind plumb/checks/_tableau.py; a
future maintained library could replace it without touching the checks.
