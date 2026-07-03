"""Entry point for ``admin-cli`` (console script) and ``python -m admin_cli``.

The console script target is ``admin_cli.__main__:app.meta`` (see pyproject),
so ``app`` is re-exported here; the meta-app carries the global ``--json`` flag
and the clean-error handler.
"""

from __future__ import annotations

from admin_cli.commands import app, main

__all__ = ["app", "main"]


if __name__ == "__main__":
    # ``main()`` -> ``app.meta()`` raises SystemExit with the process return code.
    main()
