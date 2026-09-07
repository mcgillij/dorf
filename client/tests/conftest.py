import sys
from pathlib import Path

# Make `import bot...` work when pytest runs from client/ (or anywhere).
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
