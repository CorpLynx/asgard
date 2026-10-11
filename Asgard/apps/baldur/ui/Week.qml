// Baldur: a week of estimated development time, and the reviews waiting on you. Reading only, except
// Read git and Estimate, which both go off the window's thread. Approving is on the day page, where you
// can see what you would be approving.
//
// GitHub is checked every poll_minutes while this page is open, off the window's thread, and quietly:
// the page says how fresh the list is, and GitHub being out of reach never stops anything else.
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AsgardUI

ScrollPage {
    id: page
    property var bridge: null
    property var week: ({})
    property var days: []
    property var reviews: []
    property string error: ""
    property string monday: ""
    property string pending: ""
    readonly property bool busy: pending !== ""
    property string githubPending: ""
    property var github: ({})          // the last sync_github reply: state, at, message

    readonly property int toReview: {
        var n = 0
        for (var i = 0; i < days.length; i++)
            if ((days[i].review || "").indexOf("to review") >= 0)
                n++
        return n
    }
    readonly property int approvedDays: {
        var n = 0
        for (var i = 0; i < days.length; i++)
            if ((days[i].review || "").indexOf("approved") >= 0)
                n++
        return n
    }

    function reload() {
        if (!bridge)
            return
        var r = bridge.call("week", [monday])
        if (r && r.error) {
            error = r.error
            days = []
            return
        }
        error = ""
        week = r
        days = r.days || []
        monday = r.monday
        var asked = bridge.call("reviews", [])
        reviews = asked.error ? [] : (asked.value || [])
    }

    function run(method, args) {
        pending = method + ":" + Date.now()
        bridge.callAsync(pending, method, args)
    }

    function step(weeks) {
        if (busy || (weeks > 0 && week.is_this_week))
            return
        monday = weeks < 0 ? week.previous : week.next
        reload()
    }

    function thisWeek() {
        monday = ""
        reload()
    }

    function checkGitHub() {
        if (!bridge || githubPending !== "")
            return
        githubPending = "github:" + Date.now()
        bridge.callAsync(githubPending, "sync_github", [])
    }

    readonly property string githubText: {
        if (page.week.github !== true)
            return "GitHub is off"
        if (page.githubPending !== "" && !page.github.at)
            return "Checking GitHub..."
        if (!page.github.state)
            return "Pull requests, from GitHub"
        if (page.github.state === "fresh")
            return "Checked at " + page.github.at
                   + (page.github.problems && page.github.problems.length
                      ? ", " + page.github.problems.length + " repo(s) unreadable" : "")
        return page.github.message
    }

    function openDay(date) {
        bridge.call("choose_day", [date])
        navigation.go("baldur:day")
    }

    onBridgeChanged: reload()

    Connections {
        target: page.bridge
        ignoreUnknownSignals: true
        function onAsyncDone(token, reply) {
            if (token === page.githubPending) {
                // Quiet on purpose: a failure shows beside the list, never as a toast.
                page.githubPending = ""
                page.github = reply.error ? { "state": "failed", "message": reply.error } : reply
                var asked = page.bridge.call("reviews", [])
                page.reviews = asked.error ? page.reviews : (asked.value || [])
                return
            }
            if (token !== page.pending)
                return
            page.pending = ""
            if (reply.error)
                shell.toast(reply.error, "error")
            else {
                shell.toast(reply.message || "Done.", "info")
                for (var i = 0; i < (reply.problems || []).length; i++)
                    shell.toast(reply.problems[i], "warning")
            }
            page.reload()
        }
    }

    // Every poll_minutes while the page is open; it goes when the page does, so nothing runs behind it.
    Timer {
        id: githubTimer
        interval: Math.max(1, page.github.poll_minutes || page.week.poll_minutes || 5) * 60000
        repeat: true
        triggeredOnStart: true
        running: page.bridge !== null && page.week.github === true && page.visible
        onTriggered: page.checkGitHub()
    }

    Shortcut {
        sequences: ["Ctrl+Left"]
        onActivated: page.step(-1)
    }
    Shortcut {
        sequences: ["Ctrl+Right"]
        onActivated: page.step(1)
    }

    PageHeader {
        Layout.fillWidth: true
        title: page.week.is_this_week ? "This week" : "Week of " + page.monday
        subtitle: "Baldur reads your git history and estimates the time behind it. It never writes to a "
                  + "repository, and never to Jira: Odin posts the days you approve."
        AppButton {
            text: "Read git"
            iconName: "refresh"
            enabled: !page.busy
            onClicked: page.run("collect", [])
        }
        AppButton {
            text: "Estimate"
            kind: "primary"
            enabled: !page.busy
            onClicked: page.run("estimate", [page.monday, ""])
        }
        AppButton {
            text: "Reload"
            kind: "ghost"
            enabled: !page.busy
            onClicked: page.reload()
        }
    }

    Card {
        visible: page.error !== ""
        Layout.fillWidth: true
        title: "Baldur couldn't read this"
        subtitle: page.error
        accentColor: theme.error
    }

    GridLayout {
        visible: page.error === ""
        Layout.fillWidth: true
        columns: width > 800 ? 4 : 2
        columnSpacing: 12
        rowSpacing: 12
        MetricCard {
            Layout.fillWidth: true
            label: "Development time"
            value: page.week.total_text || "0m"
            detail: "Meetings counted as '" + (page.week.policy || "ambient") + "'"
            interactive: false
        }
        MetricCard {
            Layout.fillWidth: true
            label: "Days to review"
            value: String(page.toReview)
            detail: page.toReview > 0 ? "Open a day to approve it" : "Nothing waiting"
            tone: page.toReview > 0 ? "warning" : ""
            interactive: false
        }
        MetricCard {
            Layout.fillWidth: true
            label: "Days approved"
            value: String(page.approvedDays)
            detail: "Odin posts these"
            interactive: false
        }
        MetricCard {
            Layout.fillWidth: true
            label: "Reviews asked of you"
            value: String(page.reviews.filter(function (r) { return r.kind === "requested" }).length)
            detail: page.githubText
            interactive: false
        }
    }

    RowLayout {
        visible: page.error === ""
        Layout.fillWidth: true
        spacing: 8
        AppButton {
            text: "Previous week"
            kind: "ghost"
            enabled: !page.busy
            onClicked: page.step(-1)
            Accessible.description: "Ctrl+Left"
        }
        AppButton {
            text: "This week"
            kind: "ghost"
            visible: page.week.is_this_week === false
            enabled: !page.busy
            onClicked: page.thisWeek()
        }
        AppButton {
            text: "Next week"
            kind: "ghost"
            enabled: !page.busy && !page.week.is_this_week
            onClicked: page.step(1)
            Accessible.description: "Ctrl+Right"
        }
        Item { Layout.fillWidth: true }
        Pill {
            visible: page.busy
            text: "working"
            tone: "accent"
            dot: true
        }
    }

    EmptyState {
        visible: page.error === "" && page.days.length === 0
        Layout.fillWidth: true
        Layout.topMargin: 30
        title: "Nothing estimated yet"
        detail: "Read git, then Estimate. Baldur needs your git email and the Jira projects that count, "
                + "which are in Settings."
    }

    Card {
        visible: page.days.length > 0
        Layout.fillWidth: true
        title: "Days"
        subtitle: "Open a day to see its report, and to approve or reject it."
        Repeater {
            model: page.days
            delegate: RowLayout {
                id: row
                required property var modelData
                Layout.fillWidth: true
                spacing: 10
                AppButton {
                    text: row.modelData.name
                    kind: "ghost"
                    implicitWidth: 128
                    onClicked: page.openDay(row.modelData.date)
                    Accessible.name: "Open " + row.modelData.name
                }
                Text {
                    text: row.modelData.dev_text
                    color: row.modelData.dev ? theme.text : theme.textDim
                    font.family: theme.monoFamily
                    font.pixelSize: theme.fontSize
                    horizontalAlignment: Text.AlignRight
                    Layout.preferredWidth: 60
                }
                Text {
                    text: row.modelData.tickets
                    color: row.modelData.has_work ? theme.textMuted : theme.textDim
                    font.family: theme.fontFamily
                    font.pixelSize: theme.fontSize - 1
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }
                Pill {
                    visible: row.modelData.today
                    text: "today"
                    tone: "accent"
                }
                Pill {
                    visible: row.modelData.review !== ""
                    text: row.modelData.review
                    tone: row.modelData.review.indexOf("to review") >= 0 ? "warning" : "success"
                }
            }
        }
    }

    Card {
        visible: page.reviews.length > 0
        Layout.fillWidth: true
        title: "Pull requests"
        subtitle: "Reviews asked of you, then what people said about yours in the last week."
        Repeater {
            model: page.reviews
            delegate: RowLayout {
                id: pr
                required property var modelData
                Layout.fillWidth: true
                spacing: 10
                AppButton {
                    text: pr.modelData.label
                    kind: "ghost"
                    enabled: pr.modelData.url !== ""
                    onClicked: Qt.openUrlExternally(pr.modelData.url)
                    Accessible.name: "Open " + pr.modelData.label + " on GitHub"
                }
                Text {
                    text: pr.modelData.title
                    color: theme.text
                    font.family: theme.fontFamily
                    font.pixelSize: theme.fontSize
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }
                Pill {
                    visible: pr.modelData.draft
                    text: "draft"
                }
                Pill {
                    text: pr.modelData.kind === "requested" ? "asked of you"
                                                            : pr.modelData.state.replace("_", " ")
                    tone: pr.modelData.kind === "requested" ? "warning"
                          : (pr.modelData.state === "approved" ? "success" : "accent")
                }
                Text {
                    text: pr.modelData.who
                    color: theme.textMuted
                    font.family: theme.fontFamily
                    font.pixelSize: theme.fontSize - 1
                    elide: Text.ElideRight
                    Layout.preferredWidth: 130
                }
            }
        }
    }
}
