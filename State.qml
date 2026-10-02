pragma Singleton
import QtQuick

QtObject {
  property string ship: ""
  property var rows: []
  property var contacts: ({})
  property var unreads: ({})
  property int unreadTotal: 0
  property bool showPatps: false
  property string authMessage: ""
  property var sendHandler: null
  property var refreshHandler: null
  property var sendResult: ({})
  property bool talonAvailable: false
  property var launchHandler: null
  property var launchResult: ({})
  property string launcherWarning: ""

  function reply(shipId, message) {
    if (!sendHandler) { sendResult = {ship: shipId, ok: false, error: "Urbit session unavailable"}; return }
    var error = sendHandler(shipId, message)
    if (error) sendResult = {ship: shipId, ok: false, error: error}
  }

  function refresh() { if (refreshHandler) refreshHandler() }
  function launch(action) {
    if (!launchHandler) { launchResult = {ok: false, error: "Urbit session unavailable"}; return }
    launchHandler(action)
  }
}
