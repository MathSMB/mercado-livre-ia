import os
import subprocess
import sys

if __name__ == "__main__":  # o guard evita rodar de novo nos subprocessos que o Docling/Marker criam no Windows
    if os.name == "nt" and not sys.flags.utf8_mode:
        # No Windows o padrão é cp1252: bibliotecas que abrem arquivo sem encoding quebram ("'charmap' codec").
        # Reabre o Python em modo UTF-8 (vale também para os subprocessos).
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        raise SystemExit(subprocess.call([sys.executable, "-X", "utf8", "-m", "docrag", *sys.argv[1:]], env=env))
    from .cli import main

    raise SystemExit(main())
