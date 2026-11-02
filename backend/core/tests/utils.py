from django.core.files.storage import default_storage
from django.test import override_settings

# A fresh, empty InMemoryStorage for the decorated class or test (changing STORAGES resets default_storage)
fresh_storage = override_settings(STORAGES={
	"default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
	"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})


def stored_files(path: str = "") -> list[str]:
	"""Every file name in default_storage under `path`, recursively."""
	dirs, files = default_storage.listdir(path)
	out = [f"{path}{f}" for f in files]
	for d in dirs:
		out += stored_files(f"{path}{d}/")
	return out
