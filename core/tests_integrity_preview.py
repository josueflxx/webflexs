import os
import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class IntegrityPreviewSettingsTests(SimpleTestCase):
    databases = []

    def test_settings_refuse_main_local_database(self):
        environment = os.environ.copy()
        environment["DJANGO_SETTINGS_MODULE"] = "flexs_project.settings.integrity_preview"
        environment["INTEGRITY_PREVIEW_DATABASE"] = str(Path(settings.BASE_DIR) / "db.sqlite3")
        process = subprocess.run(
            [sys.executable, "-c", "from django.conf import settings; print(settings.DATABASES)"],
            cwd=settings.BASE_DIR,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("rehusa abrir db.sqlite3", process.stderr)
