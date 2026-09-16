from vesta.legacy import adopt_legacy_environment

from .cli import main

adopt_legacy_environment()
raise SystemExit(main())
