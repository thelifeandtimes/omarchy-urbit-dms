import QtQuick
import qs.Commons
import qs.Commons as Commons
import qs.Ui as Ui

// Omabit plugin sign-in convention v1. Transport stays in the owning plugin.
Column {
  id: root
  property bool busy: false
  property bool omabitAvailable: true
  property color foreground: Commons.Color.popups.text
  signal connectOmabit()
  signal directCredentials()
  spacing: Style.space(8)
  Ui.Button {
    objectName: "connect-via-omabit"
    text: root.busy ? "Opening Omabit…" : "Connect with Omabit"
    foreground: root.foreground
    focusable: true
    bordered: true
    enabled: !root.busy && root.omabitAvailable
    onClicked: root.connectOmabit()
  }
  Ui.Button {
    objectName: "direct-credentials"
    text: "Enter direct credentials"
    foreground: Qt.darker(root.foreground, 1.4)
    focusable: true
    bordered: false
    onClicked: root.directCredentials()
  }
}
