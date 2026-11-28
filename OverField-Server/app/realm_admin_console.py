"""Self-hosted operator UI. Only the listed files are served on the owner listener."""
from pathlib import Path
from realm_gacha_catalog import pack

_ROOT = Path(__file__).with_name("admin_ui")
ASSETS = {
    path: (media, (_ROOT / filename).read_bytes())
    for path, media, filename in (
        ("/admin/", "text/html", "index.html"),
        ("/admin/console.css", "text/css", "console.css"),
        ("/admin/reward_picker.js", "text/javascript", "reward_picker.js"),
        ("/admin/mail_editor.js", "text/javascript", "mail_editor.js"),
        ("/admin/editors.js", "text/javascript", "editors.js"),
        ("/admin/results.js", "text/javascript", "results.js"),
        ("/admin/gacha.js", "text/javascript", "gacha.js"),
        ("/admin/gacha.css", "text/css", "gacha.css"),
        ("/admin/form_fields.js", "text/javascript", "form_fields.js"),
        ("/admin/forms.css", "text/css", "forms.css"),
        ("/admin/console.js", "text/javascript", "console.js"),
    )
}

ASSETS.update(pack()[1])
