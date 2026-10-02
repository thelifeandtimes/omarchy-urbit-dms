import QtQuick
import QtQuick.Layouts
import Quickshell
import qs.Commons
import qs.Ui as Ui
import "." as Dm
import "Time.js" as Time

Ui.Panel {
  id: root
  moduleName: "urbit.dms"
  manageIpc: false
  property var anchorItem: null
  property var hostWidget: null
  property string replyTo: ""
  property string feedback: ""
  property string feedbackPeer: ""
  property bool sending: false
  property string launchFeedback: ""
  property bool launching: false
  property double clockNow: Date.now()
  readonly property color dim: Qt.rgba(Color.popups.text.r, Color.popups.text.g, Color.popups.text.b, 0.72)

  Timer { interval: 60000; repeat: true; running: root.opened; onTriggered: root.clockNow = Date.now() }
  Timer { id: sentTimer; interval: 2500; onTriggered: { root.feedback = ""; root.feedbackPeer = "" } }

  function open() {
    root.controller.show()
    clockNow = Date.now()
    Dm.State.refresh()
    Dm.State.launch("apps")
    feedback = ""
    launchFeedback = ""
    launching = false
  }
  function close() {
    replyTo = ""
    feedback = ""
    feedbackPeer = ""
    sending = false
    launchFeedback = ""
    launching = false
    sentTimer.stop()
    root.controller.hide()
  }
  function toggle() { opened ? close() : open() }
  function launchInbox() {
    Quickshell.execDetached(["omarchy-shell", "shell", "summon", "urbit.dms", '{"mode":"inbox"}'])
    close()
  }
  function submitReply(peer, message) {
    if (!message.trim() || sending) return
    sentTimer.stop()
    feedbackPeer = peer
    feedback = "Sending…"
    sending = true
    Dm.State.reply(peer, message)
  }
  Connections {
    target: Dm.State
    function onLaunchResultChanged() {
      var result = Dm.State.launchResult
      if (!root.opened || !result) return
      root.launching = false
      if (result.ok) root.close()
      else root.launchFeedback = result.error || "Could not open app"
    }
    function onSendResultChanged() {
      var result = Dm.State.sendResult
      if (!result || result.ship !== root.feedbackPeer) return
      sending = false
      feedback = result.ok ? "Sent" : (result.error || "Send failed")
      if (result.ok) { replyTo = ""; sentTimer.restart() }
    }
  }

  Ui.KeyboardPanel {
    id: popup
    anchorItem: root.anchorItem
    owner: root.hostWidget || root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: popup.fittedContentWidth(Style.space(430))
    contentHeight: popup.fittedContentHeight(column.implicitHeight, Style.space(650))

    Ui.PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()

      Flickable {
        id: listScroll
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick

        Column {
          id: column
          width: parent.width
          spacing: Style.space(10)

          Row {
            id: heroRow
            width: parent.width
            height: Math.max(hero.implicitHeight, openInbox.implicitHeight)
            spacing: Style.space(8)
            Ui.PanelHero {
              id: hero
              width: parent.width - openInbox.width - parent.spacing
              height: parent.height
              title: "Urbit DMs"
              meta: Dm.State.ship ? Dm.State.ship + " · " + Dm.State.unreadTotal + " unread" : "Not signed in"
              foreground: Color.popups.text
              fontFamily: Style.font.family
              iconComponent: Component { Dm.ChatMark { foreground: Color.popups.text } }
            }
            Ui.PanelActionButton {
              id: openInbox
              anchors.verticalCenter: parent.verticalCenter
              size: Style.spacing.controlHeight
              iconText: "↗"
              foreground: Color.popups.text
              tooltipText: "Open full DM panel"
              bordered: true
              focusable: true
              onClicked: root.launchInbox()
            }
          }

          Ui.PanelSeparator { foreground: Color.popups.text }

          RowLayout {
            visible: Dm.State.ship !== ""
            width: parent.width
            Ui.PanelSectionHeader { Layout.fillWidth: true; text: "OPEN IN"; foreground: Color.popups.text }
            Ui.Button { text: "Tlon Web ↗"; foreground: Color.popups.text; bordered: true; focusable: true; enabled: !root.launching; tooltipText: "Open authenticated /apps/groups in a private browser window"; onClicked: { root.launching = true; root.launchFeedback = "Opening Tlon Web…"; Dm.State.launch("open-browser") } }
            Ui.Button { visible: Dm.State.talonAvailable; text: "Talon ↗"; foreground: Color.popups.text; bordered: true; focusable: true; enabled: !root.launching; tooltipText: "Open the installed Talon AppImage"; onClicked: { root.launching = true; root.launchFeedback = "Opening Talon…"; Dm.State.launch("launch-talon") } }
          }
          Text { visible: root.launchFeedback !== ""; width: parent.width; text: root.launchFeedback; color: root.dim; font.family: Style.font.family; font.pixelSize: Style.font.bodySmall; wrapMode: Text.Wrap; textFormat: Text.PlainText }
          Text { visible: Dm.State.ship !== "" && Dm.State.launcherWarning !== ""; width: parent.width; text: Dm.State.launcherWarning; color: Color.urgent; font.family: Style.font.family; font.pixelSize: Style.font.caption; wrapMode: Text.Wrap; textFormat: Text.PlainText }

          Ui.PanelSeparator { visible: Dm.State.ship !== ""; foreground: Color.popups.text }

          Text {
            visible: !Dm.State.ship && Dm.State.authMessage !== ""
            width: parent.width
            text: Dm.State.authMessage
            color: root.dim
            font.family: Style.font.family
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.Wrap
            textFormat: Text.PlainText
          }
          Ui.Button {
            visible: !Dm.State.ship
            text: "Sign In"
            foreground: Color.popups.text
            fontFamily: Style.font.family
            focusable: true
            bordered: true
            onClicked: root.launchInbox()
          }

          RowLayout {
            visible: Dm.State.ship !== ""
            width: parent.width
            Ui.PanelSectionHeader { Layout.fillWidth: true; text: "TOP CHATS"; foreground: Color.popups.text }
            Dm.NameModeSwitch { foreground: Color.popups.text }
          }

          Repeater {
            model: Dm.State.rows.slice(0, 5)
            delegate: Column {
              id: entry
              required property var modelData
              width: parent.width
              spacing: Style.space(6)
              readonly property string recipient: modelData.ship
              readonly property bool isOwn: modelData.latestKind !== "/chat/notice" && modelData.latestAuthor && modelData.latestAuthor === Dm.State.ship

              Ui.BorderSurface {
                width: parent.width
                height: row.implicitHeight + Style.space(10)
                radius: Style.cornerRadius
                color: root.replyTo === entry.recipient ? Style.selectedFillFor(Color.popups.text, Color.accent) : "transparent"
                borderSpec: Border.none()

                RowLayout {
                  id: row
                  anchors.left: parent.left
                  anchors.right: parent.right
                  anchors.verticalCenter: parent.verticalCenter
                  anchors.leftMargin: Style.space(6)
                  anchors.rightMargin: Style.space(6)
                  spacing: Style.space(8)
                  ColumnLayout {
                    Layout.fillWidth: true
                    spacing: Style.space(3)
                    RowLayout {
                      Layout.fillWidth: true
                       Dm.Identity {
                         Layout.fillWidth: true
                         Layout.minimumWidth: 0
                         shipId: entry.recipient
                         nickname: Dm.State.contacts[entry.recipient] || modelData.name || entry.recipient
                         showSecondary: root.replyTo === entry.recipient
                         primaryColor: Color.popups.text
                         primarySize: Style.font.subtitle
                       }
                       Text {
                        text: Time.compactDate(modelData.recency, root.clockNow)
                        color: root.dim
                        font.family: Style.font.family
                        font.pixelSize: Style.font.caption
                        textFormat: Text.PlainText
                      }
                    }
                    Text {
                      text: (entry.isOwn ? "me: " : "") + String(modelData.preview || "No messages yet").replace(/\s*\n\s*/g, " ")
                       color: entry.isOwn ? root.dim : Color.popups.text
                       font.family: "sans-serif"
                      font.pixelSize: Style.font.body
                      font.italic: entry.isOwn
                      elide: Text.ElideRight
                      Layout.fillWidth: true
                      textFormat: Text.PlainText
                    }
                  }
                  Ui.PanelActionButton {
                    iconText: "↩"
                    foreground: Color.popups.text
                    tooltipText: "Reply to " + recipient
                    focusable: true
                    onClicked: {
                      root.replyTo = entry.recipient
                      root.feedback = ""
                      root.feedbackPeer = ""
                      sentTimer.stop()
                      replyField.text = ""
                      Qt.callLater(function() {
                        replyField.forceActiveFocus()
                        listScroll.contentY = Math.min(Math.max(0, listScroll.contentHeight - listScroll.height),
                                                        Math.max(0, entry.y + entry.height - listScroll.height))
                      })
                    }
                  }
                }
              }

               Ui.TextField {
                id: replyField
                visible: root.replyTo === entry.recipient
                enabled: !root.sending
                width: parent.width - Style.space(12)
                anchors.horizontalCenter: parent.horizontalCenter
                foreground: Color.popups.text
                accent: Color.accent
                font.family: "sans-serif"
                placeholderText: "Reply to " + entry.recipient + " · Enter to send"
                onAccepted: root.submitReply(entry.recipient, text)
                Keys.onEscapePressed: root.close()
              }
              Text {
                visible: root.feedbackPeer === entry.recipient && root.feedback !== ""
                width: parent.width - Style.space(12)
                anchors.horizontalCenter: parent.horizontalCenter
                text: root.feedback
                color: root.feedback.indexOf("failed") !== -1 || root.feedback.indexOf("rejected") !== -1 ? Color.urgent : root.dim
                font.family: Style.font.family
                font.pixelSize: Style.font.caption
                textFormat: Text.PlainText
              }

              Ui.PanelSeparator { foreground: Color.popups.text }
            }
          }

          Text {
            visible: Dm.State.ship !== "" && Dm.State.rows.length === 0
            width: parent.width
            text: "No conversations yet. Open the inbox to start one."
            wrapMode: Text.Wrap
            color: Color.popups.text
            font.family: "sans-serif"
            font.pixelSize: Style.font.bodySmall
          }

        }
      }
    }
  }
}
