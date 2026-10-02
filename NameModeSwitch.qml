import QtQuick
import QtQuick.Layouts
import qs.Commons
import qs.Ui as Ui
import "." as Dm

Item {
  id: root
  property color foreground: Color.menu.text
  readonly property color dim: Qt.rgba(foreground.r, foreground.g, foreground.b, 0.72)
  implicitWidth: labels.implicitWidth
  implicitHeight: labels.implicitHeight
  activeFocusOnTab: true
  Accessible.role: Accessible.CheckBox
  Accessible.name: "Show Urbit IDs instead of nicknames"
  Accessible.checked: Dm.State.showPatps
  Accessible.onToggleAction: Dm.State.showPatps = !Dm.State.showPatps
  Keys.onSpacePressed: Dm.State.showPatps = !Dm.State.showPatps
  Keys.onReturnPressed: Dm.State.showPatps = !Dm.State.showPatps
  RowLayout {
    id: labels
    anchors.fill: parent
    spacing: Style.space(5)
    Text { text: "Nicknames"; color: Dm.State.showPatps ? root.dim : root.foreground; font.family: Style.font.family; font.pixelSize: Style.font.caption; textFormat: Text.PlainText }
    Ui.ToggleSwitch { checked: Dm.State.showPatps; interactive: false; cursorRing: false; trackHeight: Style.space(18); foreground: root.foreground }
    Text { text: "~ships"; color: Dm.State.showPatps ? root.foreground : root.dim; font.family: Style.font.family; font.pixelSize: Style.font.caption; textFormat: Text.PlainText }
  }
  MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: Dm.State.showPatps = !Dm.State.showPatps }
}
