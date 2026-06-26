"""Settings dialog: choose provider, enter API key, set model.

API key is stored in the OS credential store via `keyring` (not in plain text).
Provider/model preferences live in QSettings.
"""

from PyQt6.QtWidgets import (
    QDialog, QFormLayout, QComboBox, QLineEdit, QDialogButtonBox, QLabel
)
from PyQt6.QtCore import QSettings

try:
    import keyring
    HAVE_KEYRING = True
except Exception:
    HAVE_KEYRING = False

from llm import PROVIDERS

KEYRING_SERVICE = "pdf-translate"
ORG = "pdf-translate"
APP = "pdf-translate"


def load_settings():
    """Return (provider, model, api_key)."""
    s = QSettings(ORG, APP)
    provider = s.value("provider", "deepseek")
    model = s.value("model", PROVIDERS.get(provider, {}).get("default_model", ""))
    api_key = ""
    if HAVE_KEYRING:
        try:
            api_key = keyring.get_password(KEYRING_SERVICE, provider) or ""
        except Exception:
            api_key = ""
    if not api_key:
        api_key = s.value("api_key_%s" % provider, "")  # fallback if no keyring
    return provider, model, api_key


def save_settings(provider, model, api_key):
    s = QSettings(ORG, APP)
    s.setValue("provider", provider)
    s.setValue("model", model)
    saved = False
    if HAVE_KEYRING:
        try:
            keyring.set_password(KEYRING_SERVICE, provider, api_key)
            saved = True
        except Exception:
            saved = False
    if not saved:
        s.setValue("api_key_%s" % provider, api_key)  # fallback storage


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(420)

        provider, model, api_key = load_settings()

        self.provider_box = QComboBox()
        self.provider_box.addItems(list(PROVIDERS.keys()))
        self.provider_box.setCurrentText(provider)

        self.model_edit = QLineEdit(model)
        self.key_edit = QLineEdit(api_key)
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("Paste your API key")

        self.provider_box.currentTextChanged.connect(self._on_provider_changed)

        form = QFormLayout(self)
        form.addRow(QLabel("LLM provider:"), self.provider_box)
        form.addRow(QLabel("Model:"), self.model_edit)
        form.addRow(QLabel("API key:"), self.key_edit)

        note = QLabel("Get a DeepSeek key at platform.deepseek.com")
        note.setStyleSheet("color: gray; font-size: 11px;")
        form.addRow(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _on_provider_changed(self, provider):
        # update model + key fields to reflect the newly selected provider
        self.model_edit.setText(PROVIDERS.get(provider, {}).get("default_model", ""))
        key = ""
        if HAVE_KEYRING:
            try:
                key = keyring.get_password(KEYRING_SERVICE, provider) or ""
            except Exception:
                key = ""
        self.key_edit.setText(key)

    def values(self):
        return (
            self.provider_box.currentText(),
            self.model_edit.text().strip(),
            self.key_edit.text().strip(),
        )

    def accept(self):
        provider, model, api_key = self.values()
        save_settings(provider, model, api_key)
        super().accept()
