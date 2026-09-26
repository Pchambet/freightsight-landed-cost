#!/usr/bin/env python3
"""Write `backend/openapi.json` from the application, in the serialisation the file already uses.

    cd backend && .venv/bin/python scripts/export_openapi.py          # write it
    cd backend && .venv/bin/python scripts/export_openapi.py --check  # exit 1 if it is out of date

The front end's types are generated from that file (`cd frontend && npm run api:types`), so it is
regenerated in the same commit as any change to `app/api/v1/schemas.py` or to a route's signature.
No database is needed: the settings only want a URL to exist.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://x:x@localhost:1/x")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app

TARGET = Path(__file__).resolve().parent.parent / "openapi.json"


def main() -> int:
    current = TARGET.read_text() if TARGET.exists() else ""
    # The file is kept with its accents readable; older copies escaped them. Whichever it is, keep it.
    readable = json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n"
    escaped = json.dumps(app.openapi(), indent=2) + "\n"
    wanted = escaped if current and current == escaped else readable
    if current and "\\u00" in current and "\\u00" not in readable:
        wanted = escaped
    if "--check" in sys.argv:
        if current != wanted:
            print("openapi.json is out of date: run scripts/export_openapi.py", file=sys.stderr)
            return 1
        return 0
    TARGET.write_text(wanted)
    print(f"{TARGET.name}: {'unchanged' if current == wanted else 'updated'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
