import QtQuick
QtObject {
  id: root
  objectName: "fixtureProcess"
  property var command: []
  property bool running: false
  property bool stdinEnabled: false
  property QtObject stdout: null
  property QtObject stderr: null
  property bool handedOff: false
  property bool closedAfterWrite: false
  property int launches: 0
  property bool stoppedBeforeExit: false
  property var expectedAccount: null
  property var palette: null
  property bool force: false
  signal started()
  signal exited(int exitCode, int exitStatus)
  onRunningChanged: if (running) {
    launches++
    Qt.callLater(function() { if (root && root.running) root.started() })
  }
  onStdinEnabledChanged: if (!stdinEnabled && handedOff) closedAfterWrite = true
  function write(data) {
    handedOff = data.length > 0
    expectedAccount = data ? JSON.parse(data).expectedAccount || null : null
    palette = data ? JSON.parse(data).palette || null : null
    force = data ? JSON.parse(data).force || false : false
  }
  function signal(number) { respond("", 1) }
  function respond(data, code) {
    if (stdout && data) stdout.read(data)
    if (stoppedBeforeExit) running = false
    exited(code, 0)
    running = false
  }
}
