// Heimdall: submit a security change. Pick a saved template, fill in what it asks for, and Fill
// opens Edge and stops before Submit. This is the "using a template" half; making templates is the
// Templates page. The two are kept apart on purpose (one makes, one uses), so neither clutters the
// other.
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AsgardUI

Item {
    id: page
    property var bridge: null
    property var st: ({})
    property string selected: ""
    property var detail: null
    property var output: []
    property string runState: ""        // "", "plan", "running", "done", "failed"

    readonly property bool ready: !!st.form && !st.templates_error
    readonly property var templates: (st.templates || []).filter(function (t) { return !t.problems.length })
    readonly property var asksList: detail && detail.variables ? detail.variables : []
    readonly property bool fillable: !!detail && detail.problems.length === 0

    // What the inputs hold: {NAME: text or bool}.
    function varValues() {
        var out = {}
        for (var i = 0; i < askRepeater.count; i++) {
            var item = askRepeater.itemAt(i)
            if (item)
                out[item.modelData.name] = item.value()
        }
        return out
    }

    function call(method, args) {
        if (!bridge)
            return null
        var r = bridge.call(method, args || [])
        if (r && r.error) {
            shell.toast(r.error, "error")
            return null
        }
        return r
    }
    function reload() {
        if (!bridge)
            return
        st = bridge.call("state", [])
        var names = templates.map(function (t) { return t.name })
        if (names.indexOf(selected) < 0)
            selected = names.length ? names[0] : ""
        loadDetail()
    }
    function loadDetail() { detail = selected ? call("template", [selected]) : null }
    function select(name) {
        selected = name
        output = []
        runState = ""
        loadDetail()
    }
    function dryRun() {
        var r = call("dry_run", [selected, varValues()])
        if (r) {
            output = r.lines
            runState = "plan"
        }
    }
    function fill() {
        output = ["Starting Edge. If it asks, pick your certificate and enter your PIN."]
        runState = "running"
        if (!bridge.spawn("fill_command", [selected, varValues()]))
            runState = ""
    }

    onBridgeChanged: reload()

    Connections {
        target: page.bridge
        ignoreUnknownSignals: true
        function onOutput(line) { page.output = page.output.concat([line.replace(/^heimdall: /i, "")]) }
        function onFinished(code) {
            page.runState = code === 0 ? "done" : "failed"
            if (code !== 0)
                shell.toast("The fill stopped early. The messages below say why.", "error")
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 28
        spacing: 16

        PageHeader {
            Layout.fillWidth: true
            title: "Submit a change"
            subtitle: "Pick a template, fill in what it asks for, then Fill opens Edge, fills the ServiceNow "
                      + "form and stops so you review it and click Submit yourself."
            AppButton {
                text: "Make templates"
                kind: "ghost"
                onClicked: navigation.go("heimdall:templates")
            }
        }

        // ---- the form file isn't set up, or won't load
        Card {
            visible: !!page.st.form_error
            Layout.fillWidth: true
            title: page.st.form_missing ? "Set up the form first" : "The form file needs fixing"
            subtitle: page.st.form_error || ""
            accentColor: page.st.form_missing ? theme.warning : theme.error
            AppButton {
                text: "Go to Form"
                kind: "primary"
                onClicked: navigation.go("heimdall:form")
            }
        }
        Card {
            visible: !!page.st.templates_error
            Layout.fillWidth: true
            title: "The templates file needs fixing"
            subtitle: page.st.templates_error || ""
            accentColor: theme.error
            AppButton {
                text: "Show templates file"
                onClicked: shell.openFolder(page.st.templates_path)
            }
        }

        RowLayout {
            visible: page.ready
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 16

            // ------------------------------------------------ pick a template
            Card {
                Layout.preferredWidth: 280
                Layout.fillHeight: true
                title: "Templates ready"
                subtitle: page.templates.length === 1 ? "1 ready to submit" : page.templates.length + " ready to submit"

                ListView {
                    id: list
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: 2
                    model: page.templates
                    Accessible.role: Accessible.List
                    Accessible.name: "Templates ready to submit"
                    delegate: NavItem {
                        required property var modelData
                        width: list.width
                        label: modelData.name
                        iconText: String(modelData.fields_set)
                        accentColor: theme.accentInk
                        selected: modelData.name === page.selected
                        Accessible.description: modelData.fields_set + " fields set"
                        onClicked: page.select(modelData.name)
                    }
                }
                EmptyState {
                    visible: page.templates.length === 0
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    title: "No templates ready"
                    detail: (page.st.templates || []).length
                            ? "The templates you have don't match the form yet. Fix them on the Templates page."
                            : "Make a template first, then submit it from here."
                    actionText: "Make templates"
                    onAction: navigation.go("heimdall:templates")
                }
            }

            // ------------------------------------------------ fill it in and submit
            Card {
                visible: !!page.detail
                Layout.fillWidth: true
                Layout.fillHeight: true
                title: page.detail ? page.detail.name : ""
                subtitle: page.detail ? (page.detail.description || "What this template fills into the form") : ""

                // The fixed values the template already carries, for reference.
                ListView {
                    id: rows
                    Layout.fillWidth: true
                    Layout.preferredHeight: Math.min(160, contentHeight)
                    clip: true
                    spacing: 6
                    model: page.detail ? page.detail.fields : []
                    delegate: RowLayout {
                        required property var modelData
                        width: rows.width
                        spacing: 12
                        Text {
                            text: parent.modelData.label + (parent.modelData.required ? " *" : "")
                            color: theme.textMuted
                            font.family: theme.fontFamily
                            font.pixelSize: theme.fontSize - 1
                            elide: Text.ElideRight
                            Layout.preferredWidth: 200
                        }
                        Text {
                            text: parent.modelData.display
                                  + (parent.modelData.source === "default" ? "   (form default)" : "")
                            color: parent.modelData.source === "none" ? theme.textDim : theme.text
                            font.family: theme.fontFamily
                            font.pixelSize: theme.fontSize
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }
                    }
                }

                // ---- what this template asks for each time it's used
                ColumnLayout {
                    visible: page.asksList.length > 0
                    Layout.fillWidth: true
                    spacing: 8

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 1
                        color: theme.borderSoft
                    }
                    Text {
                        text: "Fill in"
                        color: theme.text
                        font.family: theme.fontFamily
                        font.pixelSize: theme.fontSize
                        font.weight: Font.DemiBold
                    }

                    Repeater {
                        id: askRepeater
                        model: page.asksList
                        delegate: ColumnLayout {
                            id: ask
                            required property var modelData
                            readonly property string kind: modelData.kind
                            property var results: []
                            property int total: 0
                            property bool showing: false
                            Layout.fillWidth: true
                            spacing: 4

                            function value() {
                                if (kind === "checkbox")
                                    return check.checked
                                if (kind === "dropdown")
                                    return drop.currentIndex > 0 ? drop.currentText : ""
                                return entry.text.trim()
                            }
                            function search() {
                                var r = page.call("library_search", [modelData.library, entry.text, 8])
                                results = r ? r.entries : []
                                total = r ? r.total : 0
                                showing = true
                            }
                            function pick(name) {
                                entry.text = name
                                showing = false
                            }

                            Label {
                                visible: ask.kind !== "checkbox"
                                text: ask.modelData.label + (ask.modelData.required ? " *" : "")
                                font.weight: Font.DemiBold
                            }
                            TextField {
                                id: entry
                                objectName: "variable_" + ask.modelData.name
                                visible: ask.kind === "textbox" || ask.kind === "library"
                                Layout.fillWidth: true
                                placeholderText: ask.kind === "library"
                                                 ? "Search " + ask.modelData.library + " by name or description"
                                                 : ask.modelData.hint
                                Accessible.name: ask.modelData.label
                                Accessible.description: ask.kind === "library" ? "Type to search, then pick an entry" : ""
                                onTextEdited: if (ask.kind === "library") ask.search()
                                onActiveFocusChanged: if (activeFocus && ask.kind === "library" && !ask.showing) ask.search()
                                Keys.onEscapePressed: ask.showing = false
                            }
                            ComboBox {
                                id: drop
                                visible: ask.kind === "dropdown"
                                Layout.preferredWidth: 300
                                model: ["Choose..."].concat(ask.modelData.options)
                                Accessible.name: ask.modelData.label
                            }
                            CheckBox {
                                id: check
                                visible: ask.kind === "checkbox"
                                text: ask.modelData.label + (ask.modelData.description ? ": " + ask.modelData.description : "")
                                Accessible.name: ask.modelData.label
                            }

                            Repeater {
                                model: ask.kind === "library" && ask.showing ? ask.results : []
                                delegate: ItemDelegate {
                                    required property var modelData
                                    Layout.fillWidth: true
                                    text: modelData.name + (modelData.description ? "  -  " + modelData.description : "")
                                    font.family: theme.fontFamily
                                    font.pixelSize: theme.fontSize - 1
                                    Accessible.name: modelData.name
                                    onClicked: ask.pick(modelData.name)
                                }
                            }
                            Text {
                                visible: ask.kind === "library" && ask.showing
                                text: ask.total === 0 ? "Nothing matches. Searches look at names and descriptions."
                                      : (ask.total > ask.results.length
                                         ? ask.results.length + " of " + ask.total + " shown; type more to narrow it." : "")
                                color: theme.textMuted
                                font.family: theme.fontFamily
                                font.pixelSize: theme.fontSize - 1
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                            }
                        }
                    }
                }

                Text {
                    visible: !!page.detail
                    text: "Attachment: " + (page.detail && page.detail.attachment ? page.detail.attachment : "(none)")
                    color: theme.textMuted
                    font.family: theme.fontFamily
                    font.pixelSize: theme.fontSize - 1
                    elide: Text.ElideMiddle
                    Layout.fillWidth: true
                }

                RowLayout {
                    visible: !!page.detail
                    spacing: 8
                    Layout.fillWidth: true
                    AppButton {
                        text: "Fill in Edge"
                        kind: "primary"
                        enabled: !!page.bridge && !page.bridge.running && page.fillable
                        onClicked: page.fill()
                    }
                    AppButton {
                        text: "Dry run"
                        enabled: !!page.bridge && !page.bridge.running
                        onClicked: page.dryRun()
                    }
                    Item { Layout.fillWidth: true }
                    AppButton {
                        text: "Stop"
                        kind: "danger"
                        visible: !!page.bridge && page.bridge.running
                        onClicked: page.bridge.stop()
                    }
                    AppButton {
                        text: "Edit this template"
                        kind: "ghost"
                        enabled: !!page.bridge && !page.bridge.running
                        onClicked: navigation.go("heimdall:templates")
                    }
                }

                Rectangle {
                    visible: page.output.length > 0
                    Layout.fillWidth: true
                    Layout.preferredHeight: Math.min(220, outputText.implicitHeight + 20)
                    radius: theme.radiusSmall
                    color: theme.surfaceHigh
                    border.color: page.runState === "failed" ? theme.error : theme.borderSoft
                    border.width: 1

                    ScrollView {
                        anchors.fill: parent
                        anchors.margins: 10
                        TextArea {
                            id: outputText
                            objectName: "fillOutput"
                            text: page.output.join("\n")
                            readOnly: true
                            selectByMouse: true
                            wrapMode: TextArea.Wrap
                            color: theme.text
                            font.family: theme.monoFamily
                            font.pixelSize: theme.fontSize - 1
                            background: null
                            Accessible.name: page.runState === "plan" ? "Dry run" : "Fill progress"
                        }
                    }
                }
            }
        }
    }
}
