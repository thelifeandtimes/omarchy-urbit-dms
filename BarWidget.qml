import QtQuick
import qs.Commons
import qs.Commons as Commons
import qs.Ui as Ui
import "." as Dm
import "Instance.js" as Instance

Ui.BarWidget {
  id: root
  moduleName: "urbit.dms"
  readonly property string displayName: Instance.label(moduleName)
  readonly property bool opened: panelLoader.item ? panelLoader.item.opened : false
  readonly property bool popoutSwitchClosing: panelLoader.item ? panelLoader.item.popoutSwitchClosing : false

  function injectPanel() {
    if (!panelLoader.item) return
    panelLoader.item.moduleName = root.moduleName
    panelLoader.item.bar = root.bar
    panelLoader.item.anchorItem = bubble
    panelLoader.item.hostWidget = root
  }
  function open() { if (panelLoader.item) panelLoader.item.open() }
  function close() { if (panelLoader.item) panelLoader.item.close() }
  function closeForPopoutSwitch() { if (panelLoader.item) panelLoader.item.closeForPopoutSwitch() }

  implicitWidth: bubble.implicitWidth
  implicitHeight: bubble.implicitHeight
  onBarChanged: injectPanel()
  onModuleNameChanged: injectPanel()

  Loader {
    id: panelLoader
    objectName: "dmsRecentPanelLoader"
    active: true
    visible: false
    source: Qt.resolvedUrl("RecentPanel.qml")
    onLoaded: { root.injectPanel(); Qt.callLater(root.injectPanel) }
  }

  Ui.BarIconButton {
    id: bubble
    anchors.fill: parent
    bar: root.bar
    slotSize: Style.bar.statusSlot
    tooltipText: root.displayName + (Dm.State.ship ? " · " + Dm.State.unreadTotal + " unread" : " · sign in")
    iconComponent: Component {
      Item {
        Canvas {
          id: bubbleOutline
          width: Style.space(15)
          height: Style.space(15)
          anchors.centerIn: parent
          readonly property color ink: root.bar ? root.bar.barForeground : Commons.Color.foreground
          readonly property bool showTilde: Dm.State.unreadTotal === 0
          onInkChanged: requestPaint()
          onShowTildeChanged: requestPaint()
          onPaint: {
            var ctx = getContext("2d")
            ctx.clearRect(0, 0, width, height)
            ctx.strokeStyle = ink
            ctx.lineWidth = 1.4
            ctx.lineJoin = "round"
            ctx.lineCap = "round"
            ctx.beginPath()
            ctx.moveTo(3, 2)
            ctx.lineTo(12, 2)
            ctx.quadraticCurveTo(14, 2, 14, 4)
            ctx.lineTo(14, 9)
            ctx.quadraticCurveTo(14, 11, 12, 11)
            ctx.lineTo(6, 11)
            ctx.lineTo(2, 14)
            ctx.lineTo(2, 11)
            ctx.quadraticCurveTo(1, 10, 1, 9)
            ctx.lineTo(1, 4)
            ctx.quadraticCurveTo(1, 2, 3, 2)
            ctx.stroke()
            if (showTilde) {
              // Draw the mark at the optical center of the bubble body, not
              // at a font baseline (whose ~ glyph sits too high or too low).
              ctx.beginPath()
              ctx.moveTo(4.1, 6.7)
              ctx.bezierCurveTo(5.1, 5.5, 6.1, 5.5, 7.2, 6.5)
              ctx.bezierCurveTo(8.2, 7.4, 9.2, 7.4, 10.4, 6.2)
              ctx.stroke()
            }
          }
        }
        Text {
          visible: Dm.State.unreadTotal > 0
          anchors.centerIn: bubbleOutline
          anchors.verticalCenterOffset: -Style.space(1)
          text: Dm.State.unreadTotal < 10 ? String(Dm.State.unreadTotal) : "+"
          color: root.bar ? root.bar.barForeground : Commons.Color.foreground
          font.family: Style.font.family
          font.bold: Dm.State.unreadTotal > 0
          font.pixelSize: Style.font.caption
        }
      }
    }
    onPressed: function(button) {
      if (button === Qt.RightButton) Dm.State.refresh()
      else if (panelLoader.item) panelLoader.item.toggle()
    }
  }
}
