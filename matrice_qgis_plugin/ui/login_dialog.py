"""
Form di login (sezione 2): un solo pulsante "Accedi", nessun campo tecnico
visibile (host/porta/schema sono preconfigurati nell'installazione).
"""

import os

from qgis.PyQt import uic
from qgis.PyQt.QtWidgets import QDialog

FORM_CLASS, _ = uic.loadUiType(os.path.join(os.path.dirname(__file__), "login_dialog.ui"))


class LoginDialog(QDialog, FORM_CLASS):
    def __init__(self, parent=None, remember_default=False, verify_callback=None,
                 username_default="", password_default=""):
        """
        `verify_callback(username, password, remember_me) -> (ok, error_message)`:
        chiamato quando si preme "Accedi", PRIMA di chiudere la finestra.
        Se `ok` e' False il dialogo resta aperto e mostra l'errore -- senza
        questo, un login sbagliato chiudeva subito la finestra senza che
        l'operatore vedesse alcun messaggio (l'errore finiva su un pannello
        ancora nascosto). Se omesso, "Accedi" chiude semplicemente la
        finestra senza verificare nulla.

        `username_default`/`password_default`: valori salvati da un login
        precedente con "Ricordami" (SessionManager.saved_credentials()),
        gia' precompilati cosi' l'operatore deve solo premere "Accedi".
        """
        super().__init__(parent)
        self.setupUi(self)
        self.rememberCheckBox.setChecked(remember_default)
        self.usernameEdit.setText(username_default)
        self.passwordEdit.setText(password_default)
        self.errorLabel.setText("")
        self._verify_callback = verify_callback
        # Il file .ui e' scritto a mano (non generato da Qt Designer), quindi
        # il buttonBox non chiude da solo la finestra: senza queste righe
        # "Accedi"/"Annulla" restano senza effetto.
        self.buttonBox.accepted.connect(self._on_accept_clicked)
        self.buttonBox.rejected.connect(self.reject)

    def username(self):
        return self.usernameEdit.text().strip()

    def password(self):
        return self.passwordEdit.text()

    def remember_me(self):
        return self.rememberCheckBox.isChecked()

    def show_error(self, message):
        """Messaggi in linguaggio chiaro, mai codici tecnici (sezione 2)."""
        self.errorLabel.setText(message)

    def _on_accept_clicked(self):
        if self._verify_callback is None:
            self.accept()
            return
        ok, error_message = self._verify_callback(self.username(), self.password(), self.remember_me())
        if ok:
            self.accept()
        else:
            self.show_error(error_message or "Nome utente o password non corretti.")
