// Baldur: one day's figures, and where you approve them. Approving is a decision, so it asks first and
// says what happens next. Baldur never writes to Jira; Odin posts what you approved.
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AsgardUI

ScrollPage {
    id: page
    property var bridge: null
    property var day: ({})
    property var tickets: []
    property var ai: null
    property string error: ""
    // The backend sends nothing for ai when there are none, which reaches QML as undefined.
    readonly property bool hasAi: ai ? true : false

    readonly property int estimated: {
        var total = 0
        for (var i = 0; i < tickets.length; i++)
            total += tickets[i].figure || 0
        return total
    }
    readonly property int approved: {
        var total = 0
        for (var i = 0; i < tickets.length; i++)
            if (tickets[i].approved !== null && tickets[i].approved !== undefined)
                total += tickets[i].approved
        return total
    }
    readonly property int held: {
        var total = 0
        for (var i = 0; i < tickets.length; i++)
            if (tickets[i].held !== null && tickets[i].held !== undefined)
                total += tickets[i].held
        return total
    }

    function text(minutes) {
        if (!minutes)
            return "0m"
        var h = Math.floor(minutes / 60)
        var m = minutes % 60
        return h > 0 ? (h + "h" + (m < 10 ? "0" : "") + m + "m") : (m + "m")
    }

    function reload() {
        if (!bridge)
            return
        var r = bridge.call("day", [""])        // "" means the day the week page chose, else today
        if (r && r.error) {
            error = r.error
            tickets = []
            return
        }
        error = ""
        day = r
        tickets = r.tickets || []
        ai = r.ai
    }

    function step(days) {
        var d = new Date(page.day.date + "T12:00:00")
        d.setDate(d.getDate() + days)
        bridge.call("choose_day", [Qt.formatDate(d, "yyyy-MM-dd")])
        reload()
    }

    function decide(method, args) {
        var r = bridge.call(method, args)
        if (r && r.error)
            shell.toast(r.error, "error")
        else
            shell.toast(r.message || "Done.", "info")
        reload()
    }

    onBridgeChanged: reload()

    PageHeader {
        Layout.fillWidth: true
        title: page.day.name || "One day"
        subtitle: "What Baldur estimates, what you approved, and what Jira already holds."
        AppButton {
            text: "Approve the day"
            kind: "primary"
            enabled: page.day.approvable === true
            onClicked: approveDialog.open()
        }
        AppButton {
            text: "Reject"
            enabled: page.day.approvable === true || page.day.has_approved === true
            onClicked: page.decide("reject", [page.day.date])
        }
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
        title: "Baldur couldn't read this day"
        subtitle: page.error
        accentColor: theme.error
    }

    RowLayout {
        visible: page.error === ""
        Layout.fillWidth: true
        spacing: 8
        AppButton { text: "Previous day"; kind: "ghost"; onClicked: page.step(-1) }
        AppButton { text: "Next day"; kind: "ghost"; onClicked: page.step(1) }
        Item { Layout.fillWidth: true }
        AppButton {
            text: "Copy for timesheet"
            iconName: "file"
            enabled: page.tickets.length > 0
            onClicked: {
                timesheet.selectAll()
                timesheet.copy()
                timesheet.deselect()
                shell.toast("The day is on the clipboard.", "info")
            }
        }
    }

    GridLayout {
        visible: page.error === "" && page.tickets.length > 0
        Layout.fillWidth: true
        columns: width > 800 ? 4 : 2
        columnSpacing: 12
        rowSpacing: 12
        MetricCard {
            Layout.fillWidth: true
            label: "Estimated"
            value: page.text(page.estimated)
            detail: page.tickets.length + " ticket(s)"
            interactive: false
        }
        MetricCard {
            Layout.fillWidth: true
            label: "You approved"
            value: page.text(page.approved)
            detail: page.day.approvable ? "Some still waiting" : "Nothing waiting"
            tone: page.day.approvable ? "warning" : ""
            interactive: false
        }
        MetricCard {
            Layout.fillWidth: true
            label: "Jira holds"
            value: page.text(page.held)
            detail: "What is logged there already"
            interactive: false
        }
        MetricCard {
            Layout.fillWidth: true
            label: "Untracked"
            value: page.day.untracked_text || "0m"
            detail: "No Jira key, so never logged"
            interactive: false
        }
    }

    EmptyState {
        visible: page.error === "" && page.tickets.length === 0
        Layout.fillWidth: true
        Layout.topMargin: 30
        title: "Nothing estimated for this day"
        detail: "Read git and Estimate on the week page, or step to another day."
    }

    Card {
        visible: page.tickets.length > 0
        Layout.fillWidth: true
        title: "Tickets"
        Repeater {
            model: page.tickets
            delegate: RowLayout {
                id: row
                required property var modelData
                Layout.fillWidth: true
                spacing: 10
                Text {
                    text: row.modelData.key
                    color: theme.text
                    font.family: theme.monoFamily
                    font.pixelSize: theme.fontSize
                    font.weight: Font.DemiBold
                    Layout.preferredWidth: 110
                }
                Text {
                    text: row.modelData.figure_text
                    color: theme.text
                    font.family: theme.monoFamily
                    font.pixelSize: theme.fontSize
                    horizontalAlignment: Text.AlignRight
                    Layout.preferredWidth: 60
                }
                Text {
                    text: row.modelData.held_text !== "" ? "Jira holds " + row.modelData.held_text : ""
                    color: theme.textDim
                    font.family: theme.fontFamily
                    font.pixelSize: theme.fontSize - 2
                    Layout.preferredWidth: 110
                }
                Text {
                    text: row.modelData.problem !== "" ? row.modelData.problem : row.modelData.review
                    color: row.modelData.problem !== "" ? theme.error : theme.textMuted
                    font.family: theme.fontFamily
                    font.pixelSize: theme.fontSize - 1
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }
                Pill {
                    visible: row.modelData.open
                    text: "to review"
                    tone: "warning"
                }
                Pill {
                    visible: !row.modelData.open && row.modelData.approved_text !== ""
                    text: "approved " + row.modelData.approved_text
                    tone: "success"
                }
            }
        }
    }

    Card {
        visible: page.hasAi
        Layout.fillWidth: true
        title: "AI-assisted figures"
        subtitle: page.hasAi ? page.ai.source : ""
        accentColor: theme.accent
        Text {
            text: page.hasAi ? page.ai.moves : ""
            color: theme.text
            font.family: theme.fontFamily
            font.pixelSize: theme.fontSize
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
        Repeater {
            model: page.hasAi ? page.ai.flags : []
            delegate: Text {
                required property string modelData
                text: "- " + modelData
                color: theme.textMuted
                font.family: theme.fontFamily
                font.pixelSize: theme.fontSize - 1
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
        }
        AppButton {
            visible: page.hasAi && page.ai.changed > 0 && page.day.approvable === true
            text: "Approve these instead"
            onClicked: page.decide("approve", [page.day.date, page.ai.digest])
        }
    }

    Card {
        visible: (page.day.flags || []).length > 0
        Layout.fillWidth: true
        title: "Worth a look"
        accentColor: theme.warning
        Repeater {
            model: page.day.flags || []
            delegate: Text {
                required property string modelData
                text: "- " + modelData
                color: theme.text
                font.family: theme.fontFamily
                font.pixelSize: theme.fontSize - 1
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
        }
    }

    Card {
        visible: page.day.report !== undefined && page.day.report !== ""
        Layout.fillWidth: true
        title: "The day report"
        subtitle: "Sessions, commits and how each ticket's figure was reached."
        TextEdit {
            text: page.day.report || ""
            readOnly: true
            selectByMouse: true
            color: theme.textMuted
            selectionColor: theme.accent
            selectedTextColor: theme.accentText
            font.family: theme.monoFamily
            font.pixelSize: theme.fontSize - 2
            wrapMode: TextEdit.NoWrap
            Layout.fillWidth: true
            Accessible.name: "Day report"
        }
    }

    // Off screen, only so Copy for timesheet has something to copy from.
    TextEdit {
        id: timesheet
        visible: false
        text: page.day.timesheet || ""
    }

    AppDialog {
        id: approveDialog
        title: "Approve " + (page.day.name || "this day") + "?"
        Text {
            text: "Approving records these figures as your decision. Baldur can't change a decision afterwards; "
                  + "it can only be superseded. Odin posts them to Jira on its next run, once for each day."
            color: theme.textMuted
            font.family: theme.fontFamily
            font.pixelSize: theme.fontSize
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
        buttons: [
            AppButton { text: "Cancel"; onClicked: approveDialog.close() },
            AppButton {
                text: "Approve"
                kind: "primary"
                onClicked: {
                    approveDialog.close()
                    page.decide("approve", [page.day.date, ""])
                }
            }
        ]
    }
}
