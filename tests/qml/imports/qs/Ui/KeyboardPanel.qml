import QtQuick
// Replace only the compositor window. The form still renders native Ui controls.
Item {
  id: root
  property Item anchorItem: null
  property var owner: null
  property var bar: null
  property bool open: false
  property Item focusTarget: null
  property int contentWidth: 440
  property int contentHeight: 720
  width: contentWidth
  height: contentHeight
  visible: open
  function fittedContentWidth(value) { return value }
  function fittedContentHeight(value, cap) { return Math.min(value, cap) }
  onOpenChanged: if (open && focusTarget) Qt.callLater(function() { root.focusTarget.forceActiveFocus() })
}
