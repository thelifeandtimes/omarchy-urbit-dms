pragma Singleton
import QtQuick
QtObject {
 property var lastCommand: []
 function env(name) { return "" }
 function execDetached(args) { lastCommand = args }
}
