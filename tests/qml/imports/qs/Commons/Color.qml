pragma Singleton
import QtQuick
QtObject {
  property color foreground: "#ddeeff"
  property color background: "#112233"
  property color accent: "#88aaff"
  property color urgent: "#ff8877"
  property var shellValues: ({})
  property var menu: ({background: "#112233", text: Qt.rgba(0.867,0.933,1,1), border: "#88aaff", scrim: "#88000000"})
  property var popups: ({background: "#112233", text: Qt.rgba(0.867,0.933,1,1), border: "#88aaff"})
  property var tooltip: ({ background: "#112233", text: Qt.rgba(0.867,0.933,1,1), border: "#88aaff" })
}
