import QtQuick
import qs.Commons
import qs.Commons as Commons
import qs.Ui as Ui

// Omabit plugin header convention v1. Keep the two reference copies identical.
Ui.PanelHero {
  objectName: "pluginHeader"
  foreground: Commons.Color.popups.text
  fontFamily: Style.font.family
  iconSize: Style.font.display
}
