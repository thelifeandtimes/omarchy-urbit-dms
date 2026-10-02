import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import qs.Commons
import qs.Ui as Ui
import "." as Dm
import "Search.js" as Search
import "Time.js" as Time

Item {
  id: root
  property var shell: null
  property var manifest: null
  property bool opened: false
  property string mode: "inbox"
  property string ship: ""
  property string peer: ""
  property bool peerInvited: false
  property string olderCursor: ""
  property string status: ""
  property bool statusError: false
  property bool busy: false
  property bool signingIn: false
  property bool openingApp: false
  property var rows: []
  property var contacts: ({})
  property var searchResults: []
  property var quickMatches: []
  property string quickTarget: ""
  property int quickCursor: -1
  property var messages: []
  property bool scrollToBottomPending: false
  property bool loadingOlder: false
  property bool olderPageArrived: false
  property bool fillingViewport: false
  property real olderAnchorHeight: 0
  property real olderAnchorY: 0
  property int nextRequest: 0
  property int pendingSend: -1
  property string pendingOrigin: ""
  property string pendingPeer: ""
  property string pendingText: ""
  readonly property string scriptPath: Qt.resolvedUrl("bridge.py").toString().replace(/^file:\/\//, "")
  readonly property color foreground: Color.menu.text
  readonly property color background: Color.menu.background
  readonly property color accent: Color.accent
  readonly property color border: Color.menu.border
  readonly property color dim: Qt.rgba(foreground.r, foreground.g, foreground.b, 0.72)

  Component.onCompleted: {
    Dm.State.sendHandler = function(to, text) { return root.send(to, text, "bar") }
    Dm.State.refreshHandler = function() { root.command("refresh") }
    Dm.State.launchHandler = function(action) { root.command(action) }
  }
  Component.onDestruction: {
    Dm.State.sendHandler = null
    Dm.State.refreshHandler = null
    Dm.State.launchHandler = null
  }

  function command(action, fields) {
    var data = fields || ({})
    data.action = action
    data.request = ++nextRequest
    if (bridge.running) bridge.write(JSON.stringify(data) + "\n")
    else status = "Connection helper is not running"
    return data.request
  }

  function open(payloadJson) {
    var options = ({})
    try { options = JSON.parse(payloadJson || "{}") } catch (e) { }
    mode = options.mode === "quick" ? "quick" : "inbox"
    opened = true
    status = ""
    statusError = false
    command("apps")
    if (mode === "quick") {
      quickRecipient.text = ""
      quickText.text = ""
      quickTarget = ""
      quickMatches = []
      quickCursor = -1
      if (ship) command("contacts")
      Qt.callLater(function() { (ship ? quickRecipient : loginUrl).forceActiveFocus() })
    } else {
      command("refresh")
      rebuildResults()
      Qt.callLater(function() { (ship ? searchInput : loginUrl).forceActiveFocus() })
    }
  }

  function close() { opened = false }
  function dismiss() {
    opened = false
    if (shell && typeof shell.hide === "function") shell.hide("urbit.dms")
  }

  function openPeer(id) {
    peer = id
    peerInvited = rows.some(function(row) { return row.ship === id && row.invite === true })
    messages = []
    olderCursor = ""
    scrollToBottomPending = true
    loadingOlder = false
    fillingViewport = true
    status = "Loading messages…"
    command("open", {ship: id})
  }

  function send(to, text, quick) {
    if (busy) return "A message is already sending"
    if (!to.trim() || !text.trim()) { status = "Enter a recipient and a message"; return status }
    var target = to.trim()
    if (target.indexOf("~") !== 0) {
      var matches = Object.keys(contacts).filter(function(id) { return contacts[id].toLowerCase() === target.toLowerCase() })
      if (matches.length !== 1) {
        status = matches.length ? "Multiple contacts have that nickname; use an Urbit ID" : "Unknown nickname; use an Urbit ID"
        return status
      }
      target = matches[0]
    }
    busy = true
    pendingOrigin = quick === "bar" ? "bar" : (quick ? "quick" : "thread")
    pendingPeer = target
    pendingText = text
    pendingSend = command("send", {ship: target, text: text})
    status = "Sending…"
    return ""
  }

  function displayName(id) { return contacts[id] || id }
  function nameFont(id) { return displayName(id) === id ? Style.font.family : "sans-serif" }
  function quickCandidates() {
    var term = quickRecipient.text.trim()
    if (!term || quickTarget) return []
    return Search.recipients(term, rows, contacts)
  }
  function updateQuickMatches() {
    quickTarget = ""
    quickMatches = quickCandidates()
    quickCursor = -1
    if (quickSuggestions.contentItem) quickSuggestions.contentItem.contentY = 0
    if (quickRecipient.text.trim().length > 1) command("contacts")
  }
  function selectQuick(id) {
    quickRecipient.text = ""
    quickTarget = id
    quickMatches = []
    quickText.forceActiveFocus()
  }
  function quickAccept() {
    if (quickTarget) { quickText.forceActiveFocus(); return }
    if (quickMatches.length) selectQuick(quickMatches[quickCursor < 0 ? 0 : Math.min(quickCursor, quickMatches.length - 1)].ship)
  }
  function rebuildResults(resetScroll) {
    searchResults = Search.find(searchInput.text, rows, contacts)
    if (resetScroll && inboxScroll.contentItem) {
      inboxScroll.contentItem.contentY = 0
      Qt.callLater(function() { if (inboxScroll.contentItem) inboxScroll.contentItem.contentY = 0 })
    }
  }

  function threadFlick() { return threadScroll.contentItem }

  function scrollThreadBottom() {
    var flick = threadFlick()
    if (!flick || !scrollToBottomPending || (messages.length && flick.contentHeight <= 0)) return
    flick.contentY = Math.max(0, flick.contentHeight - flick.height)
    scrollToBottomPending = false
    if (fillingViewport && olderCursor && flick.contentHeight <= flick.height) loadOlder()
    else fillingViewport = false
  }

  function loadOlder() {
    var flick = threadFlick()
    if (!flick || !olderCursor || loadingOlder || !peer) return
    olderAnchorHeight = flick.contentHeight
    olderAnchorY = flick.contentY
    loadingOlder = true
    olderPageArrived = false
    command("open", {ship: peer, cursor: olderCursor})
  }

  function maybeLoadOlder() {
    var flick = threadFlick()
    if (!flick || scrollToBottomPending || loadingOlder || !olderCursor || !opened || !peer || mode !== "inbox") return
    if (flick.contentHeight > flick.height && flick.contentY <= Style.space(50)) loadOlder()
  }

  function settleThreadScroll() {
    var flick = threadFlick()
    if (!flick) return
    if (scrollToBottomPending) {
      Qt.callLater(function() { root.scrollThreadBottom() })
    } else if (loadingOlder && olderPageArrived) {
      Qt.callLater(function() {
        if (!root.loadingOlder || !root.olderPageArrived) return
        flick.contentY = Math.max(0, root.olderAnchorY + flick.contentHeight - root.olderAnchorHeight)
        root.loadingOlder = false
        root.olderPageArrived = false
      })
    }
  }

  function onLine(line) {
    var data
    try { data = JSON.parse(line) } catch (e) { return }
    if (data.event === "login") {
      ship = data.ship
      Dm.State.ship = ship
      Dm.State.authMessage = ""
      signingIn = false
      loginCode.text = ""
      status = (data.restored ? "Session restored · " : "Signed in as ") + ship
      statusError = false
      if (opened) Qt.callLater(function() { (mode === "quick" ? quickRecipient : searchInput).forceActiveFocus() })
    } else if (data.event === "logout") {
      ship = ""; peer = ""; rows = []; contacts = ({}); messages = []
      Dm.State.ship = ""; Dm.State.rows = []; Dm.State.contacts = ({}); Dm.State.unreads = ({}); Dm.State.unreadTotal = 0
      Dm.State.authMessage = ""
      Dm.State.launcherWarning = ""
      rebuildResults()
      status = "Signed out"
    } else if (data.event === "contacts") {
      contacts = data.contacts || ({})
      rows = rows.map(function(row) { return Object.assign({}, row, {name: root.displayName(row.ship)}) })
      Dm.State.contacts = contacts; Dm.State.rows = rows
      rebuildResults()
      if (mode === "quick" && quickRecipient.text.trim() && !quickTarget) {
        quickMatches = quickCandidates()
        quickCursor = Math.min(quickCursor, quickMatches.length - 1)
      }
    } else if (data.event === "inbox") {
      rows = data.rows || []
      Dm.State.rows = rows
      rebuildResults()
      if (mode === "quick" && quickRecipient.text.trim() && !quickTarget) quickMatches = quickCandidates()
      if (rows.length === 0 && !searchInput.text.trim()) status = "No DM conversations on this ship yet"
      else if (status === "No DM conversations on this ship yet") status = ""
    } else if (data.event === "unreads") {
      Dm.State.unreads = data.counts || ({})
      Dm.State.unreadTotal = Number(data.total || 0)
    } else if (data.event === "apps") {
      Dm.State.talonAvailable = !!data.talon
    } else if (data.event === "launcher") {
      Dm.State.launcherWarning = data.installed ? "" : (data.message || "Could not add Tlon Web to the launcher")
    } else if (data.event === "opened-app") {
      openingApp = false
      Dm.State.launchResult = {ok: true, name: data.name}
      status = "Opened " + data.name
      statusError = false
      if (opened) dismiss()
    } else if (data.event === "refreshed") {
      if (mode === "inbox" && peer === "") {
        status = "Up to date · " + data.count + " conversations"
        statusError = false
      }
    } else if (data.event === "history") {
      if (data.ship !== peer) return
      var list = data.messages || []
      if (data.append) {
        var known = ({})
        messages.forEach(function(msg) { known[msg.id] = true })
        messages = list.filter(function(msg) { return !known[msg.id] }).concat(messages)
        olderPageArrived = true
      } else {
        messages = list
        scrollToBottomPending = true
      }
      olderCursor = data.older === null ? "" : String(data.older || "")
      status = ""
      if (data.append && list.length === 0) olderCursor = ""
      if (data.append && fillingViewport) {
        loadingOlder = false
        olderPageArrived = false
        scrollToBottomPending = true
      }
      settleThreadScroll()
    } else if (data.event === "message") {
      var msg = data.message
      if (!msg) return
      if (data.ship === peer && msg && !messages.some(function(m) { return m.id === msg.id })) {
        messages = messages.concat([msg]).sort(function(a,b) { return a.sent - b.sent })
        scrollToBottomPending = true
        settleThreadScroll()
      }
      var updated = rows.filter(function(row) { return row.ship !== data.ship })
      updated.unshift({ship: data.ship, name: displayName(data.ship), recency: msg.sent,
                       preview: msg.text, latestAuthor: msg.author, latestKind: msg.kind || ""})
      rows = updated
      Dm.State.rows = rows
      rebuildResults()
      command("refresh")
    } else if (data.event === "accepted") {
      if (data.ship === peer) peerInvited = false
      status = "Invitation accepted"
    } else if (data.event === "sent") {
      if (data.request === pendingSend) {
        busy = false; pendingSend = -1; status = "Sent"
        if (pendingOrigin === "bar") Dm.State.sendResult = {ship: data.ship, ok: true}
        else if (pendingOrigin === "quick") dismiss()
        else if (peer === data.ship) {
          threadText.text = ""
          if (!messages.some(function(m) { return m.id === data.message.id }))
            messages = messages.concat([data.message])
          scrollToBottomPending = true
          settleThreadScroll()
        }
      }
    } else if (data.event === "notification") {
      if (!(opened && mode === "inbox" && peer === data.ship))
        Quickshell.execDetached(["notify-send", "Urbit DM · " + displayName(data.ship),
                                data.invite ? "New DM invitation" : "New direct message"])
      command("refresh")
    } else if (data.event === "error") {
      if (data.action === "open-browser" || data.action === "launch-talon") {
        openingApp = false
        Dm.State.launchResult = {ok: false, error: data.message || "Could not open app"}
      }
      if (data.action === "login") signingIn = false
      if (data.action === "restore") Dm.State.authMessage = data.message || "Saved Urbit session unavailable"
      if (data.action === "send" && data.request !== pendingSend) return
      if (data.action === "send") {
        if (pendingOrigin === "bar") Dm.State.sendResult = {ship: pendingPeer, ok: false, error: data.message}
        busy = false; pendingSend = -1
      }
      if (data.action === "open") { loadingOlder = false; olderPageArrived = false; fillingViewport = false; scrollToBottomPending = false }
      status = data.message || "Unable to connect to the ship"
      statusError = true
    }
  }

  Process {
    id: bridge
    command: ["python3", "-u", root.scriptPath]
    running: true
    stdinEnabled: true
    stdout: SplitParser { onRead: function(line) { root.onLine(line) } }
    onExited: function(code) { root.status = "Urbit connection helper stopped (" + code + ")" }
  }

  Shortcut { sequences: ["Escape"]; enabled: root.opened; onActivated: root.dismiss() }

  Connections {
    target: threadScroll.contentItem
    function onContentHeightChanged() { root.settleThreadScroll() }
    function onContentYChanged() { root.maybeLoadOlder() }
  }

  PanelWindow {
    id: window
    visible: root.opened
    anchors { top: true; bottom: true; left: true; right: true }
    color: "transparent"
    WlrLayershell.namespace: "urbit-dms"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.Exclusive
    exclusionMode: ExclusionMode.Ignore
    Keys.onEscapePressed: root.dismiss()

    Rectangle { anchors.fill: parent; color: Color.menu.scrim }
    MouseArea { anchors.fill: parent; onClicked: root.dismiss() }

    Ui.BorderSurface {
      id: card
      width: Math.min(!root.ship ? Style.space(440) : (root.mode === "quick" ? Style.space(470) : Style.space(700)), window.width - Style.space(32))
      height: Math.min(!root.ship
                       ? headerRow.implicitHeight + signInColumn.implicitHeight + Style.spacing.panelPadding * 2
                         + Style.space(42) + (statusLabel.visible ? statusLabel.implicitHeight + Style.space(12) : 0)
                       : (root.mode === "quick" ? quickColumn.implicitHeight + headerRow.implicitHeight
                           + Style.spacing.panelPadding * 2 + Style.space(28)
                           + (statusLabel.visible ? statusLabel.implicitHeight + Style.space(12) : 0)
                           : Style.space(630)), window.height - Style.space(32))
      anchors.centerIn: parent
      radius: Style.cornerRadius
      color: root.background
      borderSpec: Border.surfaceSpec("menu", "border", root.border, Math.max(1, Style.space(2)))
      MouseArea { anchors.fill: parent; onClicked: {} }

      ColumnLayout {
        anchors.fill: parent
        anchors.margins: Style.spacing.panelPadding
        spacing: Style.space(12)

        RowLayout {
          id: headerRow
          Layout.fillWidth: true
          Item {
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            Layout.preferredHeight: hero.implicitHeight
            Ui.PanelHero {
              id: hero
              anchors.fill: parent
              title: !root.ship ? "Sign In" : (root.mode === "quick" ? "Quick DM" : "Urbit DMs")
              meta: !root.ship ? "Urbit DMs" : (root.mode === "quick" ? root.ship : root.ship + " · " + Dm.State.unreadTotal + " unread")
              foreground: root.foreground
              fontFamily: Style.font.family
              iconComponent: Component { Dm.ChatMark { foreground: root.foreground } }
            }
          }
          Ui.PanelActionButton { visible: root.mode === "inbox" && root.peer !== ""; iconText: "←"; foreground: root.foreground; tooltipText: "Back to conversations"; focusable: true; onClicked: { root.peer = ""; root.command("refresh") } }
          Ui.PanelActionButton { iconText: "×"; foreground: root.foreground; tooltipText: "Close"; focusable: true; onClicked: root.dismiss() }
        }

        Ui.PanelSeparator { Layout.fillWidth: true; foreground: root.foreground }

        Text { id: statusLabel; visible: root.status !== ""; text: root.status; color: root.statusError ? Color.urgent : root.dim; wrapMode: Text.Wrap; Layout.fillWidth: true; font.family: Style.font.family; font.pixelSize: Style.font.bodySmall; textFormat: Text.PlainText }

        ColumnLayout {
          id: signInColumn
          visible: !root.ship
          Layout.fillWidth: true
          spacing: Style.space(8)
          Text { text: "Ship URL"; color: root.foreground; font.family: Style.font.family; font.pixelSize: Style.font.bodySmall; Layout.fillWidth: true }
          Ui.TextField { id: loginUrl; Layout.fillWidth: true; foreground: root.foreground; accent: root.accent; placeholderText: "https://your-ship.example"; maximumLength: 2048; inputMethodHints: Qt.ImhUrlCharactersOnly | Qt.ImhNoPredictiveText; Accessible.name: "Ship URL"; onAccepted: loginCode.forceActiveFocus(); Keys.onEscapePressed: root.dismiss() }
          Text { text: "+code"; color: root.foreground; font.family: Style.font.family; font.pixelSize: Style.font.bodySmall; Layout.fillWidth: true }
          Ui.TextField { id: loginCode; Layout.fillWidth: true; foreground: root.foreground; accent: root.accent; placeholderText: "Your ship's +code"; password: true; passwordMaskDelay: 0; maximumLength: 512; inputMethodHints: Qt.ImhHiddenText | Qt.ImhSensitiveData | Qt.ImhNoPredictiveText | Qt.ImhNoAutoUppercase; Accessible.name: "Ship +code"; onAccepted: if (signIn.enabled) signIn.clicked(); Keys.onEscapePressed: root.dismiss() }
          Ui.Button {
            id: signIn
            text: root.signingIn ? "Connecting…" : "Sign In"
            foreground: root.foreground
            focusable: true
            bordered: true
            Layout.alignment: Qt.AlignRight
            enabled: !root.signingIn && loginUrl.text.trim() !== "" && loginCode.text.trim() !== ""
            onClicked: {
              root.signingIn = true
              root.statusError = false
              root.status = loginUrl.text.trim().indexOf("http://") === 0 &&
                            !/^http:\/\/(localhost|127\.0\.0\.1|\[::1\])(:|\/|$)/.test(loginUrl.text.trim())
                            ? "Connecting over unencrypted HTTP…" : "Connecting…"
              root.command("login", {url: loginUrl.text, code: loginCode.text})
              if (!bridge.running) root.signingIn = false
            }
          }
          Text {
            text: "Session saved in Secret Service · +code is never stored"
            color: root.dim
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
            wrapMode: Text.Wrap
            Layout.fillWidth: true
            textFormat: Text.PlainText
          }
        }

        ColumnLayout {
          id: quickColumn
          visible: !!root.ship && root.mode === "quick"
          Layout.fillWidth: true
          spacing: Style.space(8)
          RowLayout {
            Layout.fillWidth: true
            Ui.PanelSectionHeader { Layout.fillWidth: true; text: "TO"; foreground: root.foreground }
            Dm.NameModeSwitch { foreground: root.foreground }
          }
          Dm.Identity {
            visible: root.quickTarget !== ""
            Layout.fillWidth: true
            shipId: root.quickTarget
            nickname: root.displayName(root.quickTarget)
            primaryColor: root.foreground
          }
          Ui.Button {
            visible: root.quickTarget !== ""
            text: "Change recipient"
            tooltipText: "Choose a different ~ship before sending"
            foreground: root.foreground
            fontSize: Style.font.bodySmall
            focusable: true
            Layout.alignment: Qt.AlignLeft
            onClicked: { root.quickTarget = ""; quickRecipient.forceActiveFocus() }
          }
          Ui.TextField {
            id: quickRecipient
            visible: root.quickTarget === ""
            Layout.fillWidth: true
            foreground: root.foreground
            accent: root.accent
            placeholderText: "Search nickname or ~ship"
            Accessible.name: "Find a DM recipient by nickname or Urbit ID"
            onTextChanged: root.updateQuickMatches()
            onAccepted: root.quickAccept()
            Keys.onDownPressed: {
              root.quickCursor = Math.min(root.quickMatches.length - 1, root.quickCursor + 1)
              quickSuggestions.contentItem.contentY = Math.max(0, (root.quickCursor - 3) * Style.space(46))
            }
            Keys.onUpPressed: {
              root.quickCursor = Math.max(0, root.quickCursor - 1)
              quickSuggestions.contentItem.contentY = Math.min(quickSuggestions.contentItem.contentY, root.quickCursor * Style.space(46))
            }
            Keys.onEscapePressed: root.dismiss()
          }
          ScrollView {
            id: quickSuggestions
            visible: root.quickTarget === "" && quickRecipient.text.trim() !== ""
            Layout.fillWidth: true
            Layout.preferredHeight: visible ? Math.min(Style.space(182), suggestions.implicitHeight) : 0
            clip: true
            Column {
              id: suggestions
              width: quickSuggestions.availableWidth
              spacing: Style.space(2)
              Repeater {
                model: root.quickMatches
                delegate: Ui.Button {
                  required property var modelData
                  required property int index
                  width: parent.width
                  height: Style.space(44)
                  foreground: root.foreground
                  selected: root.quickCursor === index
                  focusable: true
                  tooltipText: modelData.literal ? "Message this exact Urbit ID: " + modelData.ship : "Message " + modelData.ship
                  onClicked: root.selectQuick(modelData.ship)
                  Dm.Identity {
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.leftMargin: Style.spacing.controlPaddingX
                    anchors.rightMargin: Style.spacing.controlPaddingX
                    shipId: modelData.ship
                    nickname: modelData.literal ? "New DM · exact ID" : (root.contacts[modelData.ship] || modelData.name || modelData.ship)
                    primaryColor: root.foreground
                    primarySize: Style.font.bodySmall
                  }
                }
              }
              Text { visible: root.quickMatches.length === 0; text: "No matches · enter a ~ship to message someone new"; width: parent.width; wrapMode: Text.Wrap; color: root.dim; font.family: Style.font.family; font.pixelSize: Style.font.bodySmall; textFormat: Text.PlainText }
            }
          }
          Ui.PanelSeparator { Layout.fillWidth: true; foreground: root.foreground }
          Ui.PanelSectionHeader { Layout.fillWidth: true; text: "MESSAGE"; foreground: root.foreground }
          Ui.TextField {
            id: quickText
            Layout.fillWidth: true
            foreground: root.foreground
            accent: root.accent
            placeholderText: "Message (Enter to send)"
            Accessible.name: "Message to " + (root.quickTarget || "selected recipient")
            font.family: "sans-serif"
            enabled: !root.busy
            onAccepted: if (root.quickTarget) root.send(root.quickTarget, text, true)
            Keys.onEscapePressed: root.dismiss()
          }
          Ui.Button { text: root.busy ? "Sending…" : "Send DM"; foreground: root.foreground; bordered: true; focusable: true; Layout.alignment: Qt.AlignRight; enabled: !root.busy && root.quickTarget !== "" && quickText.text.trim() !== ""; tooltipText: root.quickTarget ? "Send to " + root.quickTarget : "Choose a recipient first"; onClicked: root.send(root.quickTarget, quickText.text, true) }
        }

        ColumnLayout {
          visible: !!root.ship && root.mode === "inbox" && root.peer === ""
          Layout.fillWidth: true
          Layout.fillHeight: true
          spacing: 6
          RowLayout {
            Layout.fillWidth: true
            Ui.TextField {
              id: searchInput
              Layout.fillWidth: true
              foreground: root.foreground
              accent: root.accent
              placeholderText: "Search nickname or ~urbit-id"
              Accessible.name: "Search conversations and contacts"
              onTextChanged: { root.rebuildResults(true); if (text.trim().length > 1) root.command("contacts") }
              onAccepted: {
                var found = root.searchResults
                if (found.length) root.openPeer(found[0].ship)
                else if (text.trim().indexOf("~") === 0) root.openPeer(text.trim())
              }
              Keys.onEscapePressed: root.dismiss()
            }
            Ui.PanelActionButton { iconText: "↻"; foreground: root.foreground; tooltipText: "Refresh"; focusable: true; onClicked: { root.status = "Refreshing…"; root.command("refresh") } }
          }
           RowLayout {
             Layout.fillWidth: true
             Ui.PanelSectionHeader { Layout.fillWidth: true; text: "CONVERSATIONS"; foreground: root.foreground }
             Dm.NameModeSwitch { foreground: root.foreground }
           }
          ScrollView {
            id: inboxScroll
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            Column {
              width: inboxScroll.availableWidth
               spacing: Style.space(1)
               Repeater {
                 model: root.searchResults
                 delegate: Ui.Button {
                   id: conversation
                   required property var modelData
                   width: parent.width
                   height: Math.max(Style.space(42), conversationContent.implicitHeight + Style.space(10))
                   foreground: root.foreground
                   leftAlign: true
                   focusable: true
                   tooltipText: "Open DM with " + modelData.ship
                   onClicked: root.openPeer(modelData.ship)
                   Column {
                     id: conversationContent
                     anchors.left: parent.left
                     anchors.right: parent.right
                     anchors.verticalCenter: parent.verticalCenter
                     anchors.leftMargin: Style.spacing.controlPaddingX
                     anchors.rightMargin: Style.spacing.controlPaddingX
                     spacing: Style.space(1)
                     Dm.Identity { width: parent.width; height: implicitHeight; shipId: modelData.ship; nickname: root.contacts[modelData.ship] || modelData.name || modelData.ship; primaryColor: root.foreground; primarySize: Style.font.bodySmall }
                     Text { text: (modelData.latestKind !== "/chat/notice" && modelData.latestAuthor === root.ship ? "me: " : "") + (modelData.preview || "No messages yet"); color: root.dim; font.family: "sans-serif"; font.pixelSize: Style.font.bodySmall; font.italic: modelData.latestKind !== "/chat/notice" && modelData.latestAuthor === root.ship; elide: Text.ElideRight; width: parent.width; textFormat: Text.PlainText }
                  }
                }
              }
              Text { visible: root.searchResults.length === 0; width: parent.width; text: searchInput.text.trim() ? "No matches · try part of a nickname or ~ship" : "No conversations yet"; color: root.dim; font.family: "sans-serif"; font.pixelSize: Style.font.body; wrapMode: Text.Wrap }
              Ui.Button {
                visible: searchInput.text.trim().indexOf("~") === 0 && !root.searchResults.some(function(row) { return row.ship === searchInput.text.trim() })
                width: parent.width
                foreground: root.foreground
                leftAlign: true
                text: "Message " + searchInput.text.trim()
                onClicked: root.openPeer(searchInput.text.trim())
              }
            }
           }
           Ui.PanelSeparator { Layout.fillWidth: true; foreground: root.foreground }
           RowLayout {
             Layout.fillWidth: true
             Ui.PanelSectionHeader { Layout.fillWidth: true; text: "OPEN IN"; foreground: root.foreground }
             Ui.Button { text: "Tlon Web ↗"; foreground: root.foreground; bordered: true; focusable: true; enabled: !root.openingApp; tooltipText: "Open authenticated /apps/groups in a private browser window"; onClicked: { root.openingApp = true; root.status = "Opening Tlon Web…"; root.command("open-browser") } }
             Ui.Button { visible: Dm.State.talonAvailable; text: "Talon ↗"; foreground: root.foreground; bordered: true; focusable: true; enabled: !root.openingApp; tooltipText: "Open the installed Talon AppImage"; onClicked: { root.openingApp = true; root.status = "Opening Talon…"; root.command("launch-talon") } }
           }
           Text { visible: Dm.State.launcherWarning !== ""; Layout.fillWidth: true; text: Dm.State.launcherWarning; color: Color.urgent; font.family: Style.font.family; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap; textFormat: Text.PlainText }
           RowLayout {
             Layout.fillWidth: true
             Text { text: root.ship; color: root.dim; font.family: Style.font.family; font.pixelSize: Style.font.bodySmall; Layout.fillWidth: true }
             Ui.Button { text: "Sign out"; foreground: root.foreground; tooltipText: "Forget this DM session for " + root.ship; focusable: true; onClicked: root.command("logout") }
          }
        }

        ColumnLayout {
          visible: !!root.ship && root.mode === "inbox" && root.peer !== ""
          Layout.fillWidth: true
          Layout.fillHeight: true
          spacing: 8
          RowLayout {
            Layout.fillWidth: true
            Dm.Identity { Layout.fillWidth: true; shipId: root.peer; nickname: root.displayName(root.peer); primaryColor: root.foreground; primarySize: Style.font.subtitle; secondarySize: Style.font.bodySmall }
            Dm.NameModeSwitch { foreground: root.foreground }
          }
          Ui.Button {
            visible: root.peerInvited
            text: "Accept DM invitation"
            foreground: root.foreground
            bordered: true
            focusable: true
            onClicked: root.command("accept", {ship: root.peer})
          }
          ScrollView {
            id: threadScroll
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
            Column {
              id: threadContent
              width: threadScroll.availableWidth
              spacing: Style.space(10)
              Text {
                visible: root.messages.length === 0 && root.status === ""
                width: parent.width
                text: "No messages yet. Say hello."
                color: root.dim
                font.family: "sans-serif"
                font.pixelSize: Style.font.body
                textFormat: Text.PlainText
              }
              Repeater {
                model: root.messages
                delegate: Item {
                  required property var modelData
                  width: parent.width
                  height: isNotice ? noticeCard.height + Style.space(6) : bubble.height
                  readonly property bool ownMessage: modelData.author === root.ship
                  readonly property bool isNotice: modelData.kind === "/chat/notice"
                  readonly property bool inviteAccepted: isNotice && !!modelData.acceptedShip

                  Ui.BorderSurface {
                    id: noticeCard
                    visible: isNotice
                    width: Math.min(parent.width - Style.space(32), Style.space(350))
                    height: noticeContents.implicitHeight + Style.space(18)
                    anchors.horizontalCenter: parent.horizontalCenter
                    radius: Style.cornerRadius
                    color: inviteAccepted ? Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.08)
                                          : Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.04)
                    borderSpec: inviteAccepted ? Border.flat(Util.alpha(root.accent, 0.4), 1) : Border.none()

                    Column {
                      id: noticeContents
                      anchors.left: parent.left
                      anchors.right: parent.right
                      anchors.verticalCenter: parent.verticalCenter
                      anchors.leftMargin: Style.space(12)
                      anchors.rightMargin: Style.space(12)
                      spacing: Style.space(4)
                      Text {
                        width: parent.width
                        text: inviteAccepted ? "✓  INVITATION ACCEPTED" : "CHAT EVENT"
                        color: inviteAccepted ? root.accent : root.dim
                        font.family: Style.font.family
                        font.pixelSize: Style.font.caption
                        font.bold: true
                        horizontalAlignment: Text.AlignHCenter
                        textFormat: Text.PlainText
                      }
                      Text {
                        width: parent.width
                        text: modelData.text || "Conversation updated"
                        color: root.foreground
                        font.family: Style.font.family
                        font.pixelSize: Style.font.bodySmall
                        horizontalAlignment: Text.AlignHCenter
                        wrapMode: Text.Wrap
                        textFormat: Text.PlainText
                      }
                      Text {
                        width: parent.width
                        text: Time.compactDate(modelData.sent)
                        color: root.dim
                        font.family: Style.font.family
                        font.pixelSize: Style.font.caption
                        horizontalAlignment: Text.AlignHCenter
                        textFormat: Text.PlainText
                      }
                    }
                  }

                  TextMetrics {
                    id: messageMetrics
                    font.family: "sans-serif"
                    font.pixelSize: Style.font.body
                    text: modelData.text || "[non-text message]"
                  }

                  Ui.BorderSurface {
                    id: bubble
                    visible: !isNotice
                    width: Math.min(threadContent.width - Style.space(16),
                                    Math.max(Style.space(180), Math.min(threadContent.width * 0.74, messageMetrics.width + Style.space(24))))
                    height: bubbleContents.implicitHeight + Style.space(20)
                    anchors.right: ownMessage ? parent.right : undefined
                    anchors.left: ownMessage ? undefined : parent.left
                    anchors.margins: Style.space(8)
                    radius: Style.cornerRadius
                    color: ownMessage ? Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.10)
                                      : Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.05)
                    borderSpec: ownMessage ? Border.flat(Util.alpha(root.accent, 0.35), 1) : Border.none()

                    Column {
                      id: bubbleContents
                      anchors.left: parent.left
                      anchors.right: parent.right
                      anchors.top: parent.top
                      anchors.margins: Style.space(10)
                      spacing: Style.space(4)
                      Text { visible: ownMessage; text: "me"; width: parent.width; color: root.accent; font.family: Style.font.family; font.bold: true; font.pixelSize: Style.font.bodySmall; textFormat: Text.PlainText }
                      Dm.Identity { visible: !ownMessage; width: parent.width; shipId: modelData.author || root.peer; nickname: root.displayName(modelData.author || root.peer); primaryColor: root.foreground; primarySize: Style.font.bodySmall; secondarySize: Style.font.caption }
                      Text {
                        id: messageText
                        width: parent.width
                        text: modelData.text || "[non-text message]"
                        color: root.foreground
                        font.family: "sans-serif"
                        font.pixelSize: Style.font.body
                        wrapMode: Text.WrapAnywhere
                        textFormat: Text.PlainText
                      }
                      Text {
                        text: Time.compactDate(modelData.sent)
                        color: root.dim
                        font.family: Style.font.family
                        font.pixelSize: Style.font.caption
                        horizontalAlignment: Text.AlignRight
                        width: parent.width
                        textFormat: Text.PlainText
                      }
                    }
                  }
                }
              }
            }
          }
          Ui.PanelSeparator { Layout.fillWidth: true; foreground: root.foreground }
          RowLayout {
            Layout.fillWidth: true
            Ui.TextField {
              id: threadText
              Layout.fillWidth: true
              foreground: root.foreground
              accent: root.accent
              placeholderText: "Write a message…"
              font.family: "sans-serif"
              enabled: !root.busy && !root.peerInvited
              onAccepted: root.send(root.peer, text, false)
              Keys.onEscapePressed: root.dismiss()
            }
            Ui.Button { text: "Send"; foreground: root.foreground; bordered: true; focusable: true; enabled: !root.busy && !root.peerInvited; onClicked: root.send(root.peer, threadText.text, false) }
          }
        }
      }
    }
  }
}
