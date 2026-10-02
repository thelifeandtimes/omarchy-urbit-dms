import QtQuick
import qs.Commons
import "." as Dm

// Keep the nickname and delivery address on the same line. The second value
// is always parenthesized; elide in narrow rows rather than stacking it.
Item {
  id: root
  property string shipId: ""
  property string nickname: ""
  property bool showSecondary: true
  property color primaryColor: Color.menu.text
  property color secondaryColor: Qt.rgba(primaryColor.r, primaryColor.g, primaryColor.b, 0.72)
  property real primarySize: Style.font.subtitle
  property real secondarySize: Style.font.caption
  property bool prominent: true
  readonly property bool hasNickname: nickname !== "" && nickname !== shipId
  readonly property bool primaryIsShip: Dm.State.showPatps || !hasNickname
  readonly property string primary: primaryIsShip ? shipId : nickname
  readonly property string secondary: !hasNickname ? "" : (primaryIsShip ? nickname : shipId)
  readonly property real gap: Style.space(5)
  implicitWidth: first.implicitWidth + (second.visible ? gap + second.implicitWidth : 0)
  implicitHeight: Math.max(first.implicitHeight, second.visible ? second.implicitHeight : 0)

  Text {
    id: first
    anchors.left: parent.left
    anchors.verticalCenter: parent.verticalCenter
    width: Math.min(implicitWidth, Math.max(0, root.width - (second.visible ? second.width + root.gap : 0)))
    text: root.primary
    color: root.primaryColor
    font.family: root.primaryIsShip ? Style.font.family : "sans-serif"
    font.pixelSize: root.primarySize
    font.bold: root.prominent
    elide: Text.ElideRight
    textFormat: Text.PlainText
  }
  Text {
    id: second
    visible: root.showSecondary && root.secondary !== ""
    anchors.left: first.right
    anchors.leftMargin: root.gap
    anchors.verticalCenter: parent.verticalCenter
    width: Math.min(implicitWidth, Math.max(0, root.width * 0.52))
    text: "(" + root.secondary + ")"
    color: root.secondaryColor
    font.family: root.primaryIsShip ? "sans-serif" : Style.font.family
    font.pixelSize: root.secondarySize
    elide: Text.ElideRight
    textFormat: Text.PlainText
  }
}
