// Ysildir: which tools an AI client may use, and how to connect one. Every switch is the person's
// decision (and the ISSO's), so this page makes it; an agent can't (Kiro's guard hook blocks it).
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AsgardUI

ScrollPage {
    id: page
    property var bridge: null
    property var sdk: ({})
    property var rows: []
    property string path: ""
    property string problem: ""
    property bool writable: false
    property string error: ""
    property string checkOutput: ""

    function reload() {
        if (!bridge)
            return
        sdk = bridge.call("sdk", []) || {}
        var r = bridge.call("tools", [])
        if (r && r.error) {
            error = r.error
            rows = []
            return
        }
        error = ""
        rows = r.value || []
        path = r.path || ""
        problem = r.problem || ""
        writable = r.writable === true
    }

    function toggle(name, on) {
        var r = bridge.call("set_tool", [name, on])
        if (r && r.error)
            shell.toast(r.error, "error")
        else if (r && r.message)
            shell.toast(r.message, "info")
        reload()
    }

    onBridgeChanged: reload()

    PageHeader {
        Layout.fillWidth: true
        title: "AI tools"
        subtitle: "Ysildir is an MCP server: your AI client starts it and gets these tools. It never approves, "
                  + "changes or posts anything; it gives you the command instead."
        AppButton {
            text: "Reload"
            kind: "ghost"
            iconName: "refresh"
            onClicked: page.reload()
        }
    }

    Card {
        visible: page.error !== ""
        Layout.fillWidth: true
        title: "Ysildir couldn't be read"
        subtitle: page.error
        accentColor: theme.error
    }

    Card {
        Layout.fillWidth: true
        title: page.sdk.ready ? "The MCP SDK is installed" : "The MCP SDK isn't installed"
        subtitle: page.sdk.ready
                  ? "An AI client can start Ysildir with this Python."
                  : "Agents can still record estimates with baldur.cmd ai record and the clipboard review."
        accentColor: page.sdk.ready ? theme.success : theme.warning
        RowLayout {
            spacing: 10
            Layout.fillWidth: true
            Pill {
                text: page.sdk.ready ? "Ready" : "Missing: " + (page.sdk.missing || []).join(", ")
                tone: page.sdk.ready ? "success" : "warning"
            }
            Text {
                text: "Python " + (page.sdk.version || "?") + (page.sdk.needs ? " - needs " + page.sdk.needs : "")
                color: theme.textMuted
                font.family: theme.fontFamily
                font.pixelSize: theme.fontSize - 1
                elide: Text.ElideRight
                Layout.fillWidth: true
            }
        }
        Text {
            visible: !page.sdk.ready
            text: "Ask IT for the pinned mcp and pydantic wheels (Asgard's requirements.txt). The packaged build "
                  + "already has them."
            color: theme.textMuted
            font.family: theme.fontFamily
            font.pixelSize: theme.fontSize - 1
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
    }

    Card {
        visible: page.problem !== ""
        Layout.fillWidth: true
        title: "The switch file has a mistake in it"
        subtitle: page.problem
        accentColor: theme.warning
        Text {
            text: "Until it's fixed, only asgard_guide is on, so an agent can still be told what's wrong."
            color: theme.textMuted
            font.family: theme.fontFamily
            font.pixelSize: theme.fontSize - 1
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
    }

    Card {
        Layout.fillWidth: true
        title: "Tools"
        subtitle: page.writable
                  ? "What each tool sends to your AI client is what your ISSO approves. Restart the server in "
                    + "your client after a change."
                  : "Read-only on this Python: switching tools needs the SDK's pydantic. Use ysildir.cmd tools --on NAME."
        Repeater {
            model: page.rows
            delegate: Rectangle {
                required property var modelData
                Layout.fillWidth: true
                implicitHeight: row.implicitHeight + 24
                radius: theme.radiusMedium
                color: theme.surfaceHigh
                border.width: 1
                border.color: modelData.on ? Qt.alpha(theme.accent, 0.5) : "transparent"

                RowLayout {
                    id: row
                    anchors.fill: parent
                    anchors.margins: 12
                    spacing: 12
                    ColumnLayout {
                        spacing: 3
                        Layout.fillWidth: true
                        RowLayout {
                            spacing: 8
                            Text {
                                text: modelData.name
                                color: theme.text
                                font.family: theme.monoFamily
                                font.pixelSize: theme.fontSize
                                font.weight: Font.DemiBold
                            }
                            Pill {
                                visible: modelData.always_on
                                text: "always on"
                                tone: "accent"
                            }
                            Pill {
                                visible: !modelData.always_on && modelData.on !== modelData.default
                                text: modelData.on ? "on (changed)" : "off (changed)"
                            }
                        }
                        Text {
                            text: "Sends: " + modelData.sends
                            color: theme.textMuted
                            font.family: theme.fontFamily
                            font.pixelSize: theme.fontSize - 1
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }
                        Text {
                            visible: modelData.fallback !== "none" && modelData.fallback !== "none yet"
                            text: "Without it: " + modelData.fallback
                            color: theme.textDim
                            font.family: theme.monoFamily
                            font.pixelSize: theme.fontSize - 2
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }
                    }
                    Switch {
                        checked: modelData.on
                        enabled: page.writable && !modelData.always_on
                        Accessible.name: modelData.name
                        onToggled: page.toggle(modelData.name, checked)
                    }
                }
            }
        }
    }

    Card {
        Layout.fillWidth: true
        title: "Connect an AI client"
        subtitle: "Run one of these in the workspace folder you want it in, then restart the client."
        Repeater {
            model: page.bridge ? (page.bridge.call("clients", []).value || []) : []
            delegate: ColumnLayout {
                required property var modelData
                spacing: 2
                Layout.fillWidth: true
                Text {
                    text: modelData.name
                    color: theme.text
                    font.family: theme.fontFamily
                    font.pixelSize: theme.fontSize
                    font.weight: Font.DemiBold
                }
                TextEdit {
                    text: modelData.command
                    readOnly: true
                    selectByMouse: true
                    color: theme.textMuted
                    selectionColor: theme.accent
                    selectedTextColor: theme.accentText
                    font.family: theme.monoFamily
                    font.pixelSize: theme.fontSize - 1
                    wrapMode: TextEdit.Wrap
                    Layout.fillWidth: true
                    Accessible.name: modelData.name + " setup command"
                }
            }
        }
        AppButton {
            text: "What an agent would see"
            iconName: "play"
            onClicked: {
                var r = page.bridge.call("check", [])
                page.checkOutput = r.error ? r.error : (r.value || "")
            }
        }
        TextEdit {
            visible: page.checkOutput !== ""
            text: page.checkOutput
            readOnly: true
            selectByMouse: true
            color: theme.textMuted
            selectionColor: theme.accent
            selectedTextColor: theme.accentText
            font.family: theme.monoFamily
            font.pixelSize: theme.fontSize - 2
            wrapMode: TextEdit.Wrap
            Layout.fillWidth: true
            Accessible.name: "ysildir check output"
        }
    }

    Card {
        visible: page.path !== ""
        Layout.fillWidth: true
        title: "Where the switches live"
        AppButton {
            text: "Show the file"
            iconName: "folder"
            onClicked: shell.openFolder(page.path)
        }
        Text {
            text: page.path
            color: theme.textDim
            font.family: theme.monoFamily
            font.pixelSize: theme.fontSize - 2
            elide: Text.ElideMiddle
            Layout.fillWidth: true
        }
    }
}
