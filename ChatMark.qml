import QtQuick
import qs.Commons

Item {
  id: root
  property color foreground: Color.popups.text
  implicitWidth: Style.font.display
  implicitHeight: Style.font.display

  Canvas {
    id: mark
    anchors.fill: parent
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()
    onPaint: {
      var ctx = getContext("2d")
      ctx.clearRect(0, 0, width, height)
      ctx.save()
      ctx.scale(width / 16, height / 16)
      ctx.strokeStyle = root.foreground
      ctx.lineWidth = 1.25
      ctx.lineCap = "round"
      ctx.lineJoin = "round"
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
      ctx.beginPath()
      ctx.moveTo(4.1, 6.7)
      ctx.bezierCurveTo(5.1, 5.5, 6.1, 5.5, 7.2, 6.5)
      ctx.bezierCurveTo(8.2, 7.4, 9.2, 7.4, 10.4, 6.2)
      ctx.stroke()
      ctx.restore()
    }
    Connections { target: root; function onForegroundChanged() { mark.requestPaint() } }
  }
}
