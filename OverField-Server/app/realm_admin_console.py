"""Self-hosted operator UI. Only these three files are served on the owner listener."""
from pathlib import Path

_ROOT = Path(__file__).with_name("admin_ui")
ASSETS = {
    path: (media, (_ROOT / filename).read_bytes())
    for path, media, filename in (
        ("/admin/", "text/html", "index.html"),
        ("/admin/console.css", "text/css", "console.css"),
        ("/admin/console.js", "text/javascript", "console.js"),
    )
}
