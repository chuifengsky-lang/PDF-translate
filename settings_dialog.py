"""Settings dialog: choose provider + model, enter API key.

API key is stored in the OS credential store via `keyring` (with a QSettings
fallback if keyring is unavailable). Provider/model live in QSettings.
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

from llm import PROVIDERS, models_for

KEYRING_SERVICE = "pdf-translate"
ORG = "pdf-translate"
APP = "pdf-translate"


def _get_key(provider):
    if HAVE_KEYRING:
        try:
            return keyring.get_password(KEYRING_SERVICE, provider) or ""
        except Exception:
            pass
    return QSettings(ORG, APP).value("api_key_%s" % provider, "")


def load_settings():
    """Return (provider, model, api_key)."""
    s = QSettings(ORG, APP)
    provider = s.value("provider", "deepseek")
    default_model = PROVIDERS.get(provider, {}).get("default_model", "")
    model = s.value("model", default_model)
    return provider, model, _get_key(provider)


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
        s.setValue("api_key_%s" % provider, api_key)


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置 / Settings")
        self.setMinimumWidth(440)

        provider, model, api_key = load_settings()

        self.provider_box = QComboBox()
        self.provider_box.addItems(list(PROVIDERS.keys()))
        self.provider_box.setCurrentText(provider)

        self.model_box = QComboBox()
        self.model_box.setEditable(True)
        self.model_box.addItems(models_for(provider))
        self.model_box.setCurrentText(model)

        self.key_edit = QLineEdit(api_key)
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("Paste your DeepSeek API key")

        self.provider_box.currentTextChanged.connect(self._on_provider_changed)

        form = QFormLayout(self)
        form.addRow(QLabel("LLM 提供商:"), self.provider_box)
        form.addRow(QLabel("模型:"), self.model_box)
        form.addRow(QLabel("API Key:"), self.key_edit)

        note = QLabel("DeepSeek key: platform.deepseek.com · "
                      "Base URL: https://api.deepseek.com")
        note.setStyleSheet("color: gray; font-size: 11px;")
        form.addRow(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _on_provider_changed(self, provider):
        self.model_box.clear()
        self.model_box.addItems(models_for(provider))
        self.model_box.setCurrentText(
            PROVIDERS.get(provider, {}).get("default_model", ""))
        self.key_edit.setText(_get_key(provider))

    def values(self):
        return (
            self.provider_box.currentText(),
            self.model_box.currentText().strip(),
            self.key_edit.text().strip(),
        )

    def accept(self):
        provider, model, api_key = self.values()
        save_settings(provider, model, api_key)
        super().accept()
