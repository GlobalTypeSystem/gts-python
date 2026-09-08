"""
Trampoline for ``python -m gts`` when run from the repository root.

The gts/ project directory (src-layout) is picked up by Python as a
namespace package before the installed ``gts`` package, which shadows
the real package.  This __main__.py fixes sys.path so the real
src-layout package is found, then delegates to its CLI entry point.
"""

import os
import sys

# Put the real src-layout package first on sys.path
_src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
sys.path.insert(0, _src)

# Drop the namespace-package artefact so the real package is imported
for _key in [k for k in sys.modules if k == "gts" or k.startswith("gts.")]:
    del sys.modules[_key]

from gts._cli import main  # noqa: E402

if __name__ == "__main__":
    main()
